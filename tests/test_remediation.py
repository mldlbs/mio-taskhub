import time
import sys, os
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mio_taskhub'))

@pytest.fixture(autouse=True)
def setup_db():
    from mio_taskhub.db import init_db
    init_db()
    yield

def test_remediation_engine_stalls_detected():
    from mio_taskhub.observability.remediation import RemediationEngine
    engine = RemediationEngine()
    actions = engine.evaluate_stalled_tasks()
    assert isinstance(actions, list)

def test_remediation_engine_thread_restart():
    from mio_taskhub.observability.remediation import RemediationEngine
    engine = RemediationEngine()
    result = engine.restart_stalled_thread("test_thread")
    assert isinstance(result, dict)
    assert "success" in result

def test_remediation_log():
    from mio_taskhub.observability.remediation import RemediationEngine
    engine = RemediationEngine()
    engine.log("test_action", "Test remediation", success=True)
    recent = engine.recent(limit=10)
    assert len(recent) >= 1

def test_remediation_requeue_task():
    from mio_taskhub.observability.remediation import RemediationEngine
    from mio_taskhub.models import Task, TaskState, TaskStage
    from sqlmodel import Session
    from mio_taskhub.db import engine as db_engine
    engine_obj = RemediationEngine()
    with Session(db_engine) as session:
        task = Task(
            id="test-remediation-1",
            title="Test Task",
            description="test",
            state=TaskState.QUEUED,
            stage=TaskStage.BRAINSTORMING,
        )
        session.add(task)
        session.commit()
    result = engine_obj.requeue_task("test-remediation-1")
    assert result["success"] == True
    with Session(db_engine) as session:
        session.delete(session.get(Task, "test-remediation-1"))
        session.commit()
