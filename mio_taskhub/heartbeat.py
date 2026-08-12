# mio_taskhub/heartbeat.py
from __future__ import annotations
import threading
import time as _time
from dataclasses import dataclass
from typing import Callable, List, Optional
from mio_taskhub.models import RunState

@dataclass
class RunInfo:
    run_id: str
    task_id: str
    agent_name: str
    state: RunState
    last_heartbeat: float
    attempt: int
    max_retries: int

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
            self._sweep()

    def _sweep(self):
        now = _time.time()
        for run in self._get_runs():
            if run.state not in (RunState.CLAIMED, RunState.RUNNING):
                continue
            try:
                if now - run.last_heartbeat > self.timeout:
                    self._on_timeout(run.run_id, run.task_id)
                else:
                    self._on_alive(run.run_id)
            except Exception:
                pass  # isolate per-run failures
