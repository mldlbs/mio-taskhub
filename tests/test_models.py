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

def test_task_rich_fields():
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, Subtask, GitRef, HistoryEvent, Discussion, DiscussionMessage
    from mio_taskhub.models import _now
    with Session(engine) as s:
        t = Task(title="rich", acceptance_criteria="AC", due_at=_now(),
                 labels=["blocked"], project="p", workspace="/w", files=["a.py"],
                 deliverables=["report.md"])
        s.add(t); s.commit(); s.refresh(t)
        assert t.labels == ["blocked"] and t.project == "p" and t.files == ["a.py"]

        st = Subtask(task_id=t.id, order=1, title="step1", status="in_progress")
        g = GitRef(task_id=t.id, ref_type="branch", value="feat/x", note="n")
        h = HistoryEvent(task_id=t.id, type="created")
        d = Discussion(task_id=t.id, topic="讨论1", agent="opencode", status="open", summary="s")
        s.add_all([st, g, h, d]); s.commit()

        got = s.get(Subtask, st.id)
        assert got.status == "in_progress" and got.task_id == t.id
        assert s.get(GitRef, g.id).ref_type == "branch"
        assert s.get(HistoryEvent, h.id).type == "created"
        assert s.get(Discussion, d.id).status == "open"

        m = DiscussionMessage(discussion_id=d.id, author="user", role="user", content="hi")
        s.add(m); s.commit()
        assert s.get(DiscussionMessage, m.id).content == "hi"
