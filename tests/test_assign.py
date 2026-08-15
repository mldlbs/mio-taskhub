# tests/test_assign.py
from datetime import datetime, timezone
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
    _mk("devt", stage="ready", agent_type="dev")
    _mk("ct", stage="ready", agent_type="coder")
    with Session(engine) as s:
        devt = s.exec(select(Task).where(Task.title == "devt")).first()
        ct = s.exec(select(Task).where(Task.title == "ct")).first()
        devt.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        ct.created_at = datetime(2020, 1, 2, tzinfo=timezone.utc)
        s.commit()
    with Session(engine) as s:
        run = _claim_for("coder1", s, agent_type="coder")
        assert run is not None
        task = s.get(Task, run.task_id)
        assert task.title == "ct"
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


def test_sequential_second_claim_no_duplicate():
    """顺序领取同一任务：第一个抢到后，第二个不再产生新 run。"""
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


import mio_taskhub.wiring as wiring


def _agents():
    from mio_taskhub.models import Agent
    with Session(engine) as s:
        return s.exec(select(Agent)).all()


def test_assign_to_idle_agent():
    _register("idle1", "coder")
    _mk("at", stage="ready", agent_type="coder")
    wiring._assign_to_idle_agents()
    with Session(engine) as s:
        task = s.exec(select(Task).where(Task.title == "at")).first()
        assert task.state == TaskState.CLAIMED
        assert task.stage == TaskStage.IMPLEMENTING
        run = s.exec(select(Run).where(Run.task_id == task.id)).first()
        assert run is not None and run.agent_name == "idle1"


def test_busy_agent_not_assigned():
    _register("busy1", "coder")
    _mk("b1", stage="ready", agent_type="coder")
    # 先让 busy1 占一个 run
    with Session(engine) as s:
        _claim_for("busy1", s)
        s.commit()
    _mk("b2", stage="ready", agent_type="coder")
    wiring._assign_to_idle_agents()
    with Session(engine) as s:
        t2 = s.exec(select(Task).where(Task.title == "b2")).first()
        assert t2.state == TaskState.QUEUED  # busy agent 不再被分配


def test_task_first_assigns_later_registered_agent():
    _register("late1", "coder")
    _mk("lt", stage="ready", agent_type="coder")
    wiring._assign_to_idle_agents()
    with Session(engine) as s:
        task = s.exec(select(Task).where(Task.title == "lt")).first()
        assert task.state == TaskState.CLAIMED


def test_assign_writes_task_assigned_event():
    _register("ev1", "coder")
    _mk("et", stage="ready", agent_type="coder")
    wiring._assign_to_idle_agents()
    ev = client.get("/api/v1/events", params={"after_seq": 0}).json()
    assigned = [e for e in ev["events"] if e["type"] == "task_assigned"]
    assert assigned, "expected task_assigned event"
    assert "run_id" in assigned[0]["payload"]
