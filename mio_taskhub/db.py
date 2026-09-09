# mio_taskhub/db.py
import os
from sqlmodel import SQLModel, create_engine, Session
from typing import Generator
from sqlalchemy import event, text
from sqlalchemy.pool import StaticPool

# Allow overriding the DB path (e.g. tests use a throwaway DB so the
# production data in ~/.mio_taskhub/taskhub.db is never wiped).
BUSY_TIMEOUT_MS = 5000

DB_PATH = os.environ.get("MIO_TASKHUB_DB")
if DB_PATH:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
else:
    DATA_DIR = os.path.expanduser("~/.mio_taskhub")
    DB_PATH = os.path.join(DATA_DIR, "taskhub.db")
    os.makedirs(DATA_DIR, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

def _migrate_stage_column(target_engine=None):
    from mio_taskhub.migrations import run_migrations
    run_migrations(target_engine)

def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_stage_column()
    # 首次运行播种常用模板（表为空时才插入，幂等）
    from mio_taskhub.seed import seed_common_templates
    with Session(engine) as s:
        seed_common_templates(s)

def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

def check_connection() -> dict:
    """Verify DB connectivity. Returns {"ok": bool, "error": str|None}."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# Install auto-broadcast hooks (broadcasts Event objects on successful commit)
from mio_taskhub.events import install_broadcast_hooks
install_broadcast_hooks(engine)
