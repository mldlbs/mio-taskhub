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

def test_task_stage_and_artifacts():
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskStage
    with Session(engine) as s:
        t = Task(title="stage-test", stage=TaskStage.DESIGN,
                 spec_path="docs/x.md", plan_path="docs/y.md", review_result="ok")
        s.add(t); s.commit(); s.refresh(t)
        assert t.stage == TaskStage.DESIGN
        assert t.spec_path == "docs/x.md" and t.plan_path == "docs/y.md"
        assert t.review_result == "ok"

def test_task_default_stage_is_ready():
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskStage
    with Session(engine) as s:
        t = Task(title="default-stage")
        s.add(t); s.commit(); s.refresh(t)
        assert t.stage == TaskStage.READY

def test_stage_transition_table():
    from mio_taskhub.models import TaskStage
    assert TaskStage.can_advance(TaskStage.BRAINSTORMING, TaskStage.DESIGN)
    assert TaskStage.can_advance(TaskStage.DESIGN, TaskStage.PLANNING)
    assert TaskStage.can_advance(TaskStage.PLANNING, TaskStage.READY)
    assert TaskStage.can_advance(TaskStage.READY, TaskStage.IMPLEMENTING)
    assert TaskStage.can_advance(TaskStage.IMPLEMENTING, TaskStage.REVIEW)
    assert TaskStage.can_advance(TaskStage.REVIEW, TaskStage.DONE)
    assert not TaskStage.can_advance(TaskStage.BRAINSTORMING, TaskStage.PLANNING)
    assert not TaskStage.can_advance(TaskStage.DONE, TaskStage.READY)
    assert TaskStage.can_advance(TaskStage.BRAINSTORMING, TaskStage.CANCELLED)
    assert TaskStage.can_advance(TaskStage.REVIEW, TaskStage.CANCELLED)
