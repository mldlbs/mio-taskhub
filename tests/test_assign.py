# tests/test_assign.py
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, Run, RunState, TaskState, TaskStage
from mio_taskhub.api.tasks import _claim_for

client = TestClient(app)


def _mk(title, stage="ready", agent_type=None, **kw):
    body = {"title": title, "stage": stage}
    if agent_type:
        body["target_agent_type"] = agent_type
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


def _register(name, agent_type="t"):
    return client.post("/api/v1/agents/register", json={"name": name, "agent_type": agent_type}).json()


def _runs_for(task_id):
    with Session(engine) as s:
        return s.exec(select(Run).where(Run.task_id == task_id)).all()


def test_claim_for_idempotent_returns_existing_run():
    _register("a1")
    _mk("t1", stage="ready")
    with Session(engine) as s:
        run = _claim_for("a1", s)
        assert run is not None
        run2 = _claim_for("a1", s)
        assert run2 is not None and run2.id == run.id


def test_claim_for_none_when_no_task():
    _register("a2")
    with Session(engine) as s:
        assert _claim_for("a2", s) is None


def test_claim_for_respects_agent_type():
    _register("coder1", "coder")
    _mk("ct", stage="ready", agent_type="coder")
    _mk("devt", stage="ready", agent_type="dev")
    with Session(engine) as s:
        run = _claim_for("coder1", s)
        assert run is not None
        task = s.get(Task, run.task_id)
        assert task.target_agent_type == "coder"


def test_claim_for_does_not_create_duplicate_runs_for_task():
    _register("x1")
    _register("x2")
    _mk("dup", stage="ready")
    with Session(engine) as s:
        r1 = _claim_for("x1", s)
        r2 = _claim_for("x2", s)
        runs = s.exec(select(Run).where(Run.task_id == r1.task_id)).all()
        assert len(runs) == 1
        assert r2 is None or r2.task_id != r1.task_id


def test_concurrent_claim_single_run():
    """模拟两个 agent 并发抢同一任务：条件更新保证只有一个 run。"""
    _register("c1")
    _register("c2")
    _mk("race", stage="ready")
    with Session(engine) as s:
        r1 = _claim_for("c1", s)
        task_id = r1.task_id
        s.commit()
    with Session(engine) as s:
        r2 = _claim_for("c2", s)
        s.commit()
    runs = _runs_for(task_id)
    assert len(runs) == 1


def test_claim_with_agent_type_skips_future_run_at():
    from datetime import datetime, timedelta, timezone
    _register("r1", "rt")
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    _mk("past-t", stage="ready", agent_type="rt", run_at=past)
    _mk("future-t", stage="ready", agent_type="rt", run_at=future, priority=3)
    r = client.post("/api/v1/tasks/claim", params={"agent": "r1", "agent_type": "rt"})
    assert r.status_code == 200
    claimed = r.json()
    with Session(engine) as s:
        task = s.get(Task, claimed["task_id"])
        assert task.title == "past-t"
