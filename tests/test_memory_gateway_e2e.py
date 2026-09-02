"""端到端集成测试：taskhub + fake MCP server 全链路。

启 fake MCP server 模拟 mio-intelligence，
taskhub 启动后代理请求到 fake server，验证：
- 200/响应内容正确
- metrics 计数
- Event 表写入
"""
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

# 必须在 import app 之前 patch 环境变量
FAKE_MCP_BODY = """
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    method = req.get('method', '')
    tool = req.get('params', {}).get('name', '') if method == 'tools/call' else ''
    args = req.get('params', {}).get('arguments', {}) if method == 'tools/call' else {}
    if method == 'tools/call':
        if tool == 'mio_memory_query':
            result = [{'id': 'fake-1', 'kind': args.get('kind', 'note'), 'context': 'from fake MCP'}]
        elif tool == 'mio_memory_record':
            result = {'id': 'rec-' + str(req.get('id', 1)), 'kind': args.get('kind')}
        elif tool == 'mio_policy_check':
            result = {'allowed': True, 'reason': 'fake-policy-ok'}
        elif tool == 'mio_observer_ingest':
            result = {'ok': True}
        elif tool == 'mio_experience_reuse':
            result = {'ok': True, 'experienceId': args.get('experienceId')}
        else:
            result = {'error': 'unknown tool: ' + tool}
    elif method == 'tools/list':
        result = {'tools': [{'name': n} for n in [
            'mio_memory_query', 'mio_memory_record', 'mio_policy_check',
            'mio_observer_ingest', 'mio_experience_reuse',
        ]]}
    else:
        result = {}
    resp = {'jsonrpc': '2.0', 'id': req.get('id', 1), 'result': result}
    sys.stdout.write(json.dumps(resp) + '\\n')
    sys.stdout.flush()
"""


@pytest.fixture(scope="module")
def fake_mcp_proc():
    """Start fake MCP server in background."""
    p = subprocess.Popen(
        [sys.executable, "-c", FAKE_MCP_BODY],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    yield p
    p.kill()
    try:
        p.wait(timeout=2)
    except Exception:
        pass


@pytest.fixture(scope="module")
def e2e_client(fake_mcp_proc, monkeypatch_module):
    """Inject the fake_mcp_proc into MCPClient._proc to bypass re-spawn."""
    import mio_taskhub.memory_gateway as mg
    import mio_taskhub.api.memory as api_memory

    # Force client to use our fake proc instead of respawning
    original_ensure = mg.MCPClient._ensure_proc
    def patched_ensure(self):
        self._proc = fake_mcp_proc
    mg.MCPClient._ensure_proc = patched_ensure
    # api_memory imports MCPClient from memory_gateway, so patch the same class
    mg.reset_client()
    mg.reset_metrics()
    from mio_taskhub.main import app
    client = TestClient(app)
    yield client
    mg.MCPClient._ensure_proc = original_ensure
    mg.reset_client()


def pytest_module_setup():
    pass


from _pytest.fixtures import FixtureFunction
from pytest import fixture

# workaround to share monkeypatch scope
@fixture(scope="module")
def monkeypatch_module():
    yield


def test_e2e_health_check(e2e_client):
    r = e2e_client.get("/api/memory/health")
    assert r.status_code == 200
    body = r.json()
    assert body["mcp"]["proc_alive"] is True
    assert "respawn_count" in body["mcp"]


def test_e2e_query_returns_fake_data(e2e_client):
    r = e2e_client.get("/api/memory/query?kind=note&limit=5")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert data[0]["kind"] == "note"
    assert data[0]["context"] == "from fake MCP"


def test_e2e_record_writes_event(e2e_client):
    from mio_taskhub.db import engine
    from mio_taskhub.models import Event
    r = e2e_client.post("/api/memory/record", json={
        "kind": "decision", "context": "e2e test", "payload": {"v": 3}
    })
    assert r.status_code == 200
    with Session(engine) as s:
        events = s.exec(select(Event).where(Event.type == "memory_record")).all()
        assert any(json.loads(e.payload)["params"]["context"] == "e2e test" for e in events)


def test_e2e_policy_check(e2e_client):
    r = e2e_client.post("/api/memory/policy/check", json={
        "operation": "delete_task", "context": {"id": "t1"}
    })
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True
    assert body["reason"] == "fake-policy-ok"


def test_e2e_observer_ingest_writes_event(e2e_client):
    from mio_taskhub.db import engine
    from mio_taskhub.models import Event
    r = e2e_client.post("/api/memory/observer/ingest", json={
        "trace_id": "e2e-trace-1",
        "event_type": "task_outcome",
        "payload": {"task": "t1"},
        "outcome": "success",
    })
    assert r.status_code == 200
    with Session(engine) as s:
        events = s.exec(select(Event).where(Event.type == "memory_observer_ingest")).all()
        assert any(e.entity_id == "e2e-trace-1" for e in events)


def test_e2e_experience_reuse_writes_event(e2e_client):
    from mio_taskhub.db import engine
    from mio_taskhub.models import Event
    r = e2e_client.post("/api/memory/experience/reuse", json={
        "sourceAgent": "opencode",
        "targetAgent": "codex",
        "experienceId": "e2e-exp-1",
        "reuse": True,
        "behaviorChanged": True,
    })
    assert r.status_code == 200
    with Session(engine) as s:
        events = s.exec(select(Event).where(Event.type == "memory_experience_reuse")).all()
        assert any(e.entity_id == "e2e-exp-1" for e in events)


def test_e2e_metrics_shows_all_5_tools(e2e_client):
    """触发 5 个工具调用后，/metrics 应有 5 行 memory_calls_total。"""
    e2e_client.get("/api/memory/query?kind=note")
    e2e_client.post("/api/memory/record", json={"kind": "note", "context": "m", "payload": {}})
    e2e_client.post("/api/memory/policy/check", json={"operation": "op", "context": {}})
    e2e_client.post("/api/memory/observer/ingest", json={"trace_id": "m1", "event_type": "x", "payload": {}})
    e2e_client.post("/api/memory/experience/reuse", json={
        "sourceAgent": "a", "targetAgent": "b", "experienceId": "e1", "reuse": True
    })
    r = e2e_client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    # 至少 5 个 tool 的 metrics 行
    for tool in ["mio_memory_query", "mio_memory_record", "mio_policy_check",
                 "mio_observer_ingest", "mio_experience_reuse"]:
        assert 'tool="{}"'.format(tool) in text, "missing metrics for {}".format(tool)
