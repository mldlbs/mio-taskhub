# mio_taskhub/observability/lifecycle.py
"""Agent 生命周期埋点（T0.7，设计见 docs/taskhub/design-agent-lifecycle-instrumentation.md）。

三类事件 agent_lifecycle.*：本地 jsonl 为真相源（$MIO_HOME/lifecycle/<pid>/<run_id>.jsonl），
异步 fire-and-forget（Queue + daemon worker），任何失败静默丢弃——绝不阻塞
心跳 / claim / submit 关键路径（沿用 reaper_decision 观测先例语义）。
event 表由 scripts/ingest_lifecycle.py 幂等回填（键 type+run_id+ts）。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("mio_taskhub.lifecycle")

TYPE_AGENT_READY = "agent_lifecycle.agent_ready"
TYPE_HEARTBEAT_SENT = "agent_lifecycle.heartbeat_sent_count"
TYPE_PROCESS_ALIVE = "agent_lifecycle.process_alive"
ALL_TYPES = (TYPE_AGENT_READY, TYPE_HEARTBEAT_SENT, TYPE_PROCESS_ALIVE)

_SAFE_RUN_ID = re.compile(r"[^A-Za-z0-9_.-]")
_QUEUE_MAX = 1000
_queue: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=_QUEUE_MAX)
_worker_lock = threading.Lock()
_worker_started = False

# run_id -> 最近已探测的 pid（pid 未变化不重复探测；hub 重启后自然复测一次）
_probe_gate_lock = threading.Lock()
_probed_pid: dict[str, int] = {}
# agent 侧发送计数（每 MCP 进程独立，重启归零；ts 含进程期信息可分辨）
_sent_lock = threading.Lock()
_sent_counts: dict[str, int] = {}


def lifecycle_dir() -> Path:
    """真相源根目录：$MIO_HOME/lifecycle（与 mio_runtime.home() 同口径）。"""
    try:
        from mio_taskhub.mio_runtime import home
        base = home()
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("MIO_HOME") or (Path.home() / ".mio-intelligence"))
    return Path(base) / "lifecycle"


def record(event_type: str, run_id: str, payload: Optional[dict] = None) -> None:
    """追加一条生命周期事件（fire-and-forget，绝不抛异常）。"""
    try:
        entry = {
            "type": event_type,
            "run_id": str(run_id),
            "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "pid": os.getpid(),
        }
        if payload:
            entry.update(payload)
        _ensure_worker()
        _queue.put_nowait(entry)
    except Exception:  # noqa: BLE001 — 埋点失败绝不影响调用方
        pass


def note_heartbeat_sent(run_id: str, delivered: bool) -> int:
    """agent 侧：记录一次心跳发送尝试（本进程内累计计数）。返回本进程 sent 总数。"""
    try:
        with _sent_lock:
            n = _sent_counts.get(str(run_id), 0) + 1
            _sent_counts[str(run_id)] = n
        record(TYPE_HEARTBEAT_SENT, run_id,
               {"sent_count": n, "delivered": bool(delivered)})
        return n
    except Exception:  # noqa: BLE001
        return 0


def probe_process_alive(run_id: str, pid: int, force: bool = False) -> bool:
    """异步探测 pid 存活并回填 process_alive 结果（force 忽略 pid 去重门控）。

    返回 False 表示门控拒绝（pid 未变化且非 force），True 表示探测已派出。
    """
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if not force:
            with _probe_gate_lock:
                if _probed_pid.get(str(run_id)) == pid:
                    return False
                _probed_pid[str(run_id)] = pid
        threading.Thread(target=_probe_safe, args=(str(run_id), pid),
                         daemon=True, name=f"lifecycle-probe-{pid}").start()
        return True
    except Exception:  # noqa: BLE001
        return False


def flush(timeout: float = 5.0) -> bool:
    """等待队列清空且在途写盘完成（测试/验收用）。返回是否在超时内完成。"""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _queue.unfinished_tasks == 0:
            return True
        time.sleep(0.02)
    return False


# ── internals ──────────────────────────────────────────────────────────

def _ensure_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker, daemon=True,
                         name="lifecycle-writer").start()
        _worker_started = True


def _worker():
    while True:
        entry = None
        try:
            entry = _queue.get()
            if entry is None:
                continue
            _write_entry(entry)
        except Exception:  # noqa: BLE001 — 单条失败丢弃，worker 存活
            logger.debug("lifecycle write failed: %r", entry, exc_info=True)
        finally:
            if entry is not None:
                try:
                    _queue.task_done()
                except Exception:  # noqa: BLE001
                    pass


def _write_entry(entry: dict) -> None:
    run_id = _SAFE_RUN_ID.sub("_", str(entry.get("run_id") or "unknown"))
    path = lifecycle_dir() / str(entry.get("pid") or "unknown") / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _probe_safe(run_id: str, pid: int) -> None:
    """探测线程：结果态回填 alive / false / null(unknown)，异常全吞。"""
    try:
        alive = _probe_pid(pid)
        record(TYPE_PROCESS_ALIVE, run_id,
               {"pid": pid, "alive": alive, "probe": True})
    except Exception:  # noqa: BLE001
        pass


def _probe_pid(pid: int) -> Optional[bool]:
    try:
        import psutil
    except ImportError:
        return _probe_fallback(pid)
    try:
        p = psutil.Process(pid)
        return bool(p.is_running() and p.status() != psutil.STATUS_ZOMBIE)
    except Exception as e:  # noqa: BLE001 — NoSuchProcess/AccessDenied 归一化
        name = type(e).__name__
        if name in ("NoSuchProcess", "ZombieProcess"):
            return False
        if name == "AccessDenied":
            return True
        return None


def _probe_fallback(pid: int) -> Optional[bool]:
    """无 psutil 时的平台探测。

    Windows：**绝不用 os.kill(pid, 0)**（Windows 上 signal 0 == CTRL_C_EVENT，
    会向前台组发 Ctrl+C）；用 OpenProcess 只查询不干涉。
    POSIX：os.kill(pid, 0) 仅探活。
    """
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(
                0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                # 仍在运行 vs 已退出未清理：WaitForSingleObject(handle, 0)
                # WAIT_OBJECT_0(0) = 已 signaled（已退出）
                r = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
                ctypes.windll.kernel32.CloseHandle(handle)
                return r != 0  # 未 signaled → 存活
            return False
        except Exception:  # noqa: BLE001
            return None
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:  # noqa: BLE001
        return None
