# mio-taskhub

Cross-agent task hub — local single-user service.

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
