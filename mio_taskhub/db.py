# mio_taskhub/db.py
import os
from sqlmodel import SQLModel, create_engine, Session
from typing import Generator

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

def init_db():
    SQLModel.metadata.create_all(engine)

def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
