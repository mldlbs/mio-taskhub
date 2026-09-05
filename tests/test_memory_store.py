"""Memory Store 测试：本地 JSONL 知识图谱（替代 MCP 子进程）。"""
import json
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

import mio_taskhub.memory_store as store
import mio_taskhub.api.memory as api_memory
from mio_taskhub.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    """每个测试用临时 JSONL 文件。"""
    tmp_file = str(tmp_path / "memory.jsonl")
    monkeypatch.setattr(store, "_DATA_FILE", tmp_file)
    monkeypatch.setattr(store, "_DATA_DIR", str(tmp_path))
    store.reset_metrics()
    # Reset rate limiter
    api_memory._rate_buckets.clear()
    return tmp_file


# ====== health ======

def test_health_ok():
    r = client.get("/api/memory/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["mcp"]["store_type"] == "jsonl"
    assert body["mcp"]["available"] is True
    assert body["mcp"]["proc_alive"] is True


# ====== query ======

def test_query_empty():
    r = client.get("/api/memory/query?kind=note")
    assert r.status_code == 200
    body = r.json()
    assert body["entities"] == []
    assert body["total"] == 0


def test_query_with_keyword():
    # 先写入一些数据
    store.add_entity("test-decision-1", "rule", ["使用 Python 3.12", "项目: mio-taskhub"])
    store.add_entity("test-note-1", "note", ["前端用 React"])

    r = client.get("/api/memory/query?keyword=Python")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    names = [e["name"] for e in body["entities"]]
    assert "test-decision-1" in names


def test_query_by_kind():
    store.add_entity("r1", "rule", ["decision test"])
    store.add_entity("n1", "note", ["note test"])

    r = client.get("/api/memory/query?kind=decision")
    assert r.status_code == 200
    body = r.json()
    # kind=decision → entityType=rule
    for e in body["entities"]:
        assert e["entityType"] == "rule"


def test_query_limit():
    for i in range(5):
        store.add_entity(f"item-{i}", "note", [f"item {i}"])
    r = client.get("/api/memory/query?limit=3")
    assert r.status_code == 200
    assert r.json()["total"] <= 3


def test_query_limit_out_of_range():
    r = client.get("/api/memory/query?limit=999")
    assert r.status_code == 422


# ====== record ======

def test_record_success():
    r = client.post("/api/memory/record", json={
        "kind": "decision", "context": "spec done", "payload": {"id": "x"}
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "name" in r.json()


def test_record_invalid_payload_returns_422():
    r = client.post("/api/memory/record", json={"context": "missing kind"})
    assert r.status_code == 422


def test_record_kind_required():
    r = client.post("/api/memory/record", json={"context": "no kind"})
    assert r.status_code == 422


def test_record_writes_to_jsonl(_tmp_store):
    client.post("/api/memory/record", json={
        "kind": "note", "context": "test note", "payload": {"k": "v"}
    })
    assert os.path.exists(_tmp_store)
    with open(_tmp_store, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    assert len(lines) >= 1
    item = json.loads(lines[-1])
    assert item["type"] == "entity"
    assert item["entityType"] == "note"


# ====== policy check ======

def test_policy_check_low_risk():
    r = client.post("/api/memory/policy/check", json={
        "operation": "update_task", "context": {}
    })
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True


def test_policy_check_high_risk():
    r = client.post("/api/memory/policy/check", json={
        "operation": "delete_task", "context": {"task_id": "x"}
    })
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is False
    assert "High risk" in body["reason"]


# ====== observer ingest ======

def test_observer_ingest_success():
    r = client.post("/api/memory/observer/ingest", json={
        "trace_id": "abc123",
        "event_type": "task_outcome",
        "payload": {"task": "t1"},
        "outcome": "success",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_observer_ingest_writes_jsonl(_tmp_store):
    client.post("/api/memory/observer/ingest", json={
        "trace_id": "trace-1", "event_type": "test", "payload": {}, "outcome": "ok"
    })
    with open(_tmp_store, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    assert len(lines) >= 1
    item = json.loads(lines[-1])
    assert item["entityType"] == "trace"


# ====== experience reuse ======

def test_experience_reuse_success():
    r = client.post("/api/memory/experience/reuse", json={
        "sourceAgent": "opencode",
        "targetAgent": "codex",
        "experienceId": "exp-001",
        "reuse": True,
        "behaviorChanged": True,
        "outcomeImproved": True,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_experience_reuse_required_fields():
    r = client.post("/api/memory/experience/reuse", json={
        "sourceAgent": "x", "targetAgent": "y"
    })
    assert r.status_code == 422


# ====== rate limiting ======

def test_rate_limit_429():
    # 写入一些数据让 query 有返回
    store.add_entity("rl-test", "note", ["rate limit test"])
    # 超限
    for _ in range(61):
        client.get("/api/memory/query?kind=note")
    r = client.get("/api/memory/query?kind=note")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


# ====== memory_store 单元测试 ======

def test_add_entity_basic():
    store.add_entity("e1", "rule", ["obs1", "obs2"])
    entities = store._load_entities()
    assert any(e["name"] == "e1" for e in entities)


def test_add_entity_merges_observations():
    store.add_entity("e2", "note", ["obs1"])
    store.add_entity("e2", "note", ["obs2"])
    entities = store._load_entities()
    e2 = next(e for e in entities if e["name"] == "e2")
    assert "obs1" in e2["observations"]
    assert "obs2" in e2["observations"]


def test_search_entities():
    store.add_entity("search-test", "rule", ["Python 代码规范"])
    results = store.search_entities(["Python"])
    assert len(results) >= 1
    assert results[0]["name"] == "search-test"


def test_search_no_match():
    store.add_entity("no-match", "note", ["unrelated"])
    results = store.search_entities(["zzz_nonexistent"])
    assert len(results) == 0


def test_search_by_kind():
    store.add_entity("kind-rule", "rule", ["decision"])
    store.add_entity("kind-note", "note", ["note"])
    results = store.search_entities([], kind="decision")
    assert all(e["entityType"] == "rule" for e in results)


def test_query_memories():
    store.add_entity("qm-1", "rule", ["query test"])
    result = store.query_memories(keyword="query")
    assert result["total"] >= 1


def test_record_memory():
    result = store.record_memory("decision", "test decision", {"k": "v"}, "test-proj")
    assert result["ok"] is True
    assert "name" in result


def test_health_store():
    h = store.health()
    assert h["available"] is True
    assert h["store_type"] == "jsonl"


def test_reset_metrics():
    store.record_call("test", "ok")
    store.record_call("test", "error")
    store.reset_metrics()
    m = store.get_metrics()
    assert m["calls_5m"] == {}
