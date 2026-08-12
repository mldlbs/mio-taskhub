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
