# -*- coding: utf-8 -*-
"""T4.3/P1-OBS：手动 retry 应重置 never_started bounce 累积。"""
from fastapi.testclient import TestClient
from sqlmodel import Session

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskState, TaskStage

client = TestClient(app)


def _mk_failed_with_bounce(bounce: int):
    r = client.post("/api/v1/tasks", json={"title": "retry-bounce", "stage": "ready"})
    tid = r.json()["id"]
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.state = TaskState.FAILED
        t.stage = TaskStage.IMPLEMENTING
        t.bounce_count = bounce
        s.add(t)
        s.commit()
    return tid


def test_retry_resets_bounce_count():
    """bounce 已达上限（6）后 retry → 应清零（否则首个 never_started 立即再 FAILED）。"""
    tid = _mk_failed_with_bounce(6)
    r = client.post(f"/api/v1/tasks/{tid}/retry")
    assert r.status_code == 200, r.text
    with Session(engine) as s:
        t = s.get(Task, tid)
        assert t.bounce_count == 0, f"retry 未重置 bounce_count（={t.bounce_count}）"


def test_retry_from_failed_reopens():
    tid = _mk_failed_with_bounce(3)
    r = client.post(f"/api/v1/tasks/{tid}/retry")
    assert r.status_code == 200
    assert r.json()["state"] == "queued"
