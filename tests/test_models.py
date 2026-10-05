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

def test_idea_version_and_ideachange_models():
    from mio_taskhub.models import Idea, IdeaChange
    i = Idea(title="t")
    assert i.version == 1
    c = IdeaChange(idea_id="x", version=1, diff={"title": {"old": "A", "new": "B"}}, reason="r")
    assert c.id is None  # 自增主键，insert 后赋值
    assert c.diff == {"title": {"old": "A", "new": "B"}}


def test_task_kind_default_normal():
    from mio_taskhub.models import Task, TaskKind
    t = Task(title="t")
    assert t.task_kind == TaskKind.NORMAL
    assert TaskKind.CHANGE_TRACKING.value == "change_tracking"


def test_ideachange_db_roundtrip():
    from mio_taskhub.models import IdeaChange
    from mio_taskhub.db import get_session
    from sqlalchemy import inspect
    from mio_taskhub.db import engine
    s = next(get_session())
    try:
        c1 = IdeaChange(idea_id="i-roundtrip-1", version=1,
                        diff={"title": {"old": "A", "new": "B"}}, reason="r1")
        s.add(c1)
        s.commit()
        s.refresh(c1)
        assert c1.id is not None

        c2 = IdeaChange(idea_id="i-roundtrip-1", version=2,
                        diff={"description": {"old": "x", "new": "y"}}, reason="r2")
        s.add(c2)
        s.commit()
        s.refresh(c2)
        assert c2.id > c1.id  # 自增主键严格递增，供 before_id 游标分页

        # 嵌套 JSON diff 往返
        assert c1.diff == {"title": {"old": "A", "new": "B"}}
        assert c2.diff == {"description": {"old": "x", "new": "y"}}

        # idea_id 索引存在
        idxs = inspect(engine).get_indexes("ideachange")
        assert any("idea_id" in idx["column_names"] for idx in idxs)
    finally:
        s.close()


def test_task_kind_db_roundtrip():
    from mio_taskhub.models import Task, TaskKind
    from mio_taskhub.db import get_session
    from sqlalchemy import text
    s = next(get_session())
    try:
        t = Task(title="roundtrip-ct", task_kind=TaskKind.CHANGE_TRACKING)
        s.add(t)
        s.commit()
        s.refresh(t)
        assert t.task_kind == TaskKind.CHANGE_TRACKING
        # 数据库中以 .name（大写）存储
        raw = s.execute(text("SELECT task_kind FROM task WHERE id=:tid"),
                        {"tid": t.id}).scalar_one()
        assert raw == "CHANGE_TRACKING"
    finally:
        s.close()
