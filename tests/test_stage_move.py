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


# ── 回归：move→done 非 review 源（2026-09-25 修复 500）─────────────────────

def test_move_done_from_ready_via_review():
    # 原实现按 src 阶段直跳 (completed,ready) → 未定义转换 → 500；
    # 现经 T18 挪 review + T5 + T6 完成
    t = _mk("moveready", stage="ready")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                    json={"target_stage": "done", "review_result": "验收复核"})
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "done"
    assert r.json()["state"] == "completed"


def test_move_done_from_implementing():
    t = _mk("moveimpl", stage="implementing")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                    json={"target_stage": "done", "review_result": "ok"})
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "done"
    assert r.json()["state"] == "completed"


def test_move_done_from_completed_implementing():
    # state=completed 但 stage 停在 implementing：T18 挪到 review 后 T6 收尾
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskState, TaskStage
    t = _mk("moveci", stage="implementing")
    with Session(engine) as s:
        db_t = s.get(Task, t["id"])
        db_t.state = TaskState.COMPLETED
        db_t.stage = TaskStage.IMPLEMENTING
        s.add(db_t)
        s.commit()
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                    json={"target_stage": "done", "review_result": "ok"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "completed"
    assert r.json()["stage"] == "done"


def test_move_done_from_failed_409_not_500():
    # failed 无 T5 入口：明确 409，不再 500
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskState
    t = _mk("movefailed", stage="ready")
    with Session(engine) as s:
        db_t = s.get(Task, t["id"])
        db_t.state = TaskState.FAILED
        s.add(db_t)
        s.commit()
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                    json={"target_stage": "done", "review_result": "x"})
    assert r.status_code == 409, r.text
    assert "done" in r.json()["detail"]
