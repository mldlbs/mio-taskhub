# mio-taskhub

Cross-agent task hub — local single-user service.

```bash
pip install -e ".[dev]"
mio-taskhub serve            # 启动服务（默认 http://localhost:8080）
mio-taskhub serve --auth     # 启用 Bearer <REDACTED> 认证
```

API: http://localhost:8080/api/v1/docs
Web UI: http://localhost:8080/
