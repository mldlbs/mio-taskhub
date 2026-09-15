import time
import sys, os
from mio_taskhub.db import init_db
from mio_taskhub.observability.dep_persist import DepMetricsPersist

init_db()


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
