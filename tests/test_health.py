"""Health check endpoint tests."""
from unittest.mock import patch

from fastapi.testclient import TestClient
from mio_taskhub.main import app

client = TestClient(app, raise_server_exceptions=False)

def test_liveness():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

def test_readiness():
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "db" in data

def test_readiness_db_failure():
    """readyz should return 503 when DB is unreachable."""
    client_local = TestClient(app, raise_server_exceptions=False)

    def bad_connect():
        raise ConnectionError("DB down")

    with patch("mio_taskhub.db.engine") as mock_engine:
        mock_engine.connect.side_effect = bad_connect
        resp = client_local.get("/readyz")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["db"] == "error"
