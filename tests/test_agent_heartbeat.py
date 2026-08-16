# tests/test_agent_heartbeat.py
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlmodel import Session
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Agent, AgentStatus

client = TestClient(app)


def _agent(name):
    with Session(engine) as s:
        return s.get(Agent, name)


def test_heartbeat_upsert_registers_new_agent():
    r = client.post("/api/v1/agents/heartbeat", json={"name": "hup"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "online"
    a = _agent("hup")
    assert a is not None
    assert a.status == AgentStatus.ONLINE
    assert a.last_heartbeat is not None


def test_heartbeat_refreshes_existing_agent():
    client.post("/api/v1/agents/register", json={"name": "hexist", "agent_type": "t"})
    with Session(engine) as s:
        a = s.get(Agent, "hexist")
        a.status = AgentStatus.OFFLINE
        a.last_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)
        s.add(a); s.commit()
    r = client.post("/api/v1/agents/heartbeat", json={"name": "hexist"})
    assert r.status_code == 200
    a = _agent("hexist")
    assert a.status == AgentStatus.ONLINE
    hb = a.last_heartbeat
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    assert (datetime.now(timezone.utc) - hb).total_seconds() < 10


def test_heartbeat_does_not_write_event():
    client.post("/api/v1/agents/heartbeat", json={"name": "hevent"})
    ev = client.get("/api/v1/events", params={"after_seq": 0}).json()
    types = {e["type"] for e in ev["events"]}
    assert "agent_heartbeat" not in types


def test_heartbeat_requires_name():
    r = client.post("/api/v1/agents/heartbeat", json={})
    assert r.status_code in (400, 422)
