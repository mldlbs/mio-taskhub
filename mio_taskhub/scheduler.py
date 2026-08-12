from __future__ import annotations
import threading
import time
from typing import Callable, List, Dict

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
            self.tick()
