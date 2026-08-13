from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage
from sqlmodel import Session

client = TestClient(app)

def _mk(title="t", stage="brainstorming"):
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage))
        s.add(t); s.commit(); s.refresh(t)
        return t.id

def test_advance_to_design_requires_spec_path():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design"})
    assert r.status_code == 422

def test_advance_to_design_requires_discussion():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design", "spec_path": "s.md"})
    assert r.status_code == 422  # no discussion record

def test_advance_to_design_with_spec_and_discussion():
    tid = _mk()
    client.post(f"/api/v1/tasks/{tid}/discussions", json={"topic": "理解", "agent": "a", "summary": "s", "conclusions": "c"})
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 200
    d = r.json()
    assert d["stage"] == "design" and d["spec_path"] == "docs/s.md"

def test_advance_to_planning_requires_plan_path():
    tid = _mk(stage="design")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "planning"})
    assert r.status_code == 422

def test_advance_to_planning_with_plan():
    tid = _mk(stage="design")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "planning", "plan_path": "docs/p.md"})
    assert r.status_code == 200
    assert r.json()["stage"] == "planning"

def test_advance_to_done_requires_review_result():
    tid = _mk(stage="review")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "done"})
    assert r.status_code == 422

def test_advance_to_done_with_review():
    tid = _mk(stage="review")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "done", "review_result": "approved"})
    assert r.status_code == 200
    assert r.json()["stage"] == "done"

def test_advance_illegal_transition():
    tid = _mk()  # brainstorming
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "planning"})
    assert r.status_code == 400

def test_advance_cancelled():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "cancelled"})
    assert r.status_code == 200
    assert r.json()["stage"] == "cancelled"

def test_get_task_returns_stage_fields():
    tid = _mk(stage="planning")
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "planning"
    assert "spec_path" in d and "plan_path" in d and "review_result" in d

def test_list_tasks_filter_by_stage():
    _mk(stage="design")
    _mk(stage="ready")
    r = client.get("/api/v1/tasks", params={"stage": "design"})
    data = r.json()
    assert all(t["stage"] == "design" for t in data)
    assert len(data) == 1

def test_create_task_defaults_brainstorming():
    r = client.post("/api/v1/tasks", json={"title": "new-task"})
    tid = r.json()["id"]
    detail = client.get(f"/api/v1/tasks/{tid}").json()
    assert detail["stage"] == "brainstorming"

def test_create_task_can_set_stage_ready():
    r = client.post("/api/v1/tasks", json={"title": "ready-task", "stage": "ready"})
    tid = r.json()["id"]
    detail = client.get(f"/api/v1/tasks/{tid}").json()
    assert detail["stage"] == "ready"

def test_claim_only_picks_ready():
    ready_id = _mk(title="ready", stage="ready")
    _mk(title="brain", stage="brainstorming")
    r = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent"})
    assert r.status_code == 200
    assert r.json()["task_id"] == ready_id
    d = client.get(f"/api/v1/tasks/{ready_id}").json()
    assert d["stage"] == "implementing"

def test_claim_returns_204_when_no_ready():
    _mk(title="only-brain", stage="brainstorming")
    r = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent2"})
    assert r.status_code == 204

def test_claim_moves_ready_to_implementing():
    tid = _mk(title="to-impl", stage="ready")
    r = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent3"})
    assert r.json()["task_id"] == tid
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "implementing"
    assert d["state"] == "claimed"

def test_submit_result_moves_to_review():
    _mk(title="finish", stage="ready")
    claim = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent4"}).json()
    rid, tid = claim["id"], claim["task_id"]
    r = client.post(f"/api/v1/runs/{rid}/result", json={"success": True, "result": "ok"})
    assert r.status_code == 200
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "review"
    assert d["state"] == "completed"
