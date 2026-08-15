# tests/test_dag.py
from datetime import datetime, timezone
from sqlmodel import Session, select
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage, TaskState
import mio_taskhub.wiring as wiring

client = TestClient(app)


def _mk(title, stage="ready", deps=None, **kw):
    body = {"title": title, "stage": stage}
    if deps is not None:
        body["depends_on"] = deps
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


def _release():
    wiring._release_dependencies()


def _stage(tid):
    with Session(engine) as s:
        return s.get(Task, tid).stage


def test_all_deps_met_releases_to_ready():
    parent = _mk("p", stage="done")
    child = _mk("c", stage="planning", deps=[parent["id"]])
    _release()
    assert _stage(child["id"]) == TaskStage.READY


def test_partial_deps_not_released():
    a = _mk("a", stage="done")
    b = _mk("b", stage="brainstorming")
    c = _mk("c", stage="planning", deps=[a["id"], b["id"]])
    _release()
    assert _stage(c["id"]) == TaskStage.PLANNING


def test_done_means_satisfied():
    a = _mk("a", stage="done")
    b = _mk("b", stage="planning", deps=[a["id"]])
    _release()
    assert _stage(b["id"]) == TaskStage.READY


def test_cancelled_dependency_blocks_and_alerts():
    a = _mk("a", stage="brainstorming")
    client.post(f"/api/v1/tasks/{a['id']}/stage", json={"target_stage": "cancelled"})
    b = _mk("b", stage="planning", deps=[a["id"]])
    _release()
    assert _stage(b["id"]) == TaskStage.PLANNING


def test_release_skips_terminal_and_ready():
    a = _mk("a", stage="done")
    ready = _mk("r", stage="ready", deps=[a["id"]])
    _release()
    assert _stage(ready["id"]) == TaskStage.READY  # 已 ready 不变


def test_release_writes_event():
    a = _mk("a", stage="done")
    b = _mk("b", stage="planning", deps=[a["id"]])
    _release()
    r = client.get("/api/v1/events", params={"after_seq": 0}).json()
    assert any(e["type"] == "task_released" and e["entity_id"] == b["id"] for e in r["events"])
