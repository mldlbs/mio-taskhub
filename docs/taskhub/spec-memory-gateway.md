# mio-taskhub: Memory Gateway 设计

> 任务 ID: `c5fc195e`
> 来源 Idea: `0787a973` (SCAMPER), `23111bd6` (Analogy), `44a3134e` (First-Principles)

## 1. 目标

把 mio-intelligence 的 4 个 MCP 工具（memory.query / memory.record / policy.check / observer.ingest）暴露为 taskhub HTTP 端点（`/api/memory/*`），让任何 agent 不依赖独立 MCP 配置就能读写记忆。

## 2. 范围决策（已与用户确认）

| 维度 | 决策 | 理由 |
|------|------|------|
| 代理范围 | **A. 纯代理（薄）** | 风险低、与 mio-intelligence 兼容、不引入双写 |
| 鉴权粒度 | **A. 单 token** | 复用 taskhub 现有 Bearer |
| 查询能力 | **A. 纯透传** | 先薄后厚；如需聚合留 v2 |

## 3. 端点设计

所有端点位于 `/api/memory/*`，复用 taskhub 现有 Bearer 认证。

| 方法 | 路径 | MCP 工具 | 行为 |
|------|------|---------|------|
| GET | `/api/memory/query` | `mio_memory_query` | URL params: `kind`, `project`, `limit` → JSON 列表 |
| POST | `/api/memory/record` | `mio_memory_record` | body: `{kind, context, payload}` → 200/204 |
| POST | `/api/memory/policy/check` | `mio_policy_check` | body: `{operation, context}` → `{allowed, reason}` |
| POST | `/api/memory/observer/ingest` | `mio_observer_ingest` | body: `{trace_id, event_type, payload, outcome}` → 200/204 |
| POST | `/api/memory/experience/reuse` | `mio_experience_reuse` | body: `{sourceAgent, targetAgent, experienceId, reuse, behaviorChanged, outcomeImproved}` → 200/204 |

### 3.1 MCP 调用方式

taskhub 通过 stdio/JSON-RPC 调用 mio-intelligence MCP server（与 desktop-agent / mio-cua 一样）。配置：

```toml
# pyproject.toml 或独立 mio-intelligence.json
[mcp_servers.memory]
command = "uv"
args = ["run", "mio-intelligence"]
```

如果 mio-intelligence 不可达：返回 `503 Service Unavailable`，body 包含 `{error: "memory_unavailable", detail: ...}`。

## 4. 关键实现

### 4.1 新模块

`mio_taskhub/memory_gateway.py`：
- `is_available() -> bool`：检测 MCP server 是否可达
- `call(tool: str, params: dict) -> dict`：JSON-RPC 调用
- 单例 `MCPClient`，懒加载，进程内缓存

### 4.2 新路由

`mio_taskhub/api/memory.py`：
- 4 个端点，每个 ~15 行
- 统一异常处理：MCP 不可达 → 503；超时 → 504；参数错误 → 422
- 复用 `_verify_bearer`（taskhub 现有）

### 4.3 事件广播（v2）

写操作（`record` / `observer/ingest` / `experience/reuse`）经 taskhub Event 表 + WS 广播：
- 写一条 `entity=memory` 的 Event
- WS 消息 `type=memory_update` 推送给所有订阅者
- 异常静默（不影响主流程）

### 4.4 Metrics 暴露（v2）

`mio_taskhub/memory_gateway.py` 维护进程内计数器：
- `_metrics["calls_total"]["{tool}:{outcome}"]`：累计调用次数
- `_metrics["last_error"][tool]`：最近一次错误类型

`/metrics` 端点（v2）输出：
```
# HELP taskhub_memory_calls_total Memory gateway call counts
# TYPE taskhub_memory_calls_total counter
taskhub_memory_calls_total{tool="mio_memory_query",outcome="ok"} 5
...
```

## 5. 测试

- 单元测试：mock MCPClient 返回值，覆盖 4 个端点的成功/失败路径
- 集成测试：跳过（依赖 mio-intelligence 进程），改用 mock
- 端点契约：每个端点至少 1 个 happy + 1 个 error 测试

## 6. 验收

1. `GET /api/memory/query` 代理 `mio_memory_query`
2. `POST /api/memory/record` 代理 `mio_memory_record`，写事件
3. `POST /api/memory/policy/check` 代理 `mio_policy_check`
4. `POST /api/memory/observer/ingest` 代理 `mio_observer_ingest`，写事件
5. `POST /api/memory/experience/reuse` 代理 `mio_experience_reuse`，写事件
6. Bearer 认证
7. 单元测试覆盖（17+）
8. WS 广播（memory_update）
9. /metrics 暴露 memory 子段
10. README 更新

## 7. 风险

- **MCP 不可达**：返回 503，不阻塞 taskhub 主流程
- **超时**：默认 5s，可配置；超时返回 504
- **敏感信息**：不存储任何 secrets/credentials（依赖 mio-intelligence 自身规则）

## 8. 不在范围

- 持久化（taskhub 不写 JSONL）
- 语义检索 / 聚合
- 多 token 鉴权
- write/read token 分离
