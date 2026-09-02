"""Memory Gateway UX 增强测试（v3）。

5 项增强：
1. MCPClient respawn 循环
2. RateLimiter 端点限流
3. /health 详细子状态
4. 错误响应增强（request_id + hint）
5. GZip 响应压缩
"""
import time
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

import mio_taskhub.memory_gateway as mg
import mio_taskhub.api.memory as api_memory
from mio_taskhub.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.call.return_value = {}
    mock_client.health.return_value = {
        "available": True, "proc_alive": True,
        "respawn_count": 0, "last_call_ms": None, "last_error": None,
    }
    monkeypatch.setattr(mg, "get_client", lambda: mock_client)
    monkeypatch.setattr(api_memory, "get_client", lambda: mock_client)
    mg.reset_metrics()
    # Reset rate limiter
    if mg._limiter is not None:
        mg._limiter.reset()
    return mock_client


# ====== 1. MCPClient respawn ======

def test_client_respawns_after_proc_kill(monkeypatch):
    """子进程被杀后，下一次 call 应触发 respawn（计数 +1）。"""
    real_client = mg.MCPClient(command="cmd", args=["/c", "echo hi"], timeout=2.0)
    # Mock _ensure_proc to track calls without spawning
    ensure_calls = []
    def fake_ensure():
        ensure_calls.append(ensure_calls.__len__())
    monkeypatch.setattr(real_client, "_ensure_proc", fake_ensure)
    # Mock _call_once to simulate first failure (Unavailable), then success
    call_attempts = [0]
    def fake_call_once(tool, params):
        call_attempts[0] += 1
        if call_attempts[0] == 1:
            raise mg.MCPUnavailable("simulated pipe broken")
        return {"ok": True}
    monkeypatch.setattr(real_client, "_call_once", fake_call_once)
    result = real_client.call("mio_memory_query", {})
    assert result == {"ok": True}
    # Respawn was triggered once (after first Unavailable)
    assert real_client._respawn_count == 1
    assert call_attempts[0] == 2  # tried twice
    real_client.close()


def test_respawn_exhausted_raises_unavailable(monkeypatch):
    """连续 respawn 失败后抛 MCPUnavailable。"""
    real_client = mg.MCPClient(command="cmd", args=["/c", "echo"], timeout=2.0, max_respawn=2)
    # All attempts fail
    def always_fail(tool, params):
        raise mg.MCPUnavailable("never works")
    monkeypatch.setattr(real_client, "_call_once", always_fail)
    monkeypatch.setattr(real_client, "_ensure_proc", lambda: None)
    with pytest.raises(mg.MCPUnavailable):
        real_client.call("any_tool", {})
    # respawn_count should be max_respawn
    assert real_client._respawn_count == 2
    real_client.close()


def test_health_includes_respawn_count(_patch_client):
    _patch_client.health.return_value = {
        "available": True, "proc_alive": True,
        "respawn_count": 5, "last_call_ms": 12.3, "last_error": "timeout",
    }
    r = client.get("/api/memory/health")
    assert r.status_code == 200
    body = r.json()
    assert body["mcp"]["respawn_count"] == 5
    assert body["mcp"]["last_call_ms"] == 12.3
    assert body["mcp"]["last_error"] == "timeout"


# ====== 2. RateLimiter ======

def test_rate_limiter_allows_under_limit():
    limiter = mg.RateLimiter(max_per_min=3)
    for _ in range(3):
        allowed, _ = limiter.check("key1")
        assert allowed is True
    # 第 4 次应该被拒
    allowed, retry = limiter.check("key1")
    assert allowed is False
    assert 0 < retry <= 60


def test_rate_limiter_separate_keys():
    limiter = mg.RateLimiter(max_per_min=2)
    limiter.check("a")
    limiter.check("a")
    # key a 用完
    allowed_a, _ = limiter.check("a")
    assert allowed_a is False
    # key b 不受影响
    allowed_b, _ = limiter.check("b")
    assert allowed_b is True


def test_rate_limiter_window_expires(monkeypatch):
    limiter = mg.RateLimiter(max_per_min=2)
    # Use 2 quota
    limiter.check("k")
    limiter.check("k")
    # Mock time前进 70s
    fake_now = [time.time()]
    monkeypatch.setattr(mg.time, "time", lambda: fake_now[0])
    fake_now[0] += 70
    # 70s 后应可再次通过
    allowed, _ = limiter.check("k")
    assert allowed is True


def test_endpoint_rate_limit_returns_429(_patch_client):
    """/api/memory/query 第 N+1 次返回 429 + Retry-After。"""
    # Use a fresh limiter with very small limit
    limiter = mg.RateLimiter(max_per_min=2)
    with patch.object(mg, "_limiter", limiter):
        r1 = client.get("/api/memory/query?kind=note")
        r2 = client.get("/api/memory/query?kind=note")
        r3 = client.get("/api/memory/query?kind=note")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429
        assert "Retry-After" in r3.headers
        body = r3.json()
        assert body["detail"]["error"] == "rate_limited"
        assert "hint" in body["detail"]
        assert "request_id" in body["detail"]


# ====== 3. /health 详细子状态 ======

def test_health_includes_5min_counts(_patch_client):
    _patch_client.call.return_value = {}
    client.get("/api/memory/query?kind=note")
    client.get("/api/memory/query?kind=decision")
    r = client.get("/api/memory/health")
    body = r.json()
    assert body["mcp"]["calls_total_5m"] >= 2
    assert "per_tool_5m" in body["mcp"]
    assert "mio_memory_query" in body["mcp"]["per_tool_5m"]


def test_health_includes_last_error_per_tool(_patch_client):
    _patch_client.call.side_effect = mg.MCPTimeout("5s")
    client.get("/api/memory/query")
    r = client.get("/api/memory/health")
    body = r.json()
    assert body["mcp"]["last_error_per_tool"].get("mio_memory_query") == "timeout"


# ====== 4. 错误响应增强 ======

def test_503_response_includes_request_id_and_hint(_patch_client):
    _patch_client.call.side_effect = mg.MCPUnavailable("not running")
    r = client.get("/api/memory/query")
    assert r.status_code == 503
    body = r.json()
    detail = body["detail"]
    assert "request_id" in detail
    assert detail["request_id"] != "unknown"
    assert "hint" in detail
    assert "docs" in detail
    assert "检查" in detail["hint"] or "MCP" in detail["hint"]


def test_504_response_includes_hint(_patch_client):
    _patch_client.call.side_effect = mg.MCPTimeout("5s")
    r = client.get("/api/memory/query")
    assert r.status_code == 504
    detail = r.json()["detail"]
    assert "request_id" in detail
    assert "超时" in detail["hint"] or "5s" in detail["hint"]


def test_request_id_header_propagates():
    """X-Request-ID header 应被 echo 到响应。"""
    r = client.get("/api/memory/health", headers={"X-Request-ID": "test-12345"})
    assert r.headers.get("X-Request-ID") == "test-12345"


# ====== 5. GZip 响应压缩 ======

def test_gzip_compression_for_large_response(_patch_client):
    """> 1KB 响应应被 gzip 压缩：Content-Length 远小于 body 长度。"""
    # Mock a large response (> 1KB)
    _patch_client.call.return_value = [{"id": str(i), "context": "x" * 200, "extra": "y" * 100} for i in range(30)]
    r = client.get("/api/memory/query?kind=note", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    # GZipMiddleware: > 1KB 应被压缩
    assert r.headers.get("content-encoding") == "gzip"
    # Content-Length 是压缩后大小（198），远小于 body（httpx 已解压回 10071）
    cl = int(r.headers.get("content-length", "0"))
    assert cl > 0 and cl < len(r.content), f"expected compressed size {cl} < decompressed {len(r.content)}"


def test_small_response_passthrough(_patch_client):
    """小响应（< 1KB）不被压缩，正常 JSON 返回。"""
    _patch_client.call.return_value = {"small": True}
    r = client.get("/api/memory/query?kind=note", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"small": True}
