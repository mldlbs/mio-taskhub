"""Metrics endpoint tests."""
from unittest.mock import patch

from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task
from sqlmodel import Session

client = TestClient(app, raise_server_exceptions=False)

def _seed_task():
    """Insert a minimal task so counts > 0."""
    with Session(engine) as s:
        t = Task(id="metrics-test-1", title="seed", state="QUEUED")
        s.add(t)
        s.commit()

def test_metrics_returns_prometheus_format():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "taskhub_tasks_total" in text
    assert "taskhub_uptime_seconds" in text
    assert "# HELP" in text

def test_metrics_counts():
    _seed_task()
    resp = client.get("/metrics")
    text = resp.text
    assert "taskhub_tasks_total{state=" in text

def test_metrics_db_failure():
    """metrics should still return valid output even if DB queries fail."""
    client_local = TestClient(app, raise_server_exceptions=False)

    def fail_exec(*args, **kwargs):
        raise ConnectionError("DB down")

    with patch.object(Session, 'exec', fail_exec):
        resp = client_local.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "taskhub_uptime_seconds" in text
