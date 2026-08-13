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

def test_create_task_rich_fields():
    r = client.post("/api/v1/tasks", json={
        "title": "Rich", "acceptance_criteria": "AC",
        "due_at": "2026-12-31T23:59:59+00:00",
        "labels": ["new"], "project": "p", "workspace": "/w",
        "files": ["a.py"], "deliverables": ["r.md"],
    })
    assert r.status_code == 200
    d = client.get(f"/api/v1/tasks/{r.json()['id']}").json()
    assert d["acceptance_criteria"] == "AC"
    assert d["labels"] == ["new"] and d["project"] == "p"
    assert d["files"] == ["a.py"] and d["deliverables"] == ["r.md"]
    assert d["due_at"] is not None

def test_create_task_invalid_due_at_returns_400():
    r = client.post("/api/v1/tasks", json={"title": "Bad", "due_at": "not-a-date"})
    assert r.status_code == 400

def test_list_tasks():
    r = client.get("/api/v1/tasks")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_claim_task_creates_run():
    client.post("/api/v1/tasks", json={"title": "Claim me", "stage": "ready"})
    client.post("/api/v1/agents/register", json={"name": "agent1", "agent_type": "test"})
    r = client.post("/api/v1/tasks/claim", params={"agent": "agent1"})
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "claimed"
    assert data["agent_name"] == "agent1"

def test_claim_idempotent():
    client.post("/api/v1/tasks", json={"title": "Idempotent", "stage": "ready"})
    r1 = client.post("/api/v1/tasks/claim", params={"agent": "agent1"})
    r2 = client.post("/api/v1/tasks/claim", params={"agent": "agent1"})
    assert r1.json()["id"] == r2.json()["id"]

def test_heartbeat_updates_progress():
    client.post("/api/v1/tasks", json={"title": "Progress", "stage": "ready"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "agent1"}).json()
    rid = claim["id"]
    r = client.post(f"/api/v1/runs/{rid}/heartbeat", json={"progress": 50})
    assert r.status_code == 200
    assert r.json()["progress"] == 50
    assert r.json()["state"] == "running"

def test_submit_result_completes_run():
    client.post("/api/v1/tasks", json={"title": "Done", "stage": "ready"})
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

def test_history_add():
    created = client.post("/api/v1/tasks", json={"title": "Hist"}).json()
    r = client.post(f"/api/v1/tasks/{created['id']}/history",
                    json={"type": "discussion", "payload": {"msg": "hi"}})
    assert r.status_code == 200
    assert r.json()["type"] == "discussion"
    d = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert len(d["history"]) == 1

def test_history_404():
    r = client.post("/api/v1/tasks/nope/history", json={"type": "x"})
    assert r.status_code == 404

def test_discussion_add_and_close():
    created = client.post("/api/v1/tasks", json={"title": "Disc"}).json()
    tid = created["id"]
    r = client.post(f"/api/v1/tasks/{tid}/discussions", json={
        "topic": "如何实现", "agent": "opencode",
        "summary": "讨论了方案", "conclusions": "用方案B",
        "messages": [{"author": "opencode", "role": "assistant", "content": "建议方案B"}],
    })
    assert r.status_code == 200
    did = r.json()["id"]
    assert r.json()["status"] == "closed"
    assert r.json()["conclusions"] == "用方案B"

    d = client.get(f"/api/v1/tasks/{tid}/discussions").json()
    assert len(d["discussions"]) == 1
    assert d["discussions"][0]["messages"][0]["content"] == "建议方案B"

def test_discussion_404():
    r = client.post("/api/v1/tasks/nope/discussions", json={"topic": "t"})
    assert r.status_code == 404

def test_claim_carries_context():
    client.post("/api/v1/tasks", json={"title": "Ctx", "stage": "ready"})
    r = client.post("/api/v1/tasks/claim", params={
        "agent": "ctx-agent",
        "project": "p", "workspace": "/w", "files": "a.py,b.py",
    })
    assert r.status_code == 200
    tid = r.json()["task_id"]
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["project"] == "p"
    assert d["workspace"] == "/w"
    assert d["files"] == ["a.py", "b.py"]

def test_claim_does_not_overwrite_context():
    client.post("/api/v1/tasks", json={"title": "Keep", "project": "preset", "stage": "ready"})
    r1 = client.post("/api/v1/tasks/claim", params={"agent": "ctx1", "project": "p1"})
    assert r1.status_code == 200
    tid = r1.json()["task_id"]
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["project"] == "preset"  # preset wins, not overwritten by claim

def test_subtask_cross_task_guard():
    t1 = client.post("/api/v1/tasks", json={"title": "T1"}).json()
    t2 = client.post("/api/v1/tasks", json={"title": "T2"}).json()
    st = client.post(f"/api/v1/tasks/{t1['id']}/subtasks", json={"title": "s"}).json()
    r = client.patch(f"/api/v1/tasks/{t2['id']}/subtasks/{st['id']}", json={"status": "done"})
    assert r.status_code == 404

def test_subtask_invalid_status_returns_400():
    t = client.post("/api/v1/tasks", json={"title": "Bad"}).json()
    r = client.post(f"/api/v1/tasks/{t['id']}/subtasks", json={"title": "s", "status": "bogus"})
    assert r.status_code == 400

def test_gitref_invalid_type_returns_400():
    t = client.post("/api/v1/tasks", json={"title": "BadG"}).json()
    r = client.post(f"/api/v1/tasks/{t['id']}/gitrefs", json={"ref_type": "nope", "value": "x"})
    assert r.status_code == 400

def test_create_task_accepts_run_at_string():
    r = client.post("/api/v1/tasks", json={"title": "Sched", "run_at": "2026-12-31T23:59:59+00:00"})
    assert r.status_code == 200

def test_create_task_invalid_run_at_returns_400():
    r = client.post("/api/v1/tasks", json={"title": "Bad", "run_at": "not-a-date"})
    assert r.status_code == 400

def test_claim_skips_future_run_at():
    client.post("/api/v1/tasks", json={"title": "Future", "run_at": "2099-01-01T00:00:00+00:00", "priority": 3, "stage": "ready"})
    r = client.post("/api/v1/tasks/claim", params={"agent": "runat-agent"})
    assert r.status_code == 204  # no claimable task

def test_claim_picks_due_run_at():
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    client.post("/api/v1/tasks", json={"title": "Past", "run_at": past, "priority": 2, "stage": "ready"})
    r = client.post("/api/v1/tasks/claim", params={"agent": "runat-agent2"})
    assert r.status_code == 200
    assert r.json()["state"] == "claimed"

def test_create_task_run_at_with_offset_normalized_to_utc():
    r = client.post("/api/v1/tasks", json={"title": "TZ", "run_at": "2026-12-31T23:59:59+08:00"})
    assert r.status_code == 200
    d = client.get(f"/api/v1/tasks/{r.json()['id']}").json()
    # +08:00 23:59:59 == 15:59:59 UTC
    assert d["run_at"] == "2026-12-31T15:59:59+00:00"

def test_create_task_naive_run_at_treated_as_utc():
    r = client.post("/api/v1/tasks", json={"title": "Naive", "run_at": "2026-12-31T10:00:00"})
    assert r.status_code == 200
    d = client.get(f"/api/v1/tasks/{r.json()['id']}").json()
    assert d["run_at"] == "2026-12-31T10:00:00+00:00"

def test_discussion_defaults_stage_brainstorming():
    created = client.post("/api/v1/tasks", json={"title": "DS"}).json()
    r = client.post(f"/api/v1/tasks/{created['id']}/discussions",
                    json={"topic": "t", "agent": "a"})
    assert r.status_code == 200
    d = r.json()
    assert d["stage"] == "brainstorming"

def test_discussion_with_stage():
    created = client.post("/api/v1/tasks", json={"title": "DS2"}).json()
    r = client.post(f"/api/v1/tasks/{created['id']}/discussions",
                    json={"topic": "t", "agent": "a", "stage": "review"})
    assert r.status_code == 200
    assert r.json()["stage"] == "review"

def test_discussion_list_includes_stage():
    created = client.post("/api/v1/tasks", json={"title": "DS3"}).json()
    client.post(f"/api/v1/tasks/{created['id']}/discussions",
                json={"topic": "t", "agent": "a", "stage": "planning"})
    r = client.get(f"/api/v1/tasks/{created['id']}/discussions")
    assert r.status_code == 200
    assert r.json()["discussions"][0]["stage"] == "planning"

def test_discussion_detail_includes_stage():
    created = client.post("/api/v1/tasks", json={"title": "DS4"}).json()
    client.post(f"/api/v1/tasks/{created['id']}/discussions",
                json={"topic": "t", "agent": "a", "stage": "design"})
    r = client.get(f"/api/v1/tasks/{created['id']}")
    assert r.status_code == 200
    assert r.json()["discussions"][0]["stage"] == "design"
