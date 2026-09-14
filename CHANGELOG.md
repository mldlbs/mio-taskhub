# Changelog

## v0.2.1 (2026-09-14)

### Fixed
- `/dashboard` 在 `web/dist/dashboard.html` 缺失时返回 404（且错误文案误写为 "Landing page not found"）。现改为回退到 `main.py` 中已有的内置仪表盘 HTML（`_DASHBOARD_HTML`），无需构建产物即可访问。

### Docs
- 重写 `README.md`：补充核心模型（状态机 / 7 段研发阶段 / 想法生命周期 / 实体表）、三种部署方式、MCP 33 工具分组、Web UI 视图表、可观测性与 SLO、完整环境变量表、7 个后台线程、打包踩坑与已知限制。
- 修正文档中的 API 文档地址：为 `/docs`（非 `/api/v1/docs`）。

---

## v0.2.0 (2026-09-11)

### Observability Overhaul (P0-P3)
- **OpenTelemetry**: Auto-instrumentation for FastAPI, SQLAlchemy, httpx
- **Built-in alerting**: `AlertManager` with thread/HTTP health rules
- **Alerts API**: `GET /api/v1/alerts` returns active alerts
- **Dashboard**: Self-contained HTML dashboard at `/dashboard` with Chart.js
- **USE metrics**: CPU, memory, threads, DB connection pool utilization
- **Business metrics**: Success/failure/cancel rates, throughput, P50 latency, retry stats
- **Dependency latency**: SQLite/Git/MCP call tracking (avg/P50/P90/P99/errors)
- **Thread health**: Heartbeat age, consecutive failures, alive status per thread
- **SLO/SLI**: Service level objectives with compliance metrics
- **Structured logging**: Trace context correlation (trace_id/span_id)
- **Log query API**: `GET /api/v1/logs` with level/logger filtering

### Prometheus Alert Rules
- `packaging/alerts.yml`: 24 alerting rules (critical/warning/info)
- `packaging/alertmanager.yml`: Alertmanager production config
- `packaging/prometheus.yml`: Prometheus scrape config

### New Modules
- `otel.py`: OpenTelemetry initialization and instrumentation
- `alerts.py`: Built-in AlertManager (no external dependencies)
- `dep_metrics.py`: Dependency latency collector with track() context manager

### Metrics Added
```
# System
taskhub_process_cpu_percent
taskhub_process_memory_rss_bytes / vms_bytes / percent
taskhub_process_threads
taskhub_db_pool_size / checked_out / checked_in / overflow / utilization
taskhub_thread_pool_alive / total / utilization

# Business
taskhub_task_success_rate / failure_rate / cancel_rate
taskhub_task_terminal_total
taskhub_task_throughput_24h / 7d
taskhub_task_retries_total / avg / max
taskhub_task_avg_completion_seconds / p50_seconds
taskhub_agent_utilization

# Dependencies
taskhub_dep_latency_count / errors / avg_ms / max_ms / p50_ms / p90_ms / p99_ms
taskhub_dep_error_rate

# SLO
taskhub_slo_availability_30d / target / breach
taskhub_slo_error_budget_remaining
taskhub_slo_latency_avg_ms_1d / breach
```

### API Endpoints Added
- `GET /dashboard` - Self-contained monitoring dashboard
- `GET /api/v1/alerts` - Active alert status
- `GET /api/v1/logs` - Log query with filtering

---

## v0.1.0 (2026-09-09)

### Initial Release
- FastAPI + SQLite task coordination hub
- Multi-agent dispatch with state machine
- MCP server for agent integration
- WebSocket real-time notifications
- ADR projection to Git
- Background thread management with heartbeat
- SQLite auto-backup (hourly snapshots)
- HTTP rate limiting (120 req/min/IP)
- PyInstaller EXE build
- Web SPA dashboard