# mio-taskhub

Cross-agent task hub — local single-user service.

能力：跨 agent 任务中心、研发阶段生命周期（brainstorming→done）、依赖编排（depends_on 数组 + 依赖满足自动放行 + 环检测）、统一事件日志（seq 增量订阅）、想法工作台（Idea→Task 一键拆解）、讨论会话、MCP 工具接入。

```bash
pip install -e ".[dev]"
mio-taskhub serve            # 启动服务（默认 http://localhost:48620）
mio-taskhub serve --auth     # 启用 Bearer 认证
```

Bearer 认证：
- 服务端：`mio-taskhub serve --auth`。token 优先取 `MIO_TASKHUB_TOKEN` 环境变量，其次 `--token <token>`，均未设置时自动生成并打印到控制台。
- 客户端：MCP 服务（`mio-taskhub-mcp`）与 `agent_wrapper.py` 通过设置 `MIO_TASKHUB_TOKEN` 环境变量，自动携带 `Authorization: Bearer <token>` 头。
- WebSocket：连接时传 `?token=<token>` 或 `Authorization: Bearer <token>` 头。
- Web UI（`/`）与 `/docs` 不要求认证（本地单用户使用）。

API: http://localhost:48620/api/v1/docs
Web UI: http://localhost:48620/

## Memory Gateway

把 `mio-intelligence` MCP 工具暴露为 taskhub HTTP 端点，让任何 agent 不依赖独立 MCP 配置就能读写记忆。

| 方法 | 路径 | 代理 MCP 工具 |
|------|------|--------------|
| GET | `/api/memory/health` | 健康检查 |
| GET | `/api/memory/query` | `mio_memory_query` |
| POST | `/api/memory/record` | `mio_memory_record` |
| POST | `/api/memory/policy/check` | `mio_policy_check` |
| POST | `/api/memory/observer/ingest` | `mio_observer_ingest` |

所有端点自动复用 taskhub Bearer 认证（`/api/*` 前缀触发中间件）。

配置（环境变量，可选）：
- `MIO_MEMORY_COMMAND` — MCP server 启动命令，默认 `uv`
- `MIO_MEMORY_ARGS` — 启动参数（空格分隔），默认 `run mio-intelligence`

错误映射：
- MCP 不可达 → `503 {error: "memory_unavailable"}`
- 超时 → `504 {error: "memory_timeout"}`
- RPC 错误 → `502 {error: "memory_rpc_error"}`
- 参数错误 → `422`（Pydantic 校验）

实现：纯代理（不持久化），单例进程内 MCP 客户端，5s 超时。详细见 `docs/taskhub/spec-memory-gateway.md`。
