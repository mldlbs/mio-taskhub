import os
import tempfile
import pytest

# Isolate tests from the production DB: point at a throwaway sqlite file
# BEFORE importing mio_taskhub.db (which builds the engine at import time).
_TEST_DB = os.environ.get(
    "MIO_TEST_DB_PATH",
    os.path.join(tempfile.gettempdir(), "mio_taskhub_test.db"),
)
os.environ["MIO_TASKHUB_DB"] = _TEST_DB
if os.path.exists(_TEST_DB):
    os.remove(_TEST_DB)

# 隔离 Mio policy check：默认指向不存在的 MIO_HOME → fail-open、无子进程。
# 需要真实/伪造 Mio 的用例（test_mio_runtime / test_policy_guard）自行覆盖。
os.environ.setdefault(
    "MIO_HOME",
    os.path.join(tempfile.gettempdir(), "mio_home_tests_missing"),
)

from sqlmodel import select  # noqa: E402
from mio_taskhub.db import engine, init_db  # noqa: E402
from mio_taskhub.models import SQLModel, Task, Run, Agent  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    SQLModel.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear global rate limiter buckets between tests."""
    from mio_taskhub.middleware import _rate_buckets
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()
