import sys, os
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mio_taskhub'))

@pytest.fixture(autouse=True)
def setup_db():
    from mio_taskhub.db import init_db
    init_db()
    yield

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    return TestClient(app)

def test_insights_recent(client):
    resp = client.get("/api/v1/insights?limit=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_insights_acknowledge(client):
    from mio_taskhub.observability.insights import InsightsEngine
    engine = InsightsEngine()
    result = engine.store("test", "API Test", "Testing API", severity="info")
    resp = client.post(f"/api/v1/insights/{result['id']}/acknowledge")
    assert resp.status_code == 200

def test_audit_recent(client):
    resp = client.get("/api/v1/audit?limit=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_audit_stats(client):
    resp = client.get("/api/v1/audit/stats?hours=24")
    assert resp.status_code == 200
    assert "total" in resp.json()

def test_remediation_log(client):
    resp = client.get("/api/v1/remediation?limit=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_observability_summary(client):
    resp = client.get("/api/v1/observability/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "slo_availability" in data or "cpu_percent" in data
