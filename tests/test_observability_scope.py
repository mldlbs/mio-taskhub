# -*- coding: utf-8 -*-
"""当前交付面口径：278 口径的 SQL 与比率。"""
from datetime import datetime, timezone

from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage, TaskState
from mio_taskhub.observability.report import _collect_tasks
from mio_taskhub.observability.metrics import render_metrics


def _task(state, kind="NORMAL", labels=None, created=None):
    return Task(title="t", state=state, stage=TaskStage.READY,
                task_kind=kind, labels=labels or [],
                created_at=created or datetime.now(timezone.utc))


def test_delivery_scope_excludes_noise_and_members():
    old = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with Session(engine) as s:
        s.add(_task(TaskState.COMPLETED))
        s.add(_task(TaskState.COMPLETED))
        s.add(_task(TaskState.COMPLETED, labels=["part-of:p1"]))
        s.add(_task(TaskState.FAILED))
        s.add(_task(TaskState.FAILED, kind="REVIEW", created=old))
        s.add(_task(TaskState.FAILED, kind="REVIEW", created=old))
        s.add(_task(TaskState.QUEUED))
        s.add(_task(TaskState.CANCELLED))
        s.commit()
    data = _collect_tasks()
    scope = data["scope"]
    assert scope["total"] == 4
    assert scope["completed"] == 2
    assert scope["failed"] == 1
    assert scope["queued"] == 1
    assert scope["success_rate"] == 0.5
    assert scope["failure_rate"] == 0.25
    assert data["history"]["total"] == 7
    assert data["history"]["failure_rate"] == round(3 / 7, 4)

    text = render_metrics()
    assert "taskhub_task_success_rate 0.5000" in text
    assert "taskhub_task_failure_rate 0.2500" in text
    assert "taskhub_task_scope_total 4" in text
    assert "taskhub_task_success_rate_history 0.4286" in text
