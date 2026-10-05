# mio-taskhub: Memory Gateway 实施计划

> 任务 ID: `c5fc195e`
> spec: `docs/taskhub/spec-memory-gateway.md`

## 1. 子任务

| # | 标题 | 估计 | 依赖 |
|---|------|------|------|
| 1 | MCP 客户端封装（stdio/JSON-RPC） | 60 min | — |
| 2 | `/api/memory/*` 4 个端点 | 45 min | 1 |
| 3 | Bearer 认证 + 错误处理 | 30 min | 2 |
| 4 | 单元测试（mock MCP） | 45 min | 2 |
| 5 | README + 集成到 main.py | 30 min | 3 |
| 6 | PyInstaller rebuild + 验证 | 15 min | 5 |

## 2. 步骤

### Step 1: MCP 客户端封装

新增 `mio_taskhub/memory_gateway.py`：

```python
class MCPClient:
    def __init__(self, command, args, timeout=5): ...
    def is_available(self) -> bool: ...
    def call(self, tool: str, params: dict) -> dict: ...

_client: Optional[MCPClient] = None

def get_client() -> MCPClient:
    global _client
    if _client is None:
        _client = MCPClient.from_settings()
    return _client
```

实现要点：
- `subprocess.Popen` 启动 MCP server
- 写入 JSON-RPC 请求（`{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {...}}`）
- 读取 stdout 一行（newline-delimited JSON）
- 5s 超时
- 进程内单例，懒加载

### Step 2: 4 个端点

新增 `mio_taskhub/api/memory.py`：

```python
@router.get("/query")
def query(kind: str = None, project: str = None, limit: int = 20, ...):
    result = get_client().call("mio_memory_query", {"kind": kind, "project": project, "limit": limit})
    return result

@router.post("/record")
def record(body: RecordRequest, ...):
    get_client().call("mio_memory_record", body.dict())
    return {"ok": True}

# 同理 policy/check 和 observer/ingest
```

每个端点 ~15 行，含：
- 参数校验
- 调用 client
- 错误处理（503/504/422）

### Step 3: Bearer 认证

复用 `mio_taskhub.auth.verify_bearer`：

```python
from .auth import verify_bearer
router = APIRouter(prefix="/api/memory", dependencies=[Depends(verify_bearer)])
```

错误处理统一中间件：
- `MCPUnavailable` → 503 `{error: "memory_unavailable"}`
- `MCPTimeout` → 504 `{error: "memory_timeout"}`
- `ValueError` → 422

### Step 4: 单元测试

`tests/test_memory_gateway.py`：
- `test_query_success`：mock client 返回 list
- `test_query_unavailable`：503
- `test_query_timeout`：504
- `test_record_success`：200
- `test_record_invalid_payload`：422
- `test_policy_check_denied`：200 + `{allowed: false}`
- `test_observer_ingest_with_event_broadcast`：mock emit_event
- `test_bearer_required`：401

目标 8+ 测试。

### Step 5: 集成

- `mio_taskhub/main.py` 注册 router
- README 加一节「Memory Gateway」
- 配置说明：环境变量 `MIO_MEMORY_COMMAND` / `MIO_MEMORY_ARGS`

### Step 6: 重建 + 验证

- PyInstaller rebuild（spec 已含动态依赖）
- 启动 exe + 调用 `GET /api/memory/query?kind=note` 验证 Bearer + 503 fallback

## 3. 风险与回退

- **MCP 启动慢**：懒加载 + 5s 超时；首次失败可降级为禁用
- **pytest 中 mock**：避免真启进程，全用 monkeypatch

## 4. 交付物

- `mio_taskhub/memory_gateway.py`
- `mio_taskhub/api/memory.py`
- `mio_taskhub/main.py` 修改（1 行注册）
- `tests/test_memory_gateway.py`（8+ 测试）
- `README.md` 新增「Memory Gateway」节
- `dist/mio-taskhub/mio-taskhub.exe` 重建

## 5. 完成定义

- 459 + 8+ 测试全通过
- exe 启动 + 4 端点 curl 验证
- git commit + push
