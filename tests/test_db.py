# tests/test_db.py
from mio_taskhub.db import get_session, init_db, engine
from mio_taskhub.models import Task, Agent, TaskState
from sqlmodel import select

def test_init_db_and_create_task():
    init_db()
    s = next(get_session())
    try:
        t = Task(title="test task", description="desc")
        s.add(t)
        s.commit()
        s.refresh(t)
        assert t.id is not None
        assert t.state == TaskState.QUEUED
    finally:
        s.close()

def test_crud_agent():
    s = next(get_session())
    try:
        existing = s.exec(select(Agent).where(Agent.name == "test-agent")).first()
        if existing:
            s.delete(existing)
            s.commit()
        a = Agent(name="test-agent", agent_type="test")
        s.add(a)
        s.commit()
        found = s.exec(select(Agent).where(Agent.name == "test-agent")).first()
        assert found is not None
        assert found.agent_type == "test"
    finally:
        s.close()
