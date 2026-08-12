import pytest
from sqlmodel import select
from mio_taskhub.db import engine, init_db
from mio_taskhub.models import SQLModel, Task, Run, Agent


@pytest.fixture(autouse=True)
def _clean_db():
    SQLModel.metadata.drop_all(engine)
    init_db()
    yield
