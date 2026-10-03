# -*- coding: utf-8 -*-
"""Agent 生命周期观测脚手架（task 5b4fa957 / P1-OBS-2）。

**纯观测**：不改 reaper / timeout / 门控 / 状态机 / submit / heartbeat 语义。

设计（见 docs/taskhub/design-agent-lifecycle-instrumentation.md）：
- 事件类型（namespace 前缀 agent_lifecycle.*）：
    process_start / startup_success / startup_failure / process_exit
- **本地 durable jsonl 是唯一真相源**：先落盘（按 pid 分文件，天然免锁），
  再异步 fire-and-forget 上报 Hub；Hub 恢复不是由 worker 补传（worker 短命），
  而是由 scripts/ingest_lifecycle.py **非实时、幂等**导入。
- 两级证据：process_alive（结果态回填，不 sleep）+ agent_ready（以 Hub 侧为准）。
  心跳链失效 C 的坐实需要本地 heartbeat_sent_count>0（本地发了、Hub 没收到）。
- 开关：MIO_LIFECYCLE_PROBE=1 才启用；关闭时**本地也不落盘**（零副作用）。

本模块不 import mio_taskhub 其它内部模块，可在执行侧（packaging/idle_worker.py）
独立或按路径加载使用；Hub 侧导入用标准包路径。
"""
import json
import os
import threading
import time
import urllib.request

PROBE_ENV = "MIO_LIFECYCLE_PROBE"
GRACE_ENV = "MIO_LIFECYCLE_GRACE_SECONDS"
DEFAULT_GRACE_SECONDS = 15.0

NS = "agent_lifecycle"

EV_PROCESS_START = NS + ".process_start"
EV_STARTUP_SUCCESS = NS + ".startup_success"
EV_STARTUP_FAILURE = NS + ".startup_failure"
EV_PROCESS_EXIT = NS + ".process_exit"

EVENT_TYPES = (EV_PROCESS_START, EV_STARTUP_SUCCESS, EV_STARTUP_FAILURE, EV_PROCESS_EXIT)


def enabled() -> bool:
    """观测开关：MIO_LIFECYCLE_PROBE=1 / true / yes 时启用。"""
    return str(os.environ.get(PROBE_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def grace_seconds() -> float:
    try:
        return float(os.environ.get(GRACE_ENV, DEFAULT_GRACE_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_GRACE_SECONDS


def probe_dir() -> str:
    """本地落盘目录（真相源）。"""
    d = os.path.join(os.path.expanduser("~"), ".mio_taskhub", "lifecycle_probe")
    return d


def _jsonl_path(pid=None) -> str:
    """按 pid 分文件，天然免锁（多进程不互相破坏）。"""
    pid = pid if pid is not None else os.getpid()
    return os.path.join(probe_dir(), "%s.jsonl" % pid)


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _append_local(record: dict, pid=None) -> bool:
    """append 一行 json 到本地 jsonl。失败返回 False（计数入自检，不抛）。"""
    try:
        os.makedirs(probe_dir(), exist_ok=True)
        with open(_jsonl_path(pid), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 —— 落盘失败不可影响执行
        return False


def _report_async(record: dict, hub: str, token: str = "") -> None:
    """异步 fire-and-forget 上报 Hub：daemon 线程 + 短超时 + 不重试。

    绝不进入执行关键路径（红队约束：观测不得拖慢被观测系统）。
    """
    def _post():
        try:
            body = json.dumps({"type": record.get("event_type"),
                               "run_id": record.get("run_id", ""),
                               "payload": record}).encode("utf-8")
            req = urllib.request.Request(
                hub.rstrip("/") + "/events/probe", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("Authorization", "Bearer %s" % token)
            urllib.request.urlopen(req, timeout=2).read()
        except Exception:  # noqa: BLE001 —— 上报失败静默（本地已有真相源）
            pass

    t = threading.Thread(target=_post, daemon=True)
    t.start()


def emit(event_type: str, record: dict, hub: str = "", token: str = "") -> dict:
    """落盘 + 异步上报。返回写入的 record（含 ts）。

    - 开关关闭 → 不落盘不联网，直接返回 record（零副作用）。
    - 始终不抛异常；失败只记 `_local_ok`。
    """
    rec = dict(record or {})
    rec["event_type"] = event_type
    rec.setdefault("ts", _now_iso())

    if not enabled():
        rec["_local_ok"] = False
        rec["_probe_disabled"] = True
        return rec

    ok = _append_local(rec)
    rec["_local_ok"] = ok
    if hub:
        _report_async(rec, hub, token=token)
    return rec


# ---------- 生命周期记录器 ----------

class LifecycleRecorder:
    """一次 run 的生命周期记录器（按 run 隔离，勿用全局变量）。

    用法（执行侧 idle_worker）：
        rec = LifecycleRecorder(run_id, task_id, agent_id, hub=HUB)
        rec.process_start(launched=argv)          # Popen 前/后
        rec.heartbeat_sent()                       # 每次心跳发出后调用（本地计数）
        rec.finish(exit_code, started_at, finished_at, hub_seen_heartbeat=None)
    """

    def __init__(self, run_id: str, task_id: str = "", agent_id: str = "",
                 hub: str = "", token: str = ""):
        self.run_id = run_id
        self.task_id = task_id
        self.agent_id = agent_id
        self.hub = hub
        self.token = token
        self.started_at = None            # monotonic
        self.started_wall = _now_iso()
        self.first_heartbeat_at = None    # wall iso
        self.last_heartbeat_at = None
        self.heartbeat_sent_count = 0
        self._lock = threading.Lock()

    def _base(self):
        return {"run_id": self.run_id, "task_id": self.task_id, "agent_id": self.agent_id}

    def process_start(self, launched: str = "", pid=None):
        self.started_at = time.monotonic()
        self.started_wall = _now_iso()
        rec = self._base()
        rec.update({"pid": pid, "launched": str(launched)[:500]})
        return emit(EV_PROCESS_START, rec, hub=self.hub, token=self.token)

    def heartbeat_sent(self):
        """每次心跳**发出**后调用（本地计数；这是坐实心跳链失效 C 的唯一本地证据）。"""
        with self._lock:
            self.heartbeat_sent_count += 1
            now = _now_iso()
            if self.first_heartbeat_at is None:
                self.first_heartbeat_at = now
            self.last_heartbeat_at = now

    def startup_success(self, hub_first_heartbeat_at: str = "", process_alive=None):
        """agent_ready=成立（Hub 侧收到首心跳）时调用。"""
        latency_ms = None
        if self.started_at is not None:
            latency_ms = int((time.monotonic() - self.started_at) * 1000)
        rec = self._base()
        rec.update({
            "agent_ready": True,
            "process_alive": process_alive,
            "startup_latency_ms": latency_ms,
            "first_heartbeat_at": hub_first_heartbeat_at or self.first_heartbeat_at,
        })
        return emit(EV_STARTUP_SUCCESS, rec, hub=self.hub, token=self.token)

    def startup_failure(self, startup_error: str = "", process_alive=None, exit_code=None):
        rec = self._base()
        rec.update({
            "agent_ready": False,
            "process_alive": process_alive,
            "startup_error": str(startup_error)[:500],
            "exit_code": exit_code,
        })
        return emit(EV_STARTUP_FAILURE, rec, hub=self.hub, token=self.token)

    def finish(self, exit_code=None, finished_at_monotonic=None, hub_seen_heartbeat=None,
               grade="complete"):
        """进程结束：回填 process_alive（存活时长是否>grace）——结果态，不 sleep。"""
        end = finished_at_monotonic if finished_at_monotonic is not None else time.monotonic()
        alive_seconds = None
        process_alive = None
        if self.started_at is not None:
            alive_seconds = round(end - self.started_at, 3)
            process_alive = alive_seconds > grace_seconds()
        rec = self._base()
        rec.update({
            "exit_code": exit_code,
            "alive_seconds": alive_seconds,
            "process_alive": process_alive,
            "agent_ready": bool(hub_seen_heartbeat),
            "heartbeat_sent_count": self.heartbeat_sent_count,
            "first_heartbeat_at": self.first_heartbeat_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "grade": grade,
        })
        return emit(EV_PROCESS_EXIT, rec, hub=self.hub, token=self.token)


# ---------- 判定辅助（供裁决脚本/测试使用）----------

def classify(start_rec=None, success_rec=None, failure_rec=None, exit_rec=None):
    """据生命周期记录判定 A/B/C（对齐设计 §5 可判定矩阵）。

    返回其中一个：'startup_failure'(A) / 'died_after_start'(B) /
    'heartbeat_broken'(C) / 'alive_no_heartbeat'(C?) / 'inferred_exit' / 'unknown'
    """
    start_rec = start_rec or {}
    exit_rec = exit_rec or {}
    alive = exit_rec.get("process_alive")
    ready = exit_rec.get("agent_ready")
    sent = exit_rec.get("heartbeat_sent_count") or 0

    if failure_rec and not success_rec:
        return "startup_failure"          # A
    if ready and sent > 0:
        return "died_after_start" if exit_rec else "died_after_start"  # B
    if alive and not ready:
        # C 与网络分区区分：本地已发（sent>0）而 Hub 未 ready ⇒ 坐实链路
        if sent > 0:
            return "heartbeat_broken"      # C 坐实
        return "alive_no_heartbeat"        # C?（agent 起来但没工作）
    return "unknown"
