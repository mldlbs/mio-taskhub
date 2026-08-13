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
