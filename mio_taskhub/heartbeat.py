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
    # None = 未指定，沿用 sweep 自身 timeout（生产侧 _get_runs 总会显式传入）
    timeout_seconds: Optional[int] = None
    agent_offline: bool = False
    # 已上报进度（>0 说明 agent 在工作，用于区分 never_started）
    progress: int = 0


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

        P1-2 修正：agent OFFLINE 时**不再无条件收窄到 120s**。
        旧逻辑 `min(timeout, agent_offline_timeout)` 会用全局 120s 覆盖任务自己的
        预算，导致「agent 短暂掉线 + 长任务（est_duration_min=90）」在 120s 被杀。

        现规则：
          - 若 run 已上报过进度（progress > 0），说明 agent 确实在工作：用任务自己的
            窗口（timeout_seconds），agent OFFLINE 时最多按 offline 上限适度放宽（×2），
            绝不收到 120s。
          - 若 run 从未上报进度（never_started），用较紧的 offline 窗口快速回收僵尸 run。
        """
        timeout = getattr(run, "timeout_seconds", None) or self.timeout
        if run.agent_offline:
            if getattr(run, "progress", 0) and run.progress > 0:
                # 在工作：保留任务窗口；offline 上限放宽到 2×，避免误杀长任务
                timeout = min(timeout, self.agent_offline_timeout * 2)
            else:
                # 从未启动：可快速回收（但仍以 offline 上限为准）
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
