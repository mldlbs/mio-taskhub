# tests/test_stage_move.py
from fastapi.testclient import TestClient
from mio_taskhub.main import app

client = TestClient(app)


def _mk(title, stage="brainstorming", **kw):
    body = {"title": title, "stage": stage}
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


def test_move_to_any_stage():
    t = _mk("move1", stage="brainstorming")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "review"})
    assert r.status_code == 200
    assert r.json()["stage"] == "review"


def test_move_backwards():
    t = _mk("move2", stage="implementing")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "brainstorming"})
    assert r.status_code == 200
    assert r.json()["stage"] == "brainstorming"


def test_move_to_done_requires_review_result():
    t = _mk("move3", stage="review")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "done"})
    assert r.status_code == 422
    r2 = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                     json={"target_stage": "done", "review_result": "ok"})
    assert r2.status_code == 200
    assert r2.json()["state"] == "completed"


def test_move_to_design_requires_spec_path():
    t = _mk("move4", stage="brainstorming")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "design"})
    assert r.status_code == 422
    r2 = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                     json={"target_stage": "design", "spec_path": "docs/x.md"})
    assert r2.status_code == 200


def test_move_terminal_stage_blocked():
    t = _mk("move5", stage="done")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "review"})
    assert r.status_code == 400


def test_move_writes_event():
    t = _mk("move6", stage="brainstorming")
    client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                json={"target_stage": "planning", "plan_path": "docs/p.md"})
    ev = client.get("/api/v1/events", params={"after_seq": 0}).json()
    moved = [e for e in ev["events"] if e["type"] == "task_moved"]
    assert moved and moved[-1]["payload"]["from"] == "brainstorming"
    assert moved[-1]["payload"]["to"] == "planning"


def test_move_404():
    r = client.post("/api/v1/tasks/nope/stage/move", json={"target_stage": "ready"})
    assert r.status_code == 404
