from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import init_db

client = TestClient(app)

def test_full_task_lifecycle():
    """create -> register agent -> claim -> heartbeat -> submit result -> verify completed"""
    # Create
    r = client.post("/api/v1/tasks", json={"title": "E2E", "priority": 5, "stage": "ready"})
    assert r.status_code == 200
    # Register agent
    client.post("/api/v1/agents/register", json={"name": "e2e-agent", "agent_type": "test"})
    # Claim
    claim = client.post("/api/v1/tasks/claim", params={"agent": "e2e-agent"})
    assert claim.status_code == 200
    rid = claim.json()["id"]
    # Heartbeat
    hb = client.post(f"/api/v1/runs/{rid}/heartbeat", json={"progress": 50, "checkpoint": "step2"})
    assert hb.json()["state"] == "running"
    # Submit result
    res = client.post(f"/api/v1/runs/{rid}/result", json={"success": True, "result": "done"})
    assert res.json()["state"] == "finished"
    # Task state should be completed
    tasks = client.get("/api/v1/tasks").json()
    e2e = [t for t in tasks if t["title"] == "E2E"][0]
    assert e2e["state"] == "completed"

def test_no_tasks_returns_204():
    client.post("/api/v1/agents/register", json={"name": "lonely", "agent_type": "test"})
    r = client.post("/api/v1/tasks/claim", params={"agent": "lonely"})
    assert r.status_code == 204
