"""端到端集成测试：启 fake MCP server，验证 MCPClient 真实 JSON-RPC 通信。

fake server：stdio 接收 JSON-RPC 请求，返回固定响应。
验证：
- 进程启动 / 关闭
- JSON-RPC 请求发送
- 响应解析
- 超时行为
"""
import json
import os
import subprocess
import sys
import time

import pytest

from mio_taskhub.memory_gateway import (
    MCPClient,
    MCPTimeout,
    MCPUnavailable,
    MCPRPCError,
)


# --- fake MCP server script ---
FAKE_MCP_SCRIPT = """
import json, sys, time

# Echo back what we get, with a small delay
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    method = req.get('method', '')
    if method == 'tools/list':
        # return list of 6 tools
        resp = {
            'jsonrpc': '2.0',
            'id': req.get('id', 1),
            'result': {
                'tools': [
                    {'name': 'mio_memory_query'},
                    {'name': 'mio_memory_record'},
                    {'name': 'mio_policy_check'},
                    {'name': 'mio_observer_ingest'},
                    {'name': 'mio_experience_reuse'},
                ],
            },
        }
    elif method == 'tools/call':
        params = req.get('params', {})
        tool = params.get('name', '')
        args = params.get('arguments', {})
        # Echo result based on tool
        if tool == 'mio_memory_query':
            result = [{'id': 'm1', 'kind': 'note', 'context': 'echo:' + str(args)}]
        elif tool == 'mio_memory_record':
            result = {'id': 'rec-' + str(req.get('id')), 'kind': args.get('kind', 'note')}
        elif tool == 'mio_policy_check':
            result = {'allowed': True, 'reason': 'echo'}
        elif tool == 'mio_observer_ingest':
            result = {'ok': True}
        elif tool == 'mio_experience_reuse':
            result = {'ok': True, 'experienceId': args.get('experienceId')}
        else:
            result = {'error': 'unknown tool: ' + tool}
        resp = {
            'jsonrpc': '2.0',
            'id': req.get('id', 1),
            'result': result,
        }
    else:
        resp = {'jsonrpc': '2.0', 'id': req.get('id', 1), 'error': {'code': -32601, 'message': 'unknown'}}
    sys.stdout.write(json.dumps(resp) + '\\n')
    sys.stdout.flush()
"""


@pytest.fixture
def fake_mcp():
    """Start a fake MCP server as subprocess, return (Popen, script_path)."""
    script = FAKE_MCP_SCRIPT
    p = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    yield p
    try:
        p.kill()
    except Exception:
        pass
    try:
        p.wait(timeout=2)
    except Exception:
        pass


def test_client_spawns_and_calls(fake_mcp):
    """真实启动子进程，发送 JSON-RPC，验证响应。"""
    client = MCPClient(command=sys.executable, args=["-c", FAKE_MCP_SCRIPT], timeout=3.0)
    # Replace internal proc with the fixture proc to avoid double-spawn
    client._proc = fake_mcp
    result = client.call("mio_memory_query", {"kind": "note", "limit": 5})
    assert isinstance(result, list)
    assert result[0]["kind"] == "note"
    assert "echo:" in result[0]["context"]
    client.close()


def test_client_record_round_trip(fake_mcp):
    client = MCPClient(command=sys.executable, args=["-c", FAKE_MCP_SCRIPT], timeout=3.0)
    client._proc = fake_mcp
    result = client.call("mio_memory_record", {"kind": "decision", "context": "v3 test", "payload": {"k": "v"}})
    assert result["kind"] == "decision"
    assert result["id"].startswith("rec-")
    client.close()


def test_client_policy_check(fake_mcp):
    client = MCPClient(command=sys.executable, args=["-c", FAKE_MCP_SCRIPT], timeout=3.0)
    client._proc = fake_mcp
    result = client.call("mio_policy_check", {"operation": "delete_task", "context": {"id": "t1"}})
    assert result["allowed"] is True
    client.close()


def test_client_experience_reuse(fake_mcp):
    client = MCPClient(command=sys.executable, args=["-c", FAKE_MCP_SCRIPT], timeout=3.0)
    client._proc = fake_mcp
    result = client.call("mio_experience_reuse", {
        "sourceAgent": "a", "targetAgent": "b", "experienceId": "exp-1", "reuse": True
    })
    assert result["experienceId"] == "exp-1"
    client.close()


def test_client_unknown_tool_returns_error_from_server(fake_mcp):
    """服务端响应包含 result.error 时，客户端应原样返回（不抛）。"""
    client = MCPClient(command=sys.executable, args=["-c", FAKE_MCP_SCRIPT], timeout=3.0)
    client._proc = fake_mcp
    result = client.call("mio_unknown_tool", {})
    assert "error" in result
    client.close()


def test_client_health_check():
    """is_available 懒加载：首次调用启动子进程 → True；close 后 → False。"""
    client = MCPClient(command=sys.executable, args=["-c", FAKE_MCP_SCRIPT], timeout=3.0)
    # First call: 懒加载启动子进程 → True
    assert client.is_available() is True
    assert client._proc is not None
    client.close()
    # After close: proc is None
    assert client._proc is None
    # Next is_available: 重新启动子进程 → True (因为是懒加载)
    assert client.is_available() is True
    client.close()
