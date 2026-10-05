# mio-taskhub: Memory Gateway UX 增强

> 任务 ID: `b521838a`
> 基础：v1 (`0fb5e04`) + v2 (`fe4946b`) + v3 (`2c75472`)

## 1. 目标

在 Memory Gateway 已实现的 6 端点（query/record/policy/check/observer/ingest/experience/reuse）上，叠加 5 项体验优化：
1. **MCP 进程自动重连**
2. **端点级限流**
3. **/health 详细子状态**
4. **gzip 响应压缩**
5. **错误响应统一增强**（request_id + 建议）

## 2. 5 项体验优化

### 2.1 MCP 进程自动重连

**问题**：子进程异常退出后，下次 call 直接抛 MCPUnavailable，没有重试。

**方案**：在 `MCPClient.call` 里加 `MAX_RESPAWN = 3`：
- 检测到 `_proc.poll() is not None`（进程已死）→ 自动 respawn 后重试
- 连续 N 次失败 → 抛 MCPUnavailable + "respawn_exhausted" 提示
- 暴露 `_respawn_count` 给 /health

```python
def call(self, tool, params):
    for attempt in range(self.max_respawn + 1):
        if self._proc is None or self._proc.poll() is not None:
            self._reset_proc()  # 杀旧
            self._ensure_proc()  # 启新
        try:
            # ... 原有逻辑
            return result
        except (MCPUnavailable, MCPTimeout):
            if attempt >= self.max_respawn:
                raise
            self._reset_proc()
            continue
```

### 2.2 端点级限流

**问题**：恶意/失控 agent 可能刷接口。

**方案**：进程内滑动窗口（in-memory dict），默认 60 req/min/endpoint：
- key = (client_ip, endpoint)
- 超过 → 429 + Retry-After（秒）
- 用 middleware 而非每个端点写
- 内存里过期清理（每次检查时顺手清 > 60s 的条目）

```python
class RateLimiter:
    def __init__(self, max_per_min=60): ...
    def check(self, key) -> bool: ...  # True = allowed
    def retry_after(self, key) -> int: ...
```

### 2.3 /health 详细子状态

**当前**：
```json
{"status": "ok", "mcp_available": true}
```

**扩展**：
```json
{
  "status": "ok",
  "mcp": {
    "available": true,
    "proc_alive": true,
    "respawn_count": 0,
    "last_call_ms": 12,
    "last_error": null,
    "calls_total_5m": 47
  }
}
```

实现：MCPClient 内部记录 `last_call_ms` / `last_error` / `respawn_count`；metrics 暴露 `calls_total_5m`（时间窗口过滤）。

### 2.4 gzip 响应压缩

**问题**：API 响应 JSON 较大时（events 列表）传输慢。

**方案**：FastAPI middleware 检测 `Accept-Encoding: gzip` → 用 `gzip.compress()` 压缩响应体（仅 > 1KB 时压缩才有收益）。FastAPI 内置 `GZipMiddleware` 即可。

```python
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)
```

### 2.5 错误响应统一增强

**当前**：
```json
{"detail": {"error": "memory_unavailable", "detail": "..."}}
```

**增强**：
```json
{
  "detail": {
    "error": "memory_unavailable",
    "detail": "...",
    "request_id": "abc-123",
    "hint": "MCP 子进程未启动。检查 MIO_MEMORY_COMMAND/ARGS 环境变量。",
    "docs": "/api/memory/health"
  }
}
```

实现：自定义 `memory_error_response()` 辅助函数，注入 request_id（从 RequestIDMiddleware 取）和 hint 文本。

## 3. 关键实现

| 文件 | 改动 | 行数 |
|------|------|------|
| `memory_gateway.py` | MCPClient 加 respawn 循环 + 健康状态 | +30 |
| `memory_gateway.py` | RateLimiter 类 | +20 |
| `api/memory.py` | /health 改返回详细 | +10 |
| `api/memory.py` | 错误响应统一封装 | +15 |
| `main.py` | 加 GZipMiddleware | +3 |
| `main.py` | 全局 rate limit middleware | +15 |

## 4. 测试

每个特性至少 2 测试：

- **respawn**：
  - 子进程 kill 后下次 call 自动重启成功
  - 连续 3 次 respawn 失败抛 MCPUnavailable
- **限流**：
  - 60 req/min 内通过
  - 第 61 个返回 429 + Retry-After
- **/health 详细**：
  - 包含 mcp.proc_alive / respawn_count / last_call_ms
- **gzip**：
  - Accept-Encoding: gzip → Content-Encoding: gzip
  - > 1KB 响应才压缩
- **错误增强**：
  - 503 响应含 request_id + hint 字段

## 5. 风险

- 限流是 in-memory，跨进程不共享（多 worker 部署失效）— 接受 v1 限制
- respawn 风暴：恶意 MCP 短命进程 → 加指数退避（v2）
- gzip CPU 开销：仅压缩 > 1KB 的响应

## 6. 不在范围

- 持久化限流（Redis）
- WebSocket 限流
- 跨进程 metrics
- 完整的 OAuth / API key
