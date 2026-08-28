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


def test_move_to_done_lenient_review_result():
    # 拖拽轻量路径：done 缺 review_result 时自动补默认结论并置 completed
    t = _mk("move3", stage="review")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "done"})
    assert r.status_code == 200
    assert r.json()["state"] == "completed"
    # 显式提供 review_result 时也接受
    t2 = _mk("move3b", stage="review")
    r2 = client.post(f"/api/v1/tasks/{t2['id']}/stage/move",
                     json={"target_stage": "done", "review_result": "ok"})
    assert r2.status_code == 200
    assert r2.json()["review_result"] == "ok"


def test_move_to_design_lenient_spec_path():
    # 拖拽轻量路径：design 缺 spec_path 时仅置阶段，不强制 422
    t = _mk("move4", stage="brainstorming")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "design"})
    assert r.status_code == 200
    assert r.json()["stage"] == "design"
    r2 = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                     json={"target_stage": "design", "spec_path": "docs/x.md"})
    assert r2.status_code == 200
    assert r2.json()["spec_path"] == "docs/x.md"


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


def test_move_to_planning_lenient_plan_path():
    # 拖拽轻量路径：planning 缺 plan_path 时仅置阶段，不强制 422
    t = _mk("movep", stage="brainstorming")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "planning"})
    assert r.status_code == 200
    assert r.json()["stage"] == "planning"
    r2 = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                     json={"target_stage": "planning", "plan_path": "docs/p.md"})
    assert r2.status_code == 200
    assert r2.json()["plan_path"] == "docs/p.md"


def test_move_to_cancelled_sets_state():
    t = _mk("movec", stage="implementing")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "cancelled"})
    assert r.status_code == 200
    assert r.json()["state"] == "cancelled"


def test_move_invalid_stage_400():
    t = _mk("moveinv", stage="brainstorming")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "bogus"})
    assert r.status_code == 400


def test_move_missing_target_422():
    t = _mk("movemiss", stage="brainstorming")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={})
    assert r.status_code == 422
