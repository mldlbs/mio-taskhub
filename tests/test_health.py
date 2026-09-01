"""Health check endpoint tests."""
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
