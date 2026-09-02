"""Memory Gateway: 代理 mio-intelligence MCP 工具为本地 HTTP 端点。

- 进程内单例 MCPClient
- 懒加载：首次调用时启动 MCP 子进程
- 5s 超时，错误以异常抛出，由 API 层映射为 503/504
- 不持久化：纯代理（决策 A）
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


class MCPUnavailable(RuntimeError):
    """MCP server 不可达（启动失败/已退出）。"""


class MCPTimeout(RuntimeError):
    """MCP 调用超时。"""


class MCPRPCError(RuntimeError):
    """MCP 返回错误响应。"""


@dataclass
class MCPClient:
    command: str
    args: list = field(default_factory=list)
    timeout: float = 5.0
    _proc: Optional[subprocess.Popen] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _next_id: int = 1

    @classmethod
    def from_settings(cls) -> "MCPClient":
        cmd = os.environ.get("MIO_MEMORY_COMMAND", "uv")
        args_env = os.environ.get("MIO_MEMORY_ARGS", "run mio-intelligence")
        return cls(command=cmd, args=args_env.split())

    def is_available(self) -> bool:
        """快速健康检查：ping（不解析响应），5s 内未启动返回 False。"""
        try:
            self._ensure_proc()
            return self._proc is not None and self._proc.poll() is None
        except Exception:
            return False

    def call(self, tool: str, params: dict) -> dict:
        """同步 JSON-RPC 调用 tools/call，返回解析后的 result。"""
        with self._lock:
            self._ensure_proc()
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
        # 子线程读避免阻塞
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


_client: Optional[MCPClient] = None
_client_lock = threading.Lock()


def get_client() -> MCPClient:
    """获取进程内单例 MCP 客户端（懒加载）。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = MCPClient.from_settings()
    return _client


def reset_client():
    """测试用：重置单例以触发重新创建。"""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None
