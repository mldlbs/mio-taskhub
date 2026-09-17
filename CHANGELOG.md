# Changelog

## Unreleased

### Added — 任务文档体系

- **22 类文档 kind**：新增 `doc_paths.py` 作为唯一事实源（`DOC_KINDS`），`Task.doc_paths` 以 kind→相对路径的 JSON 落库，覆盖七件套主链 + 工程记录 + 复用资产三类。
- **七件套文档链**（`doc_chain.py`）：`requirement → architecture → spec → data-model → api → test → runbook`。`POST /api/v1/tasks/{id}/docs/scaffold` 一键补骨架，**默认不覆盖已有文件**，支持 `kinds` 指定子集与 `overwrite`；骨架自带跨链导航头、`FR-n`/`TC-n`/`ADR-n` 追溯编号与 TODO 占位，正文按分点+换行书写。
- **8 类文档生命周期状态机**（`doc_lifecycle.py`）：requirement `draft→approved`、spec `draft→review→approved`、decision `proposed→accepted→superseded`、plan `draft→approved→done`、test `planned→passed/failed`、milestone `planned→released`、incident `open→resolved→closed`，加 Task 自身。规则为严格向前：不许回退、不许跳级、终态不可改判。新增 `Task.doc_statuses` JSON 列（含迁移），首次写入自动初始化初始态。
- **质量保证三层闭环**（`doc_quality.py`）：
  - 写时 lint —— 每次 `PUT /doc` 同步检查必备章节、表格有效数据行、TODO 残留、分点书写，按 `score = max(0, 100 - 20*errors - 5*warns)` 评分并随响应返回；
  - 状态门控 —— 推进到 `review`/`approved`/`done` 要求 `errors == 0`，`force=true` 可强推但落事件留痕；
  - 修订指令 —— `GET /doc/{kind}/revision` 返回逐条可执行的修改要求，附章节写作指引与模板片段，把评分反哺回写作阶段。
  - 追溯矩阵 —— `traceability()` 校验 `requirement` 的 `FR-n` 是否被 `test` 的 `TC-n` 覆盖。
- **4 个新 MCP 工具**：`taskhub_scaffold_docs` · `taskhub_set_doc_status` · `taskhub_doc_quality` · `taskhub_doc_revision`（总数 33 → **40**）。
- **5 个新 REST 端点**：`POST /docs/scaffold`、`POST /doc/{kind}/status`、`GET /doc/statuses`、`GET /doc/{kind}/revision`、`GET /doc/quality`（总数 93 → **119**）。
- **阶段产出物门控扩展**：`STAGE_ARTIFACT_REQUIREMENTS` 由单 `document_kind` 改为 `document_kinds` 列表，新增 `brainstorming → requirement`、`implementing → changelog` 两个此前缺失的门，`design` 明确要求讨论会话；`GET /stages/requirements` 同时返回 `document_kinds` 与兼容字段 `document_kind`。
- **前端文档面板**（`DocPanel.jsx`）：22 类 kind 分类展示、七件套一键起骨架、质量分与生命周期状态徽标；新增 `web/src/stageDocs.js` 作为阶段产出物要求的单一来源，阶段推进弹窗由硬编码改为查表（无要求的阶段不再弹空输入框）。

### Added — 结构化可观测性

- **`mio_taskhub/observability/report.py`**：新增 agent 可直接消费的结构化快照，避免解析 Prometheus 文本。
  - `GET /api/v1/observability/report` —— database / http / process / threads / tasks / agents / slo / alerts / insights 全量快照。
  - `GET /api/v1/observability/integrity` —— integrity_check 风格总报告，逐组件 PASS/WARN/FAIL + `overall_status` + 完整 snapshot。
- `GET /api/v1/observability/summary` 改从 `collect_observability()` 取值，不再正则解析指标文本。
- 前端新增**可观测性视图**（`ObservabilityView.jsx`）。

### Fixed

- **`middleware.py` 错误率统计错误**：原先把所有响应都计入 `_error_count`，导致 2xx 也被算作错误；改为仅 4xx/5xx 计数。此前 `HighHttpErrorRate` 告警与可用性 SLO 会基于虚高的错误率误报。
- **`slo_history.py` SQLModel 参数传递**：`db.exec(text, {...})` 改为 `params={...}`，否则查询与清理静默失效。
- **`insights.py` 重复洞察刷屏**：60s 一次的评估会把同一个未确认洞察反复插入；现按 `title + severity + acknowledged=0` 去重，已存在则直接返回原记录。
- `AlertManager` 的 `HighHttpErrorRate` 告警文案改为中文并带百分比与请求数。

### Changed

- `PUT /api/v1/tasks/{id}/doc` 响应新增 `quality` 与 `status` 字段。
- 任务创建/更新接口统一走 `doc_paths`，`spec_path` / `plan_path` 降级为兼容入口并保留双向同步（`merge_doc_paths` / `sync_legacy_fields`）。
- `/dashboard` 移除对 `web/dist/dashboard.html` 构建产物的依赖，改由 `main.py` 内置 `_DASHBOARD_HTML` 兜底（`web/dist/dashboard.html` 已删除，876 行）。

### Tests

- 新增 `tests/test_doc_chain.py`（骨架生成与不覆盖语义）、`tests/test_doc_lifecycle.py`（状态机合法性）、`tests/test_doc_quality.py`（质量评分、门控、修订指令）、`tests/test_task_doc_paths.py`（22 类 kind 与兼容字段）。
- 用例总数 509 → **603**（53 文件 / 7,585 行）。

### Docs

- 重写 `README.md`：修正全部统计数字（后端 76 模块 / 13,749 行、119 端点、40 MCP 工具、前端 40 文件 / 11,694 行、603 用例），新增「任务文档体系」独立章节（22 类 kind / 七件套链 / 8 类生命周期 / 质量三层闭环 / 端点表 / 典型闭环），更新阶段门控表、MCP 工具分组表与目录结构。

---

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