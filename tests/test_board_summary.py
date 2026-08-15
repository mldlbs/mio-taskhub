from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.models import TaskState

client = TestClient(app)


def _mk(title, stage="ready", priority=0, **kw):
    body = {"title": title, "stage": stage, "priority": priority}
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


def test_empty_db_zero_values():
    r = client.get("/api/v1/board/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["counts"]["brainstorming"] == 0
    assert data["counts"]["done"] == 0
    assert data["ready_queue"] == []
    assert data["running"] == []
    assert data["alerts"] == []
    assert data["recent_done"] == []
    assert any("无待办" in s for s in data["next_steps"])


def test_counts_by_stage():
    _mk("b", "brainstorming")
    _mk("d", "design")
    _mk("r1", "ready")
    _mk("r2", "ready")
    data = client.get("/api/v1/board/summary").json()
    assert data["counts"]["brainstorming"] == 1
    assert data["counts"]["design"] == 1
    assert data["counts"]["ready"] == 2


def test_ready_queue_sorted_by_priority():
    _mk("low", "ready", priority=0)
    _mk("high", "ready", priority=3)
    q = client.get("/api/v1/board/summary").json()["ready_queue"]
    assert [x["title"] for x in q] == ["high", "low"]
    assert q[0]["priority"] == 3


def test_ready_queue_skips_future_run_at():
    _mk("future", "ready", run_at="2999-01-01T00:00:00+00:00")
    _mk("now", "ready", run_at="2020-01-01T00:00:00+00:00")
    q = client.get("/api/v1/board/summary").json()["ready_queue"]
    assert [x["title"] for x in q] == ["now"]


def test_ready_queue_excludes_claimed():
    _mk("claimed", "ready")
    client.post("/api/v1/agents/register", json={"name": "a", "agent_type": "t"})
    client.post("/api/v1/tasks/claim", params={"agent": "a"})
    q = client.get("/api/v1/board/summary").json()["ready_queue"]
    assert q == []


def test_running_with_agent_filter():
    _mk("t1", "ready")
    client.post("/api/v1/agents/register", json={"name": "a", "agent_type": "t"})
    client.post("/api/v1/agents/register", json={"name": "b", "agent_type": "t"})
    _mk("t1", "ready")
    client.post("/api/v1/tasks/claim", params={"agent": "a"})
    client.post("/api/v1/tasks/claim", params={"agent": "b"})

    all_r = client.get("/api/v1/board/summary").json()["running"]
    assert len(all_r) == 2
    only_a = client.get("/api/v1/board/summary", params={"agent": "a"}).json()["running"]
    assert len(only_a) == 1
    assert only_a[0]["claimed_by"] == "a"


def test_recent_done():
    _mk("d1", "ready")
    client.post("/api/v1/agents/register", json={"name": "a", "agent_type": "t"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "a"}).json()
    client.post(f"/api/v1/runs/{claim['id']}/heartbeat", json={"progress": 100})
    client.post(f"/api/v1/runs/{claim['id']}/result", json={"success": True, "result": "OK"})

    data = client.get("/api/v1/board/summary").json()
    assert len(data["recent_done"]) == 1
    assert data["recent_done"][0]["title"] == "d1"
    assert data["recent_done"][0]["completed_at"] is not None


def test_due_at_alert():
    _mk("overdue", "brainstorming", due_at="2020-01-01T00:00:00+00:00")
    data = client.get("/api/v1/board/summary").json()
    assert any("超截止时间" in a["message"] for a in data["alerts"])
    assert any("超过截止时间" in s for s in data["next_steps"])


def test_next_steps_mentions_ready():
    _mk("a", "ready", priority=1)
    data = client.get("/api/v1/board/summary").json()
    assert any("待领取" in s for s in data["next_steps"])
