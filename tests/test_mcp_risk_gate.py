# -*- coding: utf-8 -*-
"""MCP 风险门控测试（task 7d7ff97a / 评估 round3 P1-C）。

覆盖：风险分级、默认拒绝 destructive、放行开关、read/write 不受影响、
统一收口（_request）不可绕过。
"""
import os

import pytest

from mio_taskhub import mcp_risk
from mio_taskhub.mcp_risk import READ, WRITE, DESTRUCTIVE


# ---------- 分级 ----------

@pytest.mark.parametrize("method,path,level", [
    ("GET", "/board/summary", READ),
    ("GET", "/tasks", READ),
    ("GET", "/tasks/abc/doc", READ),
    ("POST", "/tasks", WRITE),
    ("POST", "/tasks/abc/subtasks", WRITE),
    ("POST", "/agents/register", WRITE),
    ("POST", "/discussions/abc/messages", WRITE),
    ("DELETE", "/tasks/abc", DESTRUCTIVE),
    ("POST", "/tasks/abc/stage/move", WRITE),
    ("POST", "/tasks/abc/doc/spec/status", DESTRUCTIVE),
    ("PATCH", "/tasks/abc", WRITE),
])
def test_classify(method, path, level):
    assert mcp_risk.classify(method, path) == level


# ---------- 策略：默认拒绝 destructive ----------

def test_destructive_blocked_by_default(monkeypatch):
    monkeypatch.delenv("MIO_MCP_ALLOW_DESTRUCTIVE", raising=False)
    allowed, reason = mcp_risk.check("DELETE", "/tasks/abc")
    assert allowed is False and "destructive" in reason


def test_move_to_stage_is_write_not_destructive(monkeypatch):
    """move_to_stage 是正常工作流（受 stage 门控约束），归 write 不拦截。"""
    monkeypatch.delenv("MIO_MCP_ALLOW_DESTRUCTIVE", raising=False)
    assert mcp_risk.check("POST", "/tasks/abc/stage/move")[0] is True


def test_write_allowed_by_default(monkeypatch):
    monkeypatch.delenv("MIO_MCP_ALLOW_DESTRUCTIVE", raising=False)
    assert mcp_risk.check("POST", "/tasks")[0] is True


def test_read_allowed_by_default(monkeypatch):
    assert mcp_risk.check("GET", "/board/summary")[0] is True


def test_destructive_allowed_when_env_set(monkeypatch):
    monkeypatch.setenv("MIO_MCP_ALLOW_DESTRUCTIVE", "1")
    assert mcp_risk.check("DELETE", "/tasks/abc")[0] is True


# ---------- 统一收口：_request 不可绕过 ----------

def test_request_gate_blocks_destructive(monkeypatch):
    """直接调用 mcp_server._request 的 destructive 路径也被拒（无第二收口）。"""
    import asyncio
    import mio_taskhub.mcp_server as ms
    monkeypatch.delenv("MIO_MCP_ALLOW_DESTRUCTIVE", raising=False)

    # 让审计写事件失败也无妨（不连 DB 时 emit 会抛，_audit 内部吞掉）
    out = asyncio.run(ms._request("DELETE", "/tasks/abc"))
    assert isinstance(out, dict) and out.get("blocked") is True
    assert "destructive" in out.get("error", "")


def test_request_gate_allows_when_enabled(monkeypatch):
    """开启放行后，因测试无 hub，会走 HTTP 分支返回连接错误而非 blocked。"""
    import asyncio
    import mio_taskhub.mcp_server as ms
    monkeypatch.setenv("MIO_MCP_ALLOW_DESTRUCTIVE", "1")
    out = asyncio.run(ms._request("DELETE", "/tasks/abc"))
    # 未连线 → 返回 error，但不应是 blocked
    assert not (isinstance(out, dict) and out.get("blocked") is True)


def test_read_request_not_blocked(monkeypatch):
    import asyncio
    import httpx
    import mio_taskhub.mcp_server as ms
    monkeypatch.delenv("MIO_MCP_ALLOW_DESTRUCTIVE", raising=False)

    async def fake_request(self, method, url, params=None, json=None):
        return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))
    monkeypatch.setattr(ms._client, "request", fake_request.__get__(ms._client, type(ms._client)))

    out = asyncio.run(ms._request("GET", "/board/summary"))
    assert not (isinstance(out, dict) and out.get("blocked") is True)
    assert out.get("ok") is True


# ---------- 工具标注一致性 ----------

def test_high_risk_tools_annotated_destructive():
    import inspect
    import mio_taskhub.mcp_server as ms
    src = inspect.getsource(ms)
    for nm in ("taskhub_cancel_task", "taskhub_set_doc_status"):
        # 找到该工具的 @_tool 声明行，确认 destructive=True
        idx = src.find('name="%s"' % nm)
        assert idx != -1, nm
        line_end = src.find("\n", idx)
        decl = src[src.rfind("@_tool", 0, idx):line_end]
        assert "destructive=True" in decl, f"{nm} 未标 destructive=True"
