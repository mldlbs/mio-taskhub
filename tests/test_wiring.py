from datetime import datetime, timezone, timedelta
from sqlmodel import Session
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, Run, TaskState, RunState
import mio_taskhub.wiring as wiring

client = TestClient(app)

def _mk_task(title="t", max_retries=3):
    with Session(engine) as s:
        t = Task(title=title, max_retries=max_retries)
        s.add(t); s.commit(); s.refresh(t)
        return t

def _claim(task_id):
    client.post("/api/v1/agents/register", json={"name": "w-agent", "agent_type": "test"})
    r = client.post("/api/v1/tasks/claim", params={"agent": "w-agent"})
    assert r.status_code == 200
    return r.json()["id"]

def test_timeout_resets_task_to_queued():
    t = _mk_task()
    rid = _claim(t.id)
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=10)
        s.add(run); s.commit()
    wiring._on_timeout(rid, t.id)
    with Session(engine) as s:
        assert s.get(Run, rid).state == RunState.FINISHED
        assert s.get(Task, t.id).state == TaskState.QUEUED

def test_timeout_respects_max_retries():
    t = _mk_task(max_retries=1)
    rid = _claim(t.id)
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.attempt = 1  # already tried once
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=10)
        s.add(run); s.commit()
    wiring._on_timeout(rid, t.id)
    with Session(engine) as s:
        assert s.get(Task, t.id).state == TaskState.FAILED

def test_sweep_skips_fresh_run():
    t = _mk_task()
    rid = _claim(t.id)
    timed_out = []
    sweep = wiring.HeartbeatSweep(get_runs=wiring._get_runs,
                                  on_timeout=lambda r, tid: timed_out.append(r),
                                  on_alive=lambda r: None)
    sweep._sweep()  # run.last_heartbeat is now, should not timeout
    assert rid not in timed_out

def test_scheduler_enqueues_due_task():
    with Session(engine) as s:
        t = Task(title="due", run_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        s.add(t); s.commit(); s.refresh(t)
    due = wiring._get_due_tasks()
    assert any(d["id"] == t.id for d in due)

def test_scheduler_skips_future_task():
    with Session(engine) as s:
        t = Task(title="future", run_at=datetime.now(timezone.utc) + timedelta(hours=1))
        s.add(t); s.commit(); s.refresh(t)
    due = wiring._get_due_tasks()
    assert not any(d["id"] == t.id for d in due)

def test_per_task_timeout_honored():
    # task with timeout_min=1 (60s) — RunInfo must carry timeout_seconds=60
    with Session(engine) as s:
        t = Task(title="short-timeout", timeout_min=1)
        s.add(t); s.commit(); s.refresh(t)
        task_id = t.id
    r = client.post("/api/v1/tasks/claim", params={"agent": "w-agent"})
    assert r.status_code == 200
    rid = r.json()["id"]
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=90)
        s.add(run); s.commit()
    infos = wiring._get_runs()
    info = next(i for i in infos if i.run_id == rid)
    assert info.timeout_seconds == 60

def test_timeout_reset_makes_task_claimable_again():
    from mio_taskhub.models import TaskStage
    with Session(engine) as s:
        t = Task(title="retry-cycle", stage=TaskStage.READY)
        s.add(t); s.commit(); s.refresh(t)
        tid = t.id
    client.post("/api/v1/agents/register", json={"name": "w-agent5", "agent_type": "test"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "w-agent5"}).json()
    rid = claim["id"]
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=10)
        s.add(run); s.commit()
    wiring._on_timeout(rid, tid)
    with Session(engine) as s:
        t2 = s.get(Task, tid)
        assert t2.state == TaskState.QUEUED
        assert t2.stage == TaskStage.READY
    r = client.post("/api/v1/tasks/claim", params={"agent": "w-agent5"})
    assert r.status_code == 200
    assert r.json()["task_id"] == tid

def test_timeout_does_not_clobber_finished_run():
    t = _mk_task()
    rid = _claim(t.id)
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.state = RunState.FINISHED
        run.result = "agent completed"
        s.add(run); s.commit()
    wiring._on_timeout(rid, t.id)  # should be a no-op now
    with Session(engine) as s:
        run = s.get(Run, rid)
        assert run.state == RunState.FINISHED
        assert run.result == "agent completed"
