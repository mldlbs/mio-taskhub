from datetime import datetime, timezone
from sqlmodel import Session, select
from mio_taskhub.db import engine
from mio_taskhub.api.tasks import _claim_for
from mio_taskhub.models import Agent, AgentStatus, Run, RunState, Task, TaskStage, TaskState
from mio_taskhub.heartbeat import HeartbeatSweep, RunInfo
from mio_taskhub.scheduler import Scheduler
from mio_taskhub.status import is_terminal, dependency_satisfied, task_deps
from mio_taskhub.events import emit_event, broadcast_for_event

DEFAULT_TIMEOUT_SECONDS = 120


def _get_runs():
    with Session(engine) as db:
        runs = db.exec(select(Run).where(Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))).all()
        out = []
        for run in runs:
            task = db.get(Task, run.task_id)
            timeout_sec = (task.timeout_min * 60) if task and task.timeout_min else DEFAULT_TIMEOUT_SECONDS
            last_hb = run.last_heartbeat or run.started_at
            if last_hb.tzinfo is None:
                last_hb = last_hb.replace(tzinfo=timezone.utc)
            out.append(RunInfo(
                run_id=run.id, task_id=run.task_id, agent_name=run.agent_name,
                state=run.state, last_heartbeat=last_hb.timestamp(),
                attempt=run.attempt, max_retries=task.max_retries if task else 3,
                timeout_seconds=timeout_sec,
            ))
        return out


def _on_timeout(run_id: str, task_id: str):
    with Session(engine) as db:
        run = db.get(Run, run_id)
        task = db.get(Task, task_id)
        if run and run.state in (RunState.CLAIMED, RunState.RUNNING):
            run.state = RunState.FINISHED
            run.result = "heartbeat timeout"
            run.finished_at = datetime.now(timezone.utc)
            run.exit_code = 1
            db.add(run)
            if task:
                if task.attempt >= task.max_retries:
                    task.state = TaskState.FAILED
                else:
                    task.state = TaskState.QUEUED
                    task.stage = TaskStage.READY
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
                t.stage = TaskStage.READY
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
            run = _claim_for(target.name, db, agent_type=t.target_agent_type)
            if run is None:
                db.rollback()
                continue
            task = db.get(Task, run.task_id)
            event = emit_event(db, type="task_assigned", entity="task", entity_id=task.id,
                               run_id=run.id, payload={"agent": target.name, "reason": "idle_assign",
                                                       "run_id": run.id})
            db.add(task)
            db.commit()
            broadcast_for_event(event)


def _get_due_tasks():
    _release_dependencies()   # 先放行依赖
    _assign_to_idle_agents()  # 再分配空闲 agent
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
    # task already queued; nothing to change, but touch for completeness
    pass


def start_background_jobs():
    sweep = HeartbeatSweep(get_runs=_get_runs, on_timeout=_on_timeout, on_alive=_on_alive)
    scheduler = Scheduler(get_due_tasks=_get_due_tasks, on_enqueue=_on_enqueue)
    sweep.start()
    scheduler.start()
    return sweep, scheduler
