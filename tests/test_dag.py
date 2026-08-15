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
    # state=completed（非 stage=done）也应满足依赖
    parent = _mk("p", stage="ready")
    client.post("/api/v1/agents/register", json={"name": "dag-a", "agent_type": "t"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "dag-a"}).json()
    client.post(f"/api/v1/runs/{claim['id']}/heartbeat", json={"progress": 100})
    client.post(f"/api/v1/runs/{claim['id']}/result", json={"success": True, "result": "ok"})
    b = _mk("b", stage="planning", deps=[parent["id"]])
    _release()
    assert _stage(b["id"]) == TaskStage.READY


def test_dangling_dep_not_released():
    b = _mk("b", stage="planning", deps=["bogus-id"])
    _release()
    assert _stage(b["id"]) == TaskStage.PLANNING


def test_release_idempotent_no_duplicate_events():
    a = _mk("a", stage="done")
    b = _mk("b", stage="planning", deps=[a["id"]])
    _release()
    _release()  # 第二次 tick 不应再发事件
    r = client.get("/api/v1/events", params={"after_seq": 0}).json()
    released = [e for e in r["events"] if e["type"] == "task_released"]
    assert len(released) == 1


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


def test_create_task_with_deps_returns_array():
    r = _mk("d", stage="ready", deps=["abc"])
    assert r["depends_on"] == ["abc"]


def test_create_task_cyclic_dependency_rejected():
    a = _mk("a", stage="ready")
    b = _mk("b", stage="ready", deps=[a["id"]])
    r = client.patch(f"/api/v1/tasks/{a['id']}", json={"depends_on": [b["id"]]})
    assert r.status_code == 422
    assert "cyclic" in r.json()["detail"]


def test_update_depends_on_stores_list():
    a = _mk("a", stage="ready")
    b = _mk("b", stage="ready")
    r = client.patch(f"/api/v1/tasks/{a['id']}", json={"depends_on": [b["id"]]})
    assert r.status_code == 200
    d = client.get(f"/api/v1/tasks/{a['id']}").json()
    assert d["depends_on"] == [b["id"]]
    assert d["idea_id"] == ""


def test_legacy_string_depends_readable():
    # 直接建一个 depends_on 为字符串的任务模拟旧数据（模型层面已归一化）
    with Session(engine) as s:
        t = Task(title="legacy")
        t.depends_on = ["x"]
        s.add(t); s.commit()
        tid = t.id
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["depends_on"] == ["x"]
