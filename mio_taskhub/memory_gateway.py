"""Memory Gateway: 代理 mio-intelligence MCP 工具为本地 HTTP 端点。

v3 增强：
- MCPClient 自动 respawn（最多 3 次）
- 健康状态字段（proc_alive / respawn_count / last_call_ms / last_error）
- RateLimiter 进程内限流
- 5min 滚动窗口调用统计
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ---------- 异常 ----------

class MCPUnavailable(RuntimeError):
    """MCP server 不可达（启动失败/已退出/respawn 耗尽）。"""


class MCPTimeout(RuntimeError):
    """MCP 调用超时。"""


class MCPRPCError(RuntimeError):
    """MCP 返回错误响应。"""


# ---------- Metrics 计数器（v2 增强）----------

_metrics: dict = {
    "calls_total": {},        # key = "tool:outcome" → int
    "last_error": {},          # key = tool → outcome
    "calls_5m": {},            # key = tool → deque[float timestamps]
    "_lock": threading.Lock(),
}


def record_call(tool: str, outcome: str, ts: float = None):
    """记录一次 MCP 调用结果。outcome ∈ ok/unavailable/timeout/rpc_error。"""
    if ts is None:
        ts = time.time()
    with _metrics["_lock"]:
        key = "{}:{}".format(tool, outcome)
        _metrics["calls_total"][key] = _metrics["calls_total"].get(key, 0) + 1
        if outcome != "ok":
            _metrics["last_error"][tool] = outcome
        dq = _metrics["calls_5m"].setdefault(tool, deque())
        dq.append(ts)
        # trim > 5 min
        while dq and ts - dq[0] > 300:
            dq.popleft()


def get_metrics() -> dict:
    """获取 metrics 快照。"""
    with _metrics["_lock"]:
        return {
            "calls_total": dict(_metrics["calls_total"]),
            "last_error": dict(_metrics["last_error"]),
            "calls_5m": {k: len(v) for k, v in _metrics["calls_5m"].items()},
        }


def reset_metrics():
    """测试用：清空计数器。"""
    with _metrics["_lock"]:
        _metrics["calls_total"].clear()
        _metrics["last_error"].clear()
        _metrics["calls_5m"].clear()


# ---------- RateLimiter ----------

class RateLimiter:
    """进程内滑动窗口限流：max_per_min/分钟/(ip+endpoint)。"""

    def __init__(self, max_per_min: int = 60):
        self.max_per_min = max_per_min
        self._buckets: dict = {}  # key → deque[float ts]
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple:
        """返回 (allowed: bool, retry_after: int)。"""
        now = time.time()
        with self._lock:
            dq = self._buckets.setdefault(key, deque())
            # trim > 60s
            while dq and now - dq[0] > 60:
                dq.popleft()
            if len(dq) >= self.max_per_min:
                # 60s 窗口期最早的请求几秒后过期
                retry = max(1, int(60 - (now - dq[0])))
                return False, retry
            dq.append(now)
            return True, 0

    def reset(self):
        with self._lock:
            self._buckets.clear()


# ---------- MCPClient ----------

@dataclass
class MCPClient:
    command: str
    args: list = field(default_factory=list)
    timeout: float = 5.0
    max_respawn: int = 3
    _proc: Optional[subprocess.Popen] = None
    _lock: threading.RLock = field(default_factory=threading.RLock)  # 可重入（call 里再调 _call_once/_ensure_proc）
    _next_id: int = 1
    # 健康状态字段
    _respawn_count: int = 0
    _last_call_ms: Optional[float] = None
    _last_error: Optional[str] = None

    @classmethod
    def from_settings(cls) -> "MCPClient":
        cmd = os.environ.get("MIO_MEMORY_COMMAND", "uv")
        args_env = os.environ.get("MIO_MEMORY_ARGS", "run mio-intelligence")
        max_respawn = int(os.environ.get("MIO_MEMORY_MAX_RESPAWN", "3"))
        return cls(command=cmd, args=args_env.split(), max_respawn=max_respawn)

    def health(self) -> dict:
        """健康状态快照（懒启动子进程）。"""
        try:
            self._ensure_proc()  # 懒启动（如未启）
        except Exception:
            pass
        with self._lock:
            alive = self._proc is not None and self._proc.poll() is None
            return {
                "available": alive,
                "proc_alive": alive,
                "respawn_count": self._respawn_count,
                "last_call_ms": self._last_call_ms,
                "last_error": self._last_error,
            }

    def is_available(self) -> bool:
        """快速健康检查：懒启动。"""
        try:
            self._ensure_proc()
            with self._lock:
                return self._proc is not None and self._proc.poll() is None
        except Exception:
            return False

    def call(self, tool: str, params: dict) -> dict:
        """同步 JSON-RPC 调用 tools/call，返回解析后的 result。

        自动 respawn：进程死亡时自动重启后重试，最多 max_respawn 次。
        """
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_respawn + 1):
            with self._lock:
                start = time.time()
                try:
                    result = self._call_once(tool, params)
                    self._last_call_ms = round((time.time() - start) * 1000, 1)
                    self._last_error = None
                    return result
                except (MCPUnavailable, MCPTimeout) as e:
                    last_exc = e
                    self._last_call_ms = round((time.time() - start) * 1000, 1)
                    self._last_error = "unavailable" if isinstance(e, MCPUnavailable) else "timeout"
                    if attempt < self.max_respawn:
                        self._reset_proc()  # 杀旧，让下次循环 _ensure_proc 启新
                        self._respawn_count += 1
                        continue
                    raise
                except MCPRPCError as e:
                    self._last_call_ms = round((time.time() - start) * 1000, 1)
                    self._last_error = "rpc_error"
                    raise
        # shouldn't reach here, but for type checker
        if last_exc:
            raise last_exc
        raise MCPUnavailable("respawn_exhausted")

    def _call_once(self, tool: str, params: dict) -> dict:
        """单次调用，不重试。"""
        self._ensure_proc()
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": params},
            }
            try:
                self._proc.stdin.write(json.dumps(request) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self._reset_proc()
                raise MCPUnavailable(f"MCP pipe broken: {e}") from e

            line = self._read_line()
            if line is None:
                self._reset_proc()
                raise MCPTimeout(f"MCP call {tool} timed out after {self.timeout}s")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError as e:
                raise MCPRPCError(f"non-JSON response: {line!r}") from e
            if "error" in resp:
                raise MCPRPCError(f"MCP error: {resp['error']}")
            return resp.get("result", {})

    def close(self):
        with self._lock:
            self._reset_proc()

    def _ensure_proc(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            try:
                self._proc = subprocess.Popen(
                    [self.command, *self.args],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except (FileNotFoundError, OSError) as e:
                raise MCPUnavailable(f"failed to start MCP: {e}") from e

    def _read_line(self) -> Optional[str]:
        """带超时的读行。None = 超时。"""
        if self._proc is None or self._proc.stdout is None:
            return None
        result = []

        def _read():
            try:
                result.append(self._proc.stdout.readline())
            except Exception as e:
                result.append(e)

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(self.timeout)
        if t.is_alive():
            return None
        v = result[0] if result else None
        if isinstance(v, Exception):
            raise v
        return v.rstrip("\n") if v else None

    def _reset_proc(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None


# ---------- 全局单例 ----------

_client: Optional[MCPClient] = None
_limiter: Optional[RateLimiter] = None
_client_lock = threading.Lock()


def get_client() -> MCPClient:
    """获取进程内单例 MCP 客户端（懒加载）。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = MCPClient.from_settings()
    return _client


def get_limiter() -> RateLimiter:
    """获取进程内单例限流器。"""
    global _limiter
    if _limiter is None:
        with _client_lock:
            if _limiter is None:
                max_per_min = int(os.environ.get("MIO_MEMORY_RATE_LIMIT", "60"))
                _limiter = RateLimiter(max_per_min=max_per_min)
    return _limiter


def reset_client():
    """测试用：重置单例。"""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None
    if _limiter is not None:
        _limiter.reset()
