# tests/test_events_api.py
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from sqlmodel import Session
from mio_taskhub.db import engine
from mio_taskhub.events import emit_event

client = TestClient(app)


def _seed(n=3):
    with Session(engine) as s:
        for i in range(n):
            s.add(emit_event(s, type=f"t{i}", entity="task", entity_id=f"id{i}",
                             payload={"i": i}))
        s.commit()


def test_empty_db():
    r = client.get("/api/v1/events")
    assert r.status_code == 200
    data = r.json()
    assert data["events"] == [] and data["next_seq"] == 0


def test_latest_without_after_seq():
    _seed()
    r = client.get("/api/v1/events")
    data = r.json()
    assert len(data["events"]) == 3
    assert data["next_seq"] == data["events"][-1]["seq"]


def test_incremental_after_seq():
    _seed(5)
    r1 = client.get("/api/v1/events").json()
    last = r1["next_seq"]
    _seed(2)
    r2 = client.get("/api/v1/events", params={"after_seq": last}).json()
    assert len(r2["events"]) == 2
    assert all(e["seq"] > last for e in r2["events"])


def test_after_seq_zero_returns_all():
    _seed(3)
    r = client.get("/api/v1/events", params={"after_seq": 0}).json()
    assert len(r["events"]) == 3
    assert r["events"][0]["seq"] >= 1


def test_payload_parsed_to_dict():
    _seed(1)
    r = client.get("/api/v1/events").json()
    assert r["events"][0]["payload"] == {"i": 0}
