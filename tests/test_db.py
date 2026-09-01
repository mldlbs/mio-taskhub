# tests/test_db.py
from mio_taskhub.db import get_session, init_db, engine
from mio_taskhub.models import Task, Agent, TaskState, Idea, IdeaHistory, IdeaStatus, TaskKind, IdeaChange
from sqlmodel import select, text

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

def test_create_all_creates_new_tables():
    from sqlalchemy import inspect
    from mio_taskhub.db import engine
    tables = set(inspect(engine).get_table_names())
    for name in ["subtask", "gitref", "historyevent", "discussion", "discussionmessage"]:
        assert name in tables, f"missing table {name}"

def test_stage_column_added_to_existing_table():
    from sqlalchemy import inspect
    from mio_taskhub.db import engine
    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("task")}
    assert "stage" in cols

def test_discussion_stage_column_migrated_on_old_table():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE task (id VARCHAR PRIMARY KEY, title VARCHAR, state VARCHAR)"
            ))
            conn.execute(text(
                "CREATE TABLE discussion (id VARCHAR PRIMARY KEY, task_id VARCHAR, "
                "topic VARCHAR, agent VARCHAR, status VARCHAR, summary VARCHAR, "
                "conclusions VARCHAR, started_at VARCHAR, ended_at VARCHAR)"
            ))
            conn.execute(text("INSERT INTO discussion (id, task_id, topic) VALUES ('d1', 't1', 'topic')"))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("discussion")}
            assert "stage" in cols
            row = conn.execute(text("SELECT stage FROM discussion WHERE id='d1'")).fetchone()
            assert row[0] == "brainstorming"  # Discussion.stage is a plain str (lowercase)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

def test_migrate_stage_column_on_old_table():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE task (id VARCHAR PRIMARY KEY, title VARCHAR, state VARCHAR)"
            ))
            conn.execute(text("INSERT INTO task (id, title, state) VALUES ('old1', 'legacy', 'queued')"))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("task")}
            assert {"stage", "spec_path", "plan_path", "review_result"} <= cols
            row = conn.execute(text("SELECT stage, spec_path, plan_path, review_result FROM task WHERE id='old1'")).fetchone()
            assert row[0] == "READY"  # SQLModel str-enum stored by .name (uppercase)
            assert row[1] == "" and row[2] == "" and row[3] == ""
        # idempotent: second run is a no-op
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("task")}
            assert {"stage", "spec_path", "plan_path", "review_result"} <= cols
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

def test_migrate_depends_on_and_idea_id():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE task (id VARCHAR PRIMARY KEY, title VARCHAR, state VARCHAR, "
                "depends_on VARCHAR)"
            ))
            conn.execute(text(
                "INSERT INTO task (id, title, state, depends_on) VALUES "
                "('t1','a','queued','parent'), ('t2','b','queued','[\"x\",\"y\"]'), "
                "('t3','c','queued',NULL), ('t4','d','queued','[')"
            ))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("task")}
            assert "idea_id" in cols
            rows = {r[0]: r[1] for r in conn.execute(text("SELECT id, depends_on FROM task"))}
            assert rows["t1"] == '["parent"]'
            assert rows["t2"] == '["x","y"]'
            assert rows["t3"] == "[]"
            assert rows["t4"] == "[]"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

def test_migrate_event_entity_columns():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE event (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR, "
                "type VARCHAR, payload VARCHAR, at VARCHAR)"
            ))
            conn.execute(text("INSERT INTO event (run_id, type) VALUES ('r1', 'heartbeat')"))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("event")}
            assert "entity" in cols and "entity_id" in cols
            row = conn.execute(text("SELECT entity, entity_id FROM event WHERE run_id='r1'")).fetchone()
            assert row[0] == "run" and row[1] == "r1"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

def test_migrate_change_tracking_unique_index():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.exc import IntegrityError
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE task (id VARCHAR PRIMARY KEY, title VARCHAR, state VARCHAR)"
            ))
            conn.commit()
        _migrate_stage_column(eng)
        # 迁移后索引存在
        with eng.connect() as conn:
            idx = {r[1] for r in conn.execute(text("PRAGMA index_list('task')"))}
            assert "uq_task_active_change_tracking" in idx
            conn.execute(text(
                "INSERT INTO task (id, title, state, task_kind, idea_id) VALUES "
                "('ct1', 'a', 'QUEUED', 'CHANGE_TRACKING', 'i1')"
            ))
            conn.commit()
        # 同 idea 第二条活跃变更任务 → 违反唯一索引
        with eng.connect() as conn:
            try:
                conn.execute(text(
                    "INSERT INTO task (id, title, state, task_kind, idea_id) VALUES "
                    "('ct2', 'b', 'QUEUED', 'CHANGE_TRACKING', 'i1')"
                ))
                conn.commit()
                raise AssertionError("expected IntegrityError for duplicate active change tracking task")
            except IntegrityError:
                pass
        # 终态（COMPLETED）不与活跃冲突；不同 idea 不冲突
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO task (id, title, state, task_kind, idea_id) VALUES "
                "('ct3', 'c', 'COMPLETED', 'CHANGE_TRACKING', 'i1')"
            ))
            conn.execute(text(
                "INSERT INTO task (id, title, state, task_kind, idea_id) VALUES "
                "('ct4', 'd', 'QUEUED', 'CHANGE_TRACKING', 'i2')"
            ))
            conn.commit()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

def test_migrate_idea_version_and_task_kind_columns():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text("CREATE TABLE idea (id VARCHAR PRIMARY KEY, title VARCHAR)"))
            conn.execute(text("INSERT INTO idea (id, title) VALUES ('i1', 't')"))
            conn.execute(text(
                "CREATE TABLE task (id VARCHAR PRIMARY KEY, title VARCHAR, state VARCHAR)"
            ))
            conn.execute(text("INSERT INTO task (id, title, state) VALUES ('t1', 'x', 'queued')"))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            icols = {c["name"] for c in inspect(conn).get_columns("idea")}
            assert "version" in icols
            v = conn.execute(text("SELECT version FROM idea WHERE id='i1'")).fetchone()[0]
            assert v == 1
            tcols = {c["name"] for c in inspect(conn).get_columns("task")}
            assert "task_kind" in tcols
            k = conn.execute(text("SELECT task_kind FROM task WHERE id='t1'")).fetchone()[0]
            assert k == "NORMAL"  # SQLModel str-enum 按 .name 存储（大写）
        _migrate_stage_column(eng)  # 幂等
        with eng.connect() as conn:
            icols = {c["name"] for c in inspect(conn).get_columns("idea")}
            assert "version" in icols
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_migrate_idea_last_reviewed_and_review_count():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE idea (id VARCHAR PRIMARY KEY, title VARCHAR, status VARCHAR, "
                "version INTEGER, project VARCHAR, labels VARCHAR, created_at VARCHAR, updated_at VARCHAR)"
            ))
            conn.execute(text("INSERT INTO idea (id, title) VALUES ('i1', 't')"))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            icols = {c["name"] for c in inspect(conn).get_columns("idea")}
            assert "last_reviewed_at" in icols
            assert "review_count" in icols
            # review_count 默认 0
            rc = conn.execute(text("SELECT review_count FROM idea WHERE id='i1'")).fetchone()[0]
            assert rc == 0
            # last_reviewed_at 允许 NULL
            lr = conn.execute(text("SELECT last_reviewed_at FROM idea WHERE id='i1'")).fetchone()[0]
            assert lr is None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_migrate_ideahistory_actor_content_columns():
    """旧库 ideahistory 表补齐 actor/content 列。"""
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE ideahistory (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "idea_id VARCHAR, kind VARCHAR, reasoning VARCHAR, extra VARCHAR, at VARCHAR)"
            ))
            conn.execute(text(
                "INSERT INTO ideahistory (idea_id, kind) VALUES ('i1', 'review')"
            ))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            icols = {c["name"] for c in inspect(conn).get_columns("ideahistory")}
            assert "actor" in icols
            assert "content" in icols
            row = conn.execute(text("SELECT actor, content FROM ideahistory WHERE idea_id='i1'")).fetchone()
            assert row[0] == "" and row[1] == ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_wal_mode_enabled():
    """PRAGMA journal_mode should be WAL."""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()
        assert mode == "wal", f"Expected WAL, got {mode}"

def test_synchronous_normal():
    """PRAGMA synchronous should be NORMAL."""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA synchronous"))
        val = result.scalar()
        assert val == 1, f"Expected NORMAL (1), got {val}"

def test_connection_pool_is_static():
    """Engine should be configured with a pool."""
    from sqlalchemy.pool import StaticPool
    pool = engine.pool
    assert isinstance(pool, StaticPool)


def test_transition_idea_status_records_history():
    """transition_idea_status 为唯一入口：状态变更 + kind=status 历史记录。"""
    from mio_taskhub.api.ideas import transition_idea_status
    init_db()
    s = next(get_session())
    try:
        i = Idea(title="状态流转测试")
        s.add(i)
        s.commit()
        s.refresh(i)

        # new -> fermenting
        transition_idea_status(i, IdeaStatus.FERMENTING, s, actor="user", source="manual")
        s.commit()
        s.refresh(i)

        assert i.status == IdeaStatus.FERMENTING
        hist = s.exec(select(IdeaHistory).where(IdeaHistory.idea_id == i.id).order_by(IdeaHistory.at)).all()
        assert len(hist) == 1
        assert hist[0].kind == "status"
        assert hist[0].extra["from"] == "new"
        assert hist[0].extra["to"] == "fermenting"
        assert hist[0].extra["source"] == "manual"
        assert hist[0].reasoning is None  # manual transition no reasoning

        # fermenting -> formed
        transition_idea_status(i, IdeaStatus.FORMED, s, actor="agent", source="review")
        s.commit()

        hist = s.exec(select(IdeaHistory).where(IdeaHistory.idea_id == i.id).order_by(IdeaHistory.at)).all()
        assert len(hist) == 2
        assert hist[1].kind == "status"
        assert hist[1].extra["from"] == "fermenting"
        assert hist[1].extra["to"] == "formed"
        assert hist[1].extra["source"] == "review"
    finally:
        s.close()


def test_transition_invalid_raises():
    """非法流转抛 422，状态不变，无历史记录。"""
    from mio_taskhub.api.ideas import transition_idea_status
    from fastapi import HTTPException
    init_db()
    s = next(get_session())
    try:
        i = Idea(title="非法流转")
        s.add(i)
        s.commit()
        s.refresh(i)

        try:
            transition_idea_status(i, IdeaStatus.FORMED, s, actor="user")  # 跳级 new->formed
            raise AssertionError("expected 422")
        except HTTPException as e:
            assert e.status_code == 422

        # 状态未变
        s.refresh(i)
        assert i.status == IdeaStatus.NEW
        hist = s.exec(select(IdeaHistory).where(IdeaHistory.idea_id == i.id)).all()
        assert len(hist) == 0
    finally:
        s.close()
