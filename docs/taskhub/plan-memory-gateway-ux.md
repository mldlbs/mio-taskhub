# Memory Gateway UX 实施计划

## 子任务

1. MCPClient respawn 循环（30 min）
2. RateLimiter + middleware（30 min）
3. /health 详细子状态（15 min）
4. 错误响应增强（15 min）
5. GZip middleware（5 min）
6. 测试（60 min）
7. README + spec 同步（15 min）
8. exe 重建 + 验证（15 min）

## 步骤

### Step 1: MCPClient respawn 循环

修改 `memory_gateway.py`：
- 加 `max_respawn: int = 3`（可配置）
- `call()` 检测 `_proc.poll() is not None` → 自动 respawn 重试
- 加 `_respawn_count: int = 0` 字段
- 加 `_last_call_ms: Optional[float] = None`
- 加 `_last_error: Optional[str] = None`
- 在 except 块中更新这些字段

### Step 2: RateLimiter + middleware

新建 `memory_gateway.py` 内 `RateLimiter`：
- `__init__(max_per_min=60)`
- `_buckets: dict[(ip, endpoint), list[float]]`
- `check(ip, endpoint) -> (allowed: bool, retry_after: int)`
- 用 `time.time()` 时间戳列表，> 60s 清掉

新建 `memory_middleware.py`（或加到 main.py）：
- 从 `request.client.host` + `request.url.path` 取 key
- 不允许 → 429 + JSON + Retry-After header

### Step 3: /health 详细

`/api/memory/health` 返回：
```json
{
  "status": "ok",
  "mcp": {
    "available": true,
    "proc_alive": true,
    "respawn_count": 0,
    "last_call_ms": 12.3,
    "last_error": null,
    "calls_total_5m": 47
  }
}
```

`calls_total_5m` 实时计算：从 `_metrics["calls_total"]` + 时间窗口过滤。

### Step 4: 错误响应统一

`api/memory.py` 加 `memory_error_response(status, error, hint)` helper：
- 注入 request_id（从 request.state 或 header 取）
- 注入 hint（"MCP 子进程未启动。检查环境变量 MIO_MEMORY_COMMAND/..."）
- 注入 docs 链接 `/api/memory/health`

替换 `_call()` 里的 `HTTPException` 构造。

### Step 5: GZip middleware

```python
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)
```

### Step 6: 测试

新增 `tests/test_memory_gateway_ux.py`（10+ 测试）：
- respawn: 杀进程后 call 仍 OK；连续 3 次失败抛错
- 限流: 60 次通过；第 61 次 429 + Retry-After
- /health 详细: 字段完整
- 错误: 503 响应含 request_id/hint
- gzip: Accept-Encoding → Content-Encoding

### Step 7: README

加 "UX 增强" 章节：5 项优化的开关和默认值。

### Step 8: 部署

- 重建 exe
- 启动 + 验证 5 项

## 完成定义

- 489 + 10+ 测试全过
- exe 启动 + 5 项验证
- git commit + push
