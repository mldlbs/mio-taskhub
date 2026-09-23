# mio_taskhub/heartbeat.py
from __future__ import annotations
import os
import threading
import time as _time
from dataclasses import dataclass
from typing import Callable, List, Optional

from mio_taskhub.models import RunState


def _env_int(name: str, default: int) -> int:
    """读正整数环境变量，非法/缺失/非正值时回退默认值。"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# run 存活基线：任务未显式配置 timeout_min 时使用。
# 2026-09-17 实测 agent 心跳间隔 67s~199s，原值 120s 过紧，会把仍在上报
# progress 的 run 判死并永久 FAILED 掉任务。
DEFAULT_TIMEOUT_SECONDS = _env_int("MIO_TASKHUB_TIMEOUT_SECONDS", 300)

# agent 已 OFFLINE 时的回收上限：只收紧有效超时，绝不旁路 run 级新鲜度。
AGENT_OFFLINE_TIMEOUT_SECONDS = _env_int("MIO_TASKHUB_AGENT_OFFLINE_SECONDS", 120)


@dataclass
class RunInfo:
    run_id: str
    task_id: str
    agent_name: str
    state: RunState
    last_heartbeat: float
    attempt: int
    max_retries: int
    # None = 未指定，沿用 sweep 自身的 timeout（生产侧 _get_runs 总会显式传入）
    timeout_seconds: Optional[int] = None
    agent_offline: bool = False


class HeartbeatSweep:
    def __init__(
        self,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        poll_interval: float = 10.0,
        get_runs: Callable[[], List[RunInfo]] = lambda: [],
        on_timeout: Callable[[str, str], None] = lambda rid, tid: None,
        on_alive: Callable[[str], None] = lambda rid: None,
        agent_offline_timeout: int = AGENT_OFFLINE_TIMEOUT_SECONDS,
        on_tick: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[], None]] = None,
    ):
        self.timeout = timeout_seconds
        self.interval = poll_interval
        self.agent_offline_timeout = agent_offline_timeout
        self._get_runs = get_runs
        self._on_timeout = on_timeout
        self._on_alive = on_alive
        self._on_tick = on_tick
        self._on_error = on_error
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
        import logging
        while not self._stop.wait(self.interval):
            try:
                self._sweep()
                if self._on_tick is not None:
                    self._on_tick()
            except Exception:
                logging.getLogger("mio_taskhub.heartbeat").exception("heartbeat sweep failed")
                if self._on_error is not None:
                    self._on_error()

    def effective_timeout(self, run: RunInfo) -> float:
        """本次判死用的有效超时。

        agent OFFLINE 只是把上限收紧到基线（用于快速回收僵尸 run），
        不能用它跳过 run 级心跳新鲜度判定 —— agent 级心跳与 run 级心跳是
        两条独立信号，长任务里 agent 可能只在 claim 前心跳一次。
        """
        timeout = getattr(run, "timeout_seconds", None) or self.timeout
        if run.agent_offline:
            timeout = min(timeout, self.agent_offline_timeout)
        return timeout

    def _sweep(self):
        now = _time.time()
        for run in self._get_runs():
            if run.state not in (RunState.CLAIMED, RunState.RUNNING):
                continue
            try:
                expired = now - run.last_heartbeat > self.effective_timeout(run)
                if expired:
                    self._on_timeout(run.run_id, run.task_id)
                else:
                    self._on_alive(run.run_id)
            except Exception:
                pass  # isolate per-run failures
