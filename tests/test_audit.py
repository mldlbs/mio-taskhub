import time
import sys, os
import pytest

# Ensure test DB env is set before importing anything from mio_taskhub
# (conftest.py handles this, but we re-import to be safe)
from mio_taskhub.db import DB_PATH, init_db
from mio_taskhub.observability.audit import AlertAudit

# Ensure the alertaudit table exists (migrations run via conftest init_db)
init_db()


def test_alert_audit_log_fire():
    audit = AlertAudit()
    audit.log_fire("TestAlert", "warning", message="test fired", metric_value=0.8, threshold=0.5)
    rows = audit.recent(limit=10)
    assert len(rows) >= 1
    row = rows[-1]
    assert row["alert_name"] == "TestAlert"
    assert row["action"] == "fire"
    assert row["metric_value"] == 0.8


def test_alert_audit_log_resolve():
    audit = AlertAudit()
    audit.log_fire("TestAlert2", "warning")
    audit.log_resolve("TestAlert2", message="resolved", duration_seconds=120.5, severity="warning")
    rows = audit.recent(limit=10)
    resolves = [r for r in rows if r["action"] == "resolve"]
    assert len(resolves) >= 1
    assert resolves[-1]["duration_seconds"] == 120.5


def test_alert_audit_recent_limit():
    audit = AlertAudit()
    for i in range(5):
        audit.log_fire(f"Alert_{i}", "info")
    rows = audit.recent(limit=3)
    assert len(rows) == 3


def test_alert_audit_stats():
    audit = AlertAudit()
    audit.log_fire("StatsTest", "warning", metric_value=1.0, threshold=0.5)
    audit.log_resolve("StatsTest", message="ok", duration_seconds=10.0, severity="warning")
    result = audit.stats(hours=1)
    assert "total" in result
    assert "fires" in result
    assert "resolves" in result
    assert result["total"] >= 2
    assert result["fires"] >= 1
    assert result["resolves"] >= 1
