import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import threading
import time
from typing import Callable, List, Dict, Optional
from sqlmodel import Session, select
from mio_taskhub.db import engine
from mio_taskhub.api.claim import claim_for as _claim_for
from mio_taskhub.models import Agent, AgentStatus, Run, RunState, Task, TaskStage, TaskState
from mio_taskhub.workflow.state_machine import is_terminal
from mio_taskhub.dependency import dependency_satisfied, task_deps
from mio_taskhub.events import emit_event, broadcast_for_event
from mio_taskhub.workflow.transitions import apply_transition, _orm_to_status_stage
from mio_taskhub.workflow.state_machine import State as M1State, Stage as M1Stage, ActorType as M1Actor
from mio_taskhub.ideas.idea_review import IdeaReviewScanner

logger = logging.getLogger("mio_taskhub.background")

DEFAULT_TIMEOUT_SECONDS = 120
AGENT_TIMEOUT_SECONDS = 180

# ---------- RunInfo ----------
@dataclass
class RunInfo:
    run_id: str
    task_id: str
    agent_name: str
    state: RunState
    last_heartbeat: float
    attempt: int
    max_retries: int
    timeout_seconds: int = 120
    agent_offline: bool = False


# ---------- Background Thread Heartbeat ----------

_thread_heartbeats: dict[str, dict] = {}
_thread_heartbeats_lock = threading.Lock()

STALL_THRESHOLD_SECONDS = 300  # 5 minutes

def thread_heartbeat(name: str, status: str = "running", metadata: dict | None = None):
    """Record a heartbeat for a background thread."""
    with _thread_heartbeats_lock:
        _thread_heartbeats[name] = {
            "last_heartbeat": time.time(),
            "status": status,
            "metadata": metadata or {},
            "consecutive_failures": 0,
            "last_failure": None,
        }

def thread_failure(name: str, error: str | None = None):
    """Record a failure for a background thread."""
    with _thread_heartbeats_lock:
        if name in _thread_heartbeats:
            _thread_heartbeats[name]["consecutive_failures"] += 1
            _thread_heartbeats[name]["last_failure"] = {
                "time": time.time(),
                "error": error,
            }

def get_thread_health() -> dict:
    """Get health status of all registered threads."""
    now = time.time()
    with _thread_heartbeats_lock:
        result = {}
        for name, data in _thread_heartbeats.items():
            last_hb = data.get("last_heartbeat", 0)
            age = now - last_hb if last_hb else float("inf")
            result[name] = {
                "status": data.get("status", "unknown"),
                "last_heartbeat": last_hb,
                "age_seconds": age,
                "consecutive_failures": data.get("consecutive_failures", 0),
                "last_failure": data.get("last_failure"),
                "alive": age < 60,  # consider dead if no heartbeat for 60s
            }
        return result


class BackgroundWorker:
    """Base class for background workers with heartbeat support."""

    def __init__(self, name: str, poll_interval: float = 10.0):
        self.name = name
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=self.name)
        self._thread.start()
        logger.info("%s started", self.name)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("%s stopped", self.name)

    def _loop(self):
        while not self._stop.wait(self.poll_interval):
            try:
                self.tick()
                thread_heartbeat(self.name, "running")
            except Exception as e:
                logger.exception("%s tick failed", self.name)
                thread_failure(self.name, str(e))

    def tick(self):
        """Override in subclass."""
        pass


def _get_runs():
    with Session(engine) as db:
        runs = db.exec(select(Run).where(Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))).all()
        out = []
        for run in runs:
            task = db.get(Task, run.task_id)
            agent = db.get(Agent, run.agent_name)
            timeout_sec = (task.timeout_min * 60) if task and task.timeout_min else DEFAULT_TIMEOUT_SECONDS
            last_hb = run.last_heartbeat or run.started_at
            if last_hb.tzinfo is None:
                last_hb = last_hb.replace(tzinfo=timezone.utc)
            out.append(RunInfo(
                run_id=run.id, task_id=run.task_id, agent_name=run.agent_name,
                state=run.state, last_heartbeat=last_hb.timestamp(),
                attempt=run.attempt, max_retries=task.max_retries if task else 3,
                timeout_seconds=timeout_sec,
                agent_offline=agent is None or agent.status == AgentStatus.OFFLINE,
            ))
        return out


BASE_RETRY_SECONDS = 2.0


def _backoff_for(task) -> timedelta:
    try:
        secs = (2 ** max(1, task.attempt)) * BASE_RETRY_SECONDS
    except Exception:
        secs = BASE_RETRY_SECONDS
    return timedelta(seconds=float(secs))


def _handle_retry_or_fail(task, reason: str, db):
    """指数退避：未超限进入 RETRYING 并设定 retry_at，超限进入 FAILED。通过 apply_transition 记录。"""
    from_st = _orm_to_status_stage(task.stage)
    if task.max_retries == 0 or task.attempt >= task.max_retries:
        apply_transition(task, M1State.FAILED, from_st,
                         M1Actor.SYSTEM, "scheduler:retry_or_fail",
                         reason=reason or "max_retries_exceeded")
        task.retry_at = None
        return "failed"
    # 进入重试，设定退避
    apply_transition(task, M1State.RETRYING, from_st,
                     M1Actor.SYSTEM, "scheduler:retry_or_fail",
                     reason=reason or "retry_backoff")
    task.retry_count = (task.retry_count or 0) + 1
    task.retry_at = datetime.now(timezone.utc) + _backoff_for(task)
    return "retrying"


def _requeue_retries():
    """把已到 retry_at 的 RETRYING 任务重入 QUEUED/READY。"""
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        retrying = db.exec(select(Task).where(Task.state == TaskState.RETRYING)).all()
        for t in retrying:
            rt = t.retry_at
            if rt is None:
                # 兼容旧数据：无 retry_at 直接重入
                # M1: T9 retry_requeue
                from_st = _orm_to_status_stage(t.stage)
                apply_transition(t, M1State.QUEUED, M1Stage.READY,
                                 M1Actor.SYSTEM, "scheduler:requeue_retries",
                                 reason="retry_backoff_elapsed")
                t.retry_at = None
                event = emit_event(db, type="task_retry_requeued", entity="task", entity_id=t.id,
                                   payload={"reason": "retry_backoff_elapsed"})
                db.add(t)
                db.commit()
                broadcast_for_event(event)
                continue
            if rt.tzinfo is None:
                rt = rt.replace(tzinfo=timezone.utc)
            if rt <= now:
                # M1: T9 retry_requeue
                from_st = _orm_to_status_stage(t.stage)
                apply_transition(t, M1State.QUEUED, M1Stage.READY,
                                 M1Actor.SYSTEM, "scheduler:requeue_retries",
                                 reason="retry_backoff_elapsed")
                t.retry_at = None
                event = emit_event(db, type="task_retry_requeued", entity="task", entity_id=t.id,
                                   payload={"reason": "retry_backoff_elapsed", "attempt": t.attempt})
                db.add(t)
                db.commit()
                broadcast_for_event(event)


def _on_timeout(run_id: str, task_id: str):
    # 心跳超时仍保持原有“立即重入队列”语义，与显式失败的指数退避区分，
    # 以保持 65882419 已有用例稳定；显式失败的退避由 runs.py 的 _requeue_retries 负责
    with Session(engine) as db:
        run = db.get(Run, run_id)
        task = db.get(Task, task_id)
        if run and run.state in (RunState.CLAIMED, RunState.RUNNING):
            agent = db.get(Agent, run.agent_name)
            agent_offline = agent is None or agent.status == AgentStatus.OFFLINE
            if agent_offline:
                run.state = RunState.FINISHED
                run.result = "agent offline"
                run.finished_at = datetime.now(timezone.utc)
                run.exit_code = 1
                db.add(run)
                if task:
                    from_st = _orm_to_status_stage(task.stage)
                    if task.attempt >= task.max_retries:
                        apply_transition(task, M1State.FAILED, from_st,
                                         M1Actor.SYSTEM, "scheduler:timeout",
                                         reason="agent_offline:max_retries_exceeded")
                    else:
                        apply_transition(task, M1State.QUEUED, M1Stage.READY,
                                         M1Actor.SYSTEM, "scheduler:timeout",
                                         reason="agent_offline:requeue")
                    db.add(task)
                db.commit()
                return
        if run and run.state in (RunState.CLAIMED, RunState.RUNNING):
            run.state = RunState.FINISHED
            run.result = "heartbeat timeout"
            run.finished_at = datetime.now(timezone.utc)
            run.exit_code = 1
            db.add(run)
            if task:
                from_st = _orm_to_status_stage(task.stage)
                if task.attempt >= task.max_retries:
                    apply_transition(task, M1State.FAILED, from_st,
                                     M1Actor.SYSTEM, "scheduler:timeout",
                                     reason="heartbeat_timeout:max_retries_exceeded")
                else:
                    apply_transition(task, M1State.QUEUED, M1Stage.READY,
                                     M1Actor.SYSTEM, "scheduler:timeout",
                                     reason="heartbeat_timeout:requeue")
                db.add(task)
        db.commit()


def _on_alive(run_id: str):
    pass


def _release_dependencies():
    """调度器 tick：把依赖全部满足的任务自动放行到 READY。

    放行范围：state 非终态、stage ∈ {brainstorming, design, planning}、depends_on 非空。
    前置全部 dependency_satisfied → stage=READY + 事件 + 广播。
    前置存在 cancelled/failed（不可放行）→ 不动（告警由 board.summary 生成）。
    """
    with Session(engine) as db:
        tasks = db.exec(select(Task)).all()
        for t in tasks:
            deps = task_deps(t)
            if not deps:
                continue
            stage_v = t.stage.value if not isinstance(t.stage, str) else t.stage
            if is_terminal(t) or stage_v not in ("brainstorming", "design", "planning"):
                continue
            prereqs = [db.get(Task, d) for d in deps if d]
            if prereqs and all(p is not None and dependency_satisfied(p) for p in prereqs):
                # M1: T17 manual_advance (depsatisfied → ready)
                from_st = _orm_to_status_stage(t.stage)
                apply_transition(t, M1State.QUEUED, M1Stage.READY,
                                 M1Actor.SYSTEM, "scheduler:release_deps",
                                 reason="deps_satisfied")
                event = emit_event(db, type="task_released", entity="task",
                                   entity_id=t.id, payload={"reason": "deps_met"})
                db.add(t)
                db.commit()
                broadcast_for_event(event)


def _assign_to_idle_agents():
    """Task-first 调度：把 ready 待领任务按优先级分配给空闲在线 agent。

    先排任务（priority desc, created_at asc），逐任务找匹配空闲 agent，
    agent 分到任务后即标记忙（一 tick 一个 agent 最多一单）。
    """
    with Session(engine) as db:
        ready = db.exec(
            select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
            .order_by(Task.priority.desc(), Task.created_at.asc())
        ).all()
        now = datetime.now(timezone.utc)
        for t in ready:
            if t.schedule_type == "once" and t.run_at:
                run_at = t.run_at
                if run_at.tzinfo is None:
                    run_at = run_at.replace(tzinfo=timezone.utc)
                if run_at > now:
                    continue
            agents = db.exec(select(Agent).where(Agent.status == AgentStatus.ONLINE)).all()
            target = None
            for a in agents:
                if a.agent_type and t.target_agent_type and a.agent_type != t.target_agent_type:
                    continue
                busy = db.exec(
                    select(Run).where(Run.agent_name == a.name,
                                      Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
                ).first()
                if busy:
                    continue
                target = a
                break
            if target is None:
                continue
            run = _claim_for(target.name, db, agent_type=target.agent_type or None)
            if run is None:
                db.rollback()
                continue
            if run.task_id != t.id:
                # 并发下该 agent 可能已被占（返回了别的 run），跳过此任务
                db.rollback()
                continue
            task = db.get(Task, run.task_id)
            event = emit_event(db, type="task_assigned", entity="task", entity_id=task.id,
                               run_id=run.id, payload={"agent": target.name, "reason": "idle_assign",
                                                       "run_id": run.id})
            db.add(task)
            db.commit()
            broadcast_for_event(event)


def _mark_stale_agents():
    """把超过 AGENT_TIMEOUT_SECONDS 未心跳的 agent 标记为 OFFLINE。

    只改 agent status，不动 Run（run 由 _on_timeout 回收）。DB 层过滤，不全表遍历。
    """
    # SQLite 存储的 datetime 无 tzinfo（naive UTC），故 cutoff 也用 naive 比较
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=AGENT_TIMEOUT_SECONDS)).replace(tzinfo=None)
    with Session(engine) as db:
        stale = db.exec(
            select(Agent).where(
                Agent.status != AgentStatus.OFFLINE,
                Agent.last_heartbeat.is_not(None),
                Agent.last_heartbeat < cutoff,
            )
        ).all()
        for a in stale:
            a.status = AgentStatus.OFFLINE
            event = emit_event(db, type="agent_offline", entity="agent",
                               entity_id=a.name, payload={"reason": "heartbeat_timeout"})
            db.add(a)
            db.commit()
            broadcast_for_event(event)


def _scheduler_tick():
    _mark_stale_agents()          # ① agent 生命周期
    _requeue_retries()            # ② 重试退避到期重入
    _release_dependencies()       # ③ 依赖放行
    _assign_to_idle_agents()      # ④ 空闲分配


def _get_due_tasks():
    _scheduler_tick()
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        tasks = db.exec(select(Task).where(Task.state == TaskState.QUEUED)).all()
        due = []
        for t in tasks:
            run_at = t.run_at
            if run_at is not None and run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if t.schedule_type == "cron" or (run_at and run_at <= now):
                due.append({"id": t.id})
        return due


def _on_enqueue(task_id: str):
    pass


class ThreadRegistry:
    """统一管理所有后台守护线程的启动与停止。

    按注册顺序启动，按反向顺序停止（保证依赖正确的关闭顺序）。
    提供健康检查接口。
    """

    def __init__(self):
        self._entries: list[tuple[str, threading.Thread, object]] = []

    def register(self, name: str, thread: threading.Thread, obj: object = None):
        """注册一个守护线程及其停止方法。"""
        self._entries.append((name, thread, obj))

    def start_all(self):
        """按注册顺序启动所有线程。"""
        for name, thread, _ in self._entries:
            thread.start()

    def stop_all(self):
        """按反向顺序停止所有线程（保证依赖正确的关闭顺序）。"""
        for name, thread, obj in reversed(self._entries):
            if obj is not None and hasattr(obj, "stop"):
                obj.stop()
            thread.join(timeout=5)

    def health_check(self) -> dict:
        """返回所有线程的健康状态。"""
        result = {}
        for name, thread, _ in self._entries:
            result[name] = "alive" if thread.is_alive() else "dead"
        return result


_thread_registry = ThreadRegistry()


def register_thread(name: str, thread: threading.Thread, obj: object = None):
    """注册一个守护线程到全局调度器。"""
    _thread_registry.register(name, thread, obj)


class ThreadRegistry:
    """统一管理所有后台守护线程的启动与停止。

    按注册顺序启动，按反向顺序停止（保证依赖正确的关闭顺序）。
    提供健康检查接口（含心跳状态）。
    """

    def __init__(self):
        self._entries: list[tuple[str, threading.Thread, object]] = []

    def register(self, name: str, thread: threading.Thread, obj: object = None):
        """注册一个守护线程及其停止方法。"""
        self._entries.append((name, thread, obj))

    def start_all(self):
        """按注册顺序启动所有线程。"""
        for name, thread, _ in self._entries:
            thread.start()

    def stop_all(self):
        """按反向顺序停止所有线程（保证依赖正确的关闭顺序）。"""
        for name, thread, obj in reversed(self._entries):
            if obj is not None and hasattr(obj, "stop"):
                obj.stop()
            thread.join(timeout=5)

    def health_check(self) -> dict:
        """返回所有线程的健康状态（含心跳）。"""
        thread_health = get_thread_health()
        result = {}
        for name, thread, _ in self._entries:
            hb = thread_health.get(name, {})
            result[name] = {
                "alive": thread.is_alive(),
                "heartbeat_age_seconds": hb.get("age_seconds"),
                "consecutive_failures": hb.get("consecutive_failures", 0),
                "last_failure": hb.get("last_failure"),
            }
        return result


_thread_registry = ThreadRegistry()


def register_thread(name: str, thread: threading.Thread, obj: object = None):
    """注册一个守护线程到全局调度器。"""
    _thread_registry.register(name, thread, obj)


def start_background_jobs():
    sweep = HeartbeatSweep(get_runs=_get_runs, on_timeout=_on_timeout, on_alive=_on_alive)
    scheduler = Scheduler(get_due_tasks=_get_due_tasks, on_enqueue=_on_enqueue)
    idea_scanner = IdeaReviewScanner()
    sweep.start()
    scheduler.start()
    idea_scanner.start()
    register_thread("heartbeat", sweep._thread, sweep)
    register_thread("scheduler", scheduler._thread, scheduler)
    register_thread("idea-review", idea_scanner._thread, idea_scanner)
    return sweep, scheduler, idea_scanner


# ---------- HeartbeatSweep ----------
class HeartbeatSweep:
    def __init__(
        self,
        timeout_seconds: int = 120,
        poll_interval: float = 10.0,
        get_runs: Callable[[], List[RunInfo]] = lambda: [],
        on_timeout: Callable[[str, str], None] = lambda rid, tid: None,
        on_alive: Callable[[str], None] = lambda rid: None,
    ):
        self.timeout = timeout_seconds
        self.interval = poll_interval
        self._get_runs = get_runs
        self._on_timeout = on_timeout
        self._on_alive = on_alive
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self._sweep()
                thread_heartbeat("heartbeat", "running")
            except Exception:
                thread_failure("heartbeat")
                logging.getLogger("mio_taskhub.heartbeat").exception("heartbeat sweep failed")

    def _sweep(self):
        now = time.time()
        for run in self._get_runs():
            if run.state not in (RunState.CLAIMED, RunState.RUNNING):
                continue
            try:
                expired = now - run.last_heartbeat > getattr(run, "timeout_seconds", self.timeout)
                if run.agent_offline or expired:
                    self._on_timeout(run.run_id, run.task_id)
                else:
                    self._on_alive(run.run_id)
            except Exception:
                pass  # isolate per-run failures


# ---------- Scheduler ----------
class Scheduler:
    def __init__(
        self,
        interval: float = 30.0,
        get_due_tasks: Callable[[], List[Dict]] = lambda: [],
        on_enqueue: Callable[[str], None] = lambda tid: None,
    ):
        self.interval = interval
        self._get_due_tasks = get_due_tasks
        self._on_enqueue = on_enqueue
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def tick(self):
        for task in self._get_due_tasks():
            self._on_enqueue(task["id"])

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.tick()
                thread_heartbeat("scheduler", "running")
            except Exception:
                thread_failure("scheduler")
                logging.getLogger("mio_taskhub.scheduling.scheduler").exception("scheduler tick failed")
