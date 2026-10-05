from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import init_db
from mio_taskhub.models import Task

client = TestClient(app)

def _mk(title, dur, priority=0, depends_on=None, state="queued"):
    from mio_taskhub.db import engine
    from sqlmodel import Session
    with Session(engine) as s:
        from mio_taskhub.models import Task, TaskState
        t = Task(title=title, description="", est_duration_min=dur, priority=priority,
                 depends_on=depends_on, state=TaskState(state))
        s.add(t); s.commit()
        return t.id

def test_night_plan_returns_scheduled_items():
    _mk("A", 60, priority=1)
    _mk("B", 30, priority=0)
    r = client.get("/api/v1/plans/night")
    assert r.status_code == 200
    data = r.json()
    assert data["window_start"] == "22:00"
    assert data["window_end"] == "07:00"
    assert len(data["items"]) == 2
    # priority desc: A first
    assert data["items"][0]["title"] == "A"
    assert data["items"][0]["scheduled_start"] == "22:00"
    assert data["items"][1]["scheduled_start"] == "23:05"  # 60min + 5min buffer

def test_night_plan_respects_dependencies():
    parent = _mk("Parent", 60, priority=0)
    _mk("Child", 30, priority=1, depends_on=parent)
    r = client.get("/api/v1/plans/night")
    items = r.json()["items"]
    titles = [i["title"] for i in items]
    assert titles.index("Parent") < titles.index("Child")

def test_night_plan_overflow():
    _mk("Big", 600, priority=0)
    r = client.get("/api/v1/plans/night")
    assert r.json()["has_overflow"] is True

def test_night_plan_ignores_completed_and_failed():
    _mk("Done", 30, state="completed")
    _mk("Fail", 30, state="failed")
    r = client.get("/api/v1/plans/night")
    assert r.json()["items"] == []

def test_night_plan_empty():
    r = client.get("/api/v1/plans/night")
    assert r.json()["items"] == []
    assert r.json()["has_overflow"] is False

def test_night_plan_custom_window():
    _mk("C", 60, priority=2)
    r = client.get("/api/v1/plans/night", params={"start": "20:00", "end": "21:00"})
    assert r.status_code == 200
    assert r.json()["window_start"] == "20:00"

def test_night_plan_task_ids_filter():
    a = _mk("OnlyA", 30, priority=2)
    _mk("Other", 30, priority=1)
    r = client.get("/api/v1/plans/night", params={"task_ids": a})
    items = r.json()["items"]
    assert [i["task_id"] for i in items] == [a]

def test_night_plan_bad_window_falls_back():
    _mk("D", 30)
    r = client.get("/api/v1/plans/night", params={"start": "not-a-time"})
    assert r.status_code == 200
    assert r.json()["window_start"] == "22:00"
