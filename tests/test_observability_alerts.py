# -*- coding: utf-8 -*-
"""活性告警：队列陈旧、无跃迁、无消费者、调度状态丢失。"""
import time
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.models import ScheduledJob, Task
from mio_taskhub.observability.alerts import AlertManager


def test_task_activity_alerts_fire():
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=40)
    with Session(engine) as s:
        s.add(Task(id="queued-old", title="stuck", state="QUEUED",
                   created_at=old, last_transition_at=old))
        s.add(ScheduledJob(id="job-null", name="lost", cron_expr="0 * * * *",
                           enabled=True, next_run_at=None))
        s.commit()

    names = {a.name for a in AlertManager()._check_tasks()}
    assert "TaskQueueStale" in names
    assert "NoTaskTransition" in names
    assert "NoConsumers" not in names

    mgr = AlertManager()
    mgr._condition_since["NoConsumers"] = time.time() - 301
    names = {a.name for a in mgr._check_tasks()}
    assert "NoConsumers" in names
    assert "SchedulerStateLost" in names


def test_no_queue_no_activity_alerts():
    names = {a.name for a in AlertManager()._check_tasks()}
    assert "TaskQueueStale" not in names
    assert "NoConsumers" not in names
