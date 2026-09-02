"""Memory Gateway 测试：mock MCPClient，覆盖 4 个端点 + 错误处理。"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

import mio_taskhub.memory_gateway as mg
import mio_taskhub.api.memory as api_memory
from mio_taskhub.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    """每个测试提供独立 mock client，注入到 memory_gateway 和 api.memory。"""
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.call.return_value = {}
    monkeypatch.setattr(mg, "get_client", lambda: mock_client)
    monkeypatch.setattr(api_memory, "get_client", lambda: mock_client)
    mg.reset_metrics()
    return mock_client


def test_health_ok(_patch_client):
    _patch_client.is_available.return_value = True
    r = client.get("/api/memory/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["mcp_available"] is True


def test_query_success(_patch_client):
    _patch_client.call.return_value = [{"id": "1", "kind": "note", "context": "x"}]
    r = client.get("/api/memory/query?kind=note&limit=5")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert data[0]["kind"] == "note"
    _patch_client.call.assert_called_once()
    args, _ = _patch_client.call.call_args
    assert args[0] == "mio_memory_query"
    assert args[1]["kind"] == "note"
    assert args[1]["limit"] == 5


def test_query_unavailable_returns_503(_patch_client):
    _patch_client.call.side_effect = mg.MCPUnavailable("not running")
    r = client.get("/api/memory/query")
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "memory_unavailable"


def test_query_timeout_returns_504(_patch_client):
    _patch_client.call.side_effect = mg.MCPTimeout("5s")
    r = client.get("/api/memory/query")
    assert r.status_code == 504
    assert r.json()["detail"]["error"] == "memory_timeout"


def test_record_success(_patch_client):
    _patch_client.call.return_value = {}
    r = client.post("/api/memory/record", json={
        "kind": "decision", "context": "spec done", "payload": {"id": "x"}
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    _patch_client.call.assert_called_once()
    args, _ = _patch_client.call.call_args
    assert args[0] == "mio_memory_record"
    assert args[1]["kind"] == "decision"


def test_record_invalid_payload_returns_422(_patch_client):
    r = client.post("/api/memory/record", json={"context": "missing kind"})
    assert r.status_code == 422
    _patch_client.call.assert_not_called()


def test_policy_check_denied(_patch_client):
    _patch_client.call.return_value = {"allowed": False, "reason": "high risk"}
    r = client.post("/api/memory/policy/check", json={
        "operation": "delete_task", "context": {"task_id": "x"}
    })
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is False
    assert body["reason"] == "high risk"
    args, _ = _patch_client.call.call_args
    assert args[0] == "mio_policy_check"


def test_observer_ingest_success(_patch_client):
    _patch_client.call.return_value = {}
    r = client.post("/api/memory/observer/ingest", json={
        "trace_id": "abc123",
        "event_type": "task_outcome",
        "payload": {"task": "t1"},
        "outcome": "success",
    })
    assert r.status_code == 200
    args, _ = _patch_client.call.call_args
    assert args[0] == "mio_observer_ingest"
    assert args[1]["trace_id"] == "abc123"
    assert args[1]["outcome"] == "success"


def test_observer_ingest_rpc_error_returns_502(_patch_client):
    _patch_client.call.side_effect = mg.MCPRPCError("malformed")
    r = client.post("/api/memory/observer/ingest", json={
        "trace_id": "x", "event_type": "y", "payload": {}
    })
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "memory_rpc_error"


def test_query_limit_out_of_range(_patch_client):
    r = client.get("/api/memory/query?limit=999")
    assert r.status_code == 422


def test_record_kind_required(_patch_client):
    r = client.post("/api/memory/record", json={"context": "no kind"})
    assert r.status_code == 422


# ----- v2: experience_reuse + metrics + WS broadcast -----

def test_experience_reuse_success(_patch_client):
    _patch_client.call.return_value = {"ok": True}
    r = client.post("/api/memory/experience/reuse", json={
        "sourceAgent": "opencode",
        "targetAgent": "codex",
        "experienceId": "exp-001",
        "reuse": True,
        "behaviorChanged": True,
        "outcomeImproved": True,
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    args, _ = _patch_client.call.call_args
    assert args[0] == "mio_experience_reuse"
    assert args[1]["experienceId"] == "exp-001"


def test_experience_reuse_required_fields(_patch_client):
    r = client.post("/api/memory/experience/reuse", json={
        "sourceAgent": "x", "targetAgent": "y"
    })
    assert r.status_code == 422
    _patch_client.call.assert_not_called()


def test_metrics_counter_records_ok(_patch_client):
    _patch_client.call.return_value = {"ok": True}
    client.get("/api/memory/query?kind=note")
    client.get("/api/memory/query?kind=decision")
    metrics = mg.get_metrics()
    assert metrics["calls_total"].get("mio_memory_query:ok") == 2
    assert "mio_memory_query" not in metrics["last_error"]


def test_metrics_counter_records_timeout(_patch_client):
    _patch_client.call.side_effect = mg.MCPTimeout("5s")
    client.get("/api/memory/query")
    metrics = mg.get_metrics()
    assert metrics["calls_total"].get("mio_memory_query:timeout") == 1
    assert metrics["last_error"]["mio_memory_query"] == "timeout"


def test_metrics_endpoint_includes_memory(_patch_client):
    _patch_client.call.return_value = {"ok": True}
    client.get("/api/memory/query")
    r = client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    assert 'taskhub_memory_calls_total' in text
    assert 'tool="mio_memory_query"' in text
    assert 'outcome="ok"' in text


def test_record_writes_event_and_broadcasts(_patch_client, monkeypatch):
    """record 端点应触发 Event 表写入 + WS 广播。"""
    _patch_client.call.return_value = {}
    from mio_taskhub.db import engine
    from sqlmodel import Session, select
    from mio_taskhub.models import Event

    with Session(engine) as s:
        before = len(s.exec(select(Event)).all())

    r = client.post("/api/memory/record", json={
        "kind": "decision", "context": "v2 spec", "payload": {"v": 2}
    })
    assert r.status_code == 200

    with Session(engine) as s:
        events = s.exec(select(Event).where(Event.type == "memory_record")).all()
        assert len(events) >= 1
        latest = events[-1]
        assert latest.entity == "memory"
        import json
        payload = json.loads(latest.payload)
        assert payload["tool"] == "mio_memory_record"

