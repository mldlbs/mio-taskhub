from mio_taskhub.models import Task, Agent, Run, TaskState, RunState

def test_task_default_state():
    t = Task(title="test", description="")
    assert t.state == TaskState.QUEUED
    assert t.priority == 0
    assert t.max_retries == 3
    assert t.attempt == 0

def test_run_default_state():
    r = Run(task_id="t1", agent_name="hermes")
    assert r.state == RunState.CLAIMED
    assert r.attempt == 1

def test_agent_default_status():
    a = Agent(name="hermes", agent_type="llm")
    assert a.status == "offline"

def test_task_state_machine_valid_transitions():
    assert TaskState.can_transition(TaskState.QUEUED, TaskState.CLAIMED)
    assert not TaskState.can_transition(TaskState.QUEUED, TaskState.COMPLETED)
