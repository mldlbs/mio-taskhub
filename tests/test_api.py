from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import init_db
from mio_taskhub.models import Task, Agent, Run, TaskState

client = TestClient(app)

def test_create_task():
    r = client.post("/api/v1/tasks", json={"title": "Hello", "description": "world"})
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Hello"
    assert data["state"] == "queued"

def test_list_tasks():
    r = client.get("/api/v1/tasks")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_claim_task_creates_run():
    client.post("/api/v1/tasks", json={"title": "Claim me"})
    client.post("/api/v1/agents/register", json={"name": "agent1", "agent_type": "test"})
    r = client.post("/api/v1/tasks/claim", params={"agent": "agent1"})
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "claimed"
    assert data["agent_name"] == "agent1"

def test_claim_idempotent():
    client.post("/api/v1/tasks", json={"title": "Idempotent"})
    r1 = client.post("/api/v1/tasks/claim", params={"agent": "agent1"})
    r2 = client.post("/api/v1/tasks/claim", params={"agent": "agent1"})
    assert r1.json()["id"] == r2.json()["id"]

def test_heartbeat_updates_progress():
    client.post("/api/v1/tasks", json={"title": "Progress"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "agent1"}).json()
    rid = claim["id"]
    r = client.post(f"/api/v1/runs/{rid}/heartbeat", json={"progress": 50})
    assert r.status_code == 200
    assert r.json()["progress"] == 50
    assert r.json()["state"] == "running"

def test_submit_result_completes_run():
    client.post("/api/v1/tasks", json={"title": "Done"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "agent1"}).json()
    rid = claim["id"]
    client.post(f"/api/v1/runs/{rid}/heartbeat", json={"progress": 100})
    r = client.post(f"/api/v1/runs/{rid}/result", json={"success": True, "result": "OK"})
    assert r.status_code == 200
    assert r.json()["state"] == "finished"

def test_patch_task_rich_fields():
    created = client.post("/api/v1/tasks", json={"title": "Patch me"}).json()
    r = client.patch(f"/api/v1/tasks/{created['id']}", json={
        "acceptance_criteria": "AC", "due_at": "2026-12-31T23:59:59+00:00",
        "labels": ["blocked"], "project": "p", "workspace": "/w",
        "files": ["a.py"], "deliverables": ["report.md"],
    })
    assert r.status_code == 200
    d = r.json()
    assert d["acceptance_criteria"] == "AC"
    assert d["labels"] == ["blocked"]
    assert d["project"] == "p"
    assert d["workspace"] == "/w"
    assert d["files"] == ["a.py"]
    assert d["deliverables"] == ["report.md"]
    assert d["due_at"] is not None

def test_get_task_returns_rich_fields():
    created = client.post("/api/v1/tasks", json={"title": "Rich"}).json()
    client.patch(f"/api/v1/tasks/{created['id']}", json={"labels": ["x"], "project": "p"})
    d = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert d["labels"] == ["x"] and d["project"] == "p"
    assert d["acceptance_criteria"] == ""

def test_subtask_crud():
    created = client.post("/api/v1/tasks", json={"title": "ST"}).json()
    tid = created["id"]
    r = client.post(f"/api/v1/tasks/{tid}/subtasks", json={"title": "s1", "order": 1})
    assert r.status_code == 200
    sid = r.json()["id"]
    assert r.json()["status"] == "pending"

    r2 = client.patch(f"/api/v1/tasks/{tid}/subtasks/{sid}", json={"status": "done"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "done"

    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert len(d["subtasks"]) == 1 and d["subtasks"][0]["title"] == "s1"

def test_subtask_404():
    r = client.post("/api/v1/tasks/nonexistent/subtasks", json={"title": "x"})
    assert r.status_code == 404

def test_gitref_add():
    created = client.post("/api/v1/tasks", json={"title": "GR"}).json()
    r = client.post(f"/api/v1/tasks/{created['id']}/gitrefs",
                    json={"ref_type": "branch", "value": "feat/x", "note": "n"})
    assert r.status_code == 200
    assert r.json()["ref_type"] == "branch"
    d = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert len(d["gitrefs"]) == 1 and d["gitrefs"][0]["value"] == "feat/x"

def test_gitref_404():
    r = client.post("/api/v1/tasks/nope/gitrefs", json={"ref_type": "commit", "value": "abc"})
    assert r.status_code == 404
