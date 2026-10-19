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


def test_dep_persist_loop_calls_cleanup():
    """P1-OBS：dep-persist 循环应调用 cleanup，防 depmetricssnapshot 无界增长。"""
    import inspect
    from mio_taskhub import main
    src = inspect.getsource(main)
    assert "_dep_persist.cleanup()" in src, "dep-persist 循环未调用 cleanup"


def test_dep_persist_cleanup_removes_old():
    """cleanup(days=N) 删除超期快照。"""
    from mio_taskhub.db import engine
    from sqlalchemy import text
    persist = DepMetricsPersist()
    with engine.connect() as c:
        c.execute(text("INSERT INTO depmetricssnapshot (ts, dep, op, count, errors, avg_ms, p50_ms, p90_ms, p99_ms, max_ms, error_rate) VALUES (:ts,'sqlite','op',1,0,1,1,1,1,1,0)"), {"ts": time.time() - 40*86400})
        c.commit()
    persist.cleanup(days=7)   # 40 天前的应被删
    with engine.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM depmetricssnapshot WHERE ts < :cutoff"), {"cutoff": time.time() - 7*86400}).fetchone()[0]
    assert n == 0
