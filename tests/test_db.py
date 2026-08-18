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
