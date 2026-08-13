# mio_taskhub/db.py
import os
from sqlmodel import SQLModel, create_engine, Session
from typing import Generator
from sqlalchemy import inspect, text

# Allow overriding the DB path (e.g. tests use a throwaway DB so the
# production data in ~/.mio_taskhub/taskhub.db is never wiped).
DB_PATH = os.environ.get("MIO_TASKHUB_DB")
if DB_PATH:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
else:
    DATA_DIR = os.path.expanduser("~/.mio_taskhub")
    DB_PATH = os.path.join(DATA_DIR, "taskhub.db")
    os.makedirs(DATA_DIR, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)

def _migrate_stage_column(target_engine=None):
    eng = target_engine or engine
    with eng.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("task")}
        if "stage" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'READY'"))
        if "spec_path" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN spec_path VARCHAR NOT NULL DEFAULT ''"))
        if "plan_path" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN plan_path VARCHAR NOT NULL DEFAULT ''"))
        if "review_result" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN review_result VARCHAR NOT NULL DEFAULT ''"))
        # SQLModel stores str-enums by .name (uppercase). Fix any lowercase
        # legacy values (e.g. from a pre-fix migration) to uppercase names.
        conn.execute(text(
            "UPDATE task SET stage = 'BRAINSTORMING' WHERE stage = 'brainstorming'"
        ))
        conn.execute(text("UPDATE task SET stage = 'DESIGN' WHERE stage = 'design'"))
        conn.execute(text("UPDATE task SET stage = 'PLANNING' WHERE stage = 'planning'"))
        conn.execute(text("UPDATE task SET stage = 'READY' WHERE stage = 'ready'"))
        conn.execute(text("UPDATE task SET stage = 'IMPLEMENTING' WHERE stage = 'implementing'"))
        conn.execute(text("UPDATE task SET stage = 'REVIEW' WHERE stage = 'review'"))
        conn.execute(text("UPDATE task SET stage = 'DONE' WHERE stage = 'done'"))
        conn.execute(text("UPDATE task SET stage = 'CANCELLED' WHERE stage = 'cancelled'"))
        # Discussion table: add stage column if missing (existing installs).
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "discussion" in tables:
            dcols = {c["name"] for c in inspect(conn).get_columns("discussion")}
            if "stage" not in dcols:
                conn.execute(text("ALTER TABLE discussion ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'brainstorming'"))
        conn.commit()

def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_stage_column()

def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
