import time
import sys, os
import pytest
from mio_taskhub.db import init_db
from mio_taskhub.observability.dep_persist import DepMetricsPersist
from mio_taskhub.observability.dep_metrics import DepMetrics, _lock, _data


@pytest.fixture(autouse=True)
def _ensure_db():
    init_db()
    yield
    DepMetrics.reset()


def test_dep_persist_recent_empty():
    persist = DepMetricsPersist()
    rows = persist.recent(limit=10)
    assert isinstance(rows, list)


def test_dep_persist_recent_limit():
    persist = DepMetricsPersist()
    rows = persist.recent(limit=5)
    assert len(rows) <= 5


def test_dep_persist_cleanup():
    persist = DepMetricsPersist()
    persist.cleanup(days=0)  # cleanup everything
    rows = persist.recent(limit=100)
    assert len(rows) == 0


def test_dep_persist_snapshot():
    persist = DepMetricsPersist()
    persist.cleanup(days=0)

    # Reset in-memory metrics so only our test data is snapshotted
    DepMetrics.reset()
    DepMetrics.record("sqlite", "query", 10.5, success=True)
    DepMetrics.record("sqlite", "query", 20.0, success=True)
    DepMetrics.record("sqlite", "query", 5.0, success=False)

    before = time.time()
    persist.snapshot()
    after = time.time()

    rows = persist.recent(limit=100)
    our_rows = [r for r in rows if before <= r["ts"] <= after]
    assert len(our_rows) == 1
    row = our_rows[0]
    assert row["dep"] == "sqlite"
    assert row["op"] == "query"
    assert row["count"] == 3
    assert row["errors"] == 1
    assert row["error_rate"] == pytest.approx(1 / 3, abs=0.01)
