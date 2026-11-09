# 服务设计与 API 契约（Phase 2 / 任务 #6）

> 编制：程不宕（后端服务工程师）｜取证日 2026-11-06
> 对象：`E:\work\code\agent-dev\mio-taskhub` 现有 v0.6.1 代码库，不重写后端。
> 事实底稿：`docs/taskhub/_delivery-fact-pack.md`、`delivery-plan/phase1-requirements.md`、`delivery-plan/phase1-architecture.md`（三份已完整读完）；补充源码复核 `main.py`、`api/runs.py`、`api/task_documents.py`、`api/tasks.py`、`api/claim.py`、`api/board.py`、`api/task_stages.py`、`api/reviews.py`、`api/events.py`、`api/task_graph.py`、`api/task_diff.py`、`api/agents.py`、`diff_gate.py`、`completion_gate.py`、`read_evidence.py`、`ratchet.py`、`doc_quality.py`、`middleware.py`、`background.py`、`events.py`、`db.py`、`models.py`、`migrations.py`、`seed.py`、`net_guard.py`、`mcp_risk.py`、`scheduling/cron_engine.py`、`scheduling/night_runner.py`、`ops/backup.py`、`heartbeat.py`、`d468e633/api.md`。
> 口径声明：版本 0.6.1 / 176 REST / 56 MCP / 22 类文档 kind / 1601 用例取事实包 §2。本次源码复核实测 `mcp_server.py` 905 行、`background.py` 717 行、`models.py` 646 行、`api/*.py` 44 个（含 `__init__.py`，非 init 43 个）、`api/runs.py` 636 行、`diff_gate.py` 371 行。与事实包旧口径不一致处逐条标【待确认】。文档门控只用「结构完整性校验」与「服务端强制」两个口径。本章不含工期日历与人力估算；涉及成本只给假设前提。

---

## 1. 服务边界与模块划分

### 1.1 物理服务清单

系统形态是**模块化单体 + 单进程 + 进程内事件驱动组件**。物理上只有一个 hub 进程，默认监听 `127.0.0.1:48620`（`main.py:471`）。

| 服务 | 职责 | 依赖 | 技术栈 |
|---|---|---|---|
| hub（`main.py` 装配 + `lifespan`） | `/api/v1` 176 REST、静态 Web 产物挂载 `/`、`/ws` 广播、中间件链、后台线程装配与关停 | SQLite(WAL) `taskhub.db`、workspace 文件系统、git 只读子进程、cron webhook（httpx） | Python ≥3.10 / FastAPI 0.141.1 / Starlette 1.6.0 / uvicorn 0.52.4 / SQLModel 0.0.42 / SQLAlchemy 2.0.52 / Pydantic 2.13.5 |
| MCP stdio 服务（`mcp_server.py` 905 行，God File） | 56 个 MCP 工具，本质是内部 HTTP `/api/v1` 转发薄代理 | hub 进程（同机回环） | mcp 1.29.1；统一执行层收口在 `_request`，`mcp_risk.check()` 在此判定 |
| Web UI（静态产物） | 10+ 视图，看板 / 拓扑 / 甘特 / 文档面板 / 评审面板，`/ws` 驱动刷新 | hub 的 `/api/v1` 与 `/ws` | React 18 + Vite 5，产物由 `app.mount("/", StaticFiles(...))` 挂载（`main.py:453-454`） |
| 打包入口（`packaging/run.py`） | 按 `sys.argv[1]` 分派 hub / mcp / widget 三角色 | 上述三者 | PyInstaller，Windows x64 优先 |

MCP 服务不是独立进程，是同一 hub 进程内的 stdio 包装；Web UI 不是独立服务，是 hub 挂载的静态文件。「服务间通信」在本项目里是进程内函数调用，不是 RPC。

### 1.2 域 / 路由模块 / 职责 / 关键端点 / 跨域依赖

`api/*.py` 实测 44 个文件。`main.py:218-277` 逐 router `include_router`。

| 域 | 路由模块 | 职责 | 关键端点 | 与其他域的依赖 |
|---|---|---|---|---|
| 任务与状态 | `tasks.py`、`task_helpers.py`、`task_subtasks.py`、`decompose.py`、`decisions.py`、`inbox.py`、`blockers.py`、`readiness.py` | 建管、详情、取消、重试、人工确认、讨论、父子层级、决策一等公民 | `POST /tasks`、`GET /tasks`、`GET/PATCH/DELETE /tasks/{id}`、`/retry`、`/confirm` | `completion_gate`（tier 推断）、`dependency`/`planner`（环检测）、`claim.py`、`events`、`doc_paths` |
| 阶段推进 | `task_stages.py` | 7 段阶段门控、`STAGE_ARTIFACT_REQUIREMENTS`、`LIFECYCLE_GATE` 累计门控 | `GET /tasks/stages/requirements`、`POST /tasks/{id}/stage`、`POST /tasks/{id}/stage/move` | `doc_lifecycle`、`completion_gate.check_done`、`workflow.state_machine`、`read_evidence.resolve_doc_path` |
| claim / run / submit | `claim.py`、`agents.py`、`runs.py`、`hall.py` | 原子抢单、注册 / 心跳、run 生命周期、提交结果（门控汇聚点） | `POST /tasks/claim`、`/agents/register`、`/agents/heartbeat`、`/runs/{id}/heartbeat`、`/runs/{id}/result` | `read_evidence`、`diff_gate`、`completion_gate`、`workflow.transitions`、`events`、`mio_bridge` |
| 文档读写与门控 | `task_documents.py`（`DOC_PATTERNS` 是 `discover_task_docs()` 内局部字典，:82-107）、`doc_chain.py`、`doc_quality.py`、`doc_lifecycle.py`、`ratchet.py` | 文档正文读写、清单、scaffold、状态门控、质量报告、修订指令、棘轮 | `GET/PUT /tasks/{id}/doc`、`/documents`、`/file`、`/raw`、`POST /docs/scaffold`、`POST /doc/{kind}/status`、`GET /doc/statuses`、`/doc/{kind}/revision`、`/doc/quality`、`/ratchet` | `doc_paths`、`read_evidence`、`events`、`_resolve_within_workspace()` |
| 读证与 diff 证据 | `runs.py`、`read_evidence.py`、`diff_gate.py`、`task_diff.py` | 必读指纹校验、空 diff 采集判定、只读自查、任务级 diff 清单 | `GET /runs/{id}/read-evidence`、`/runs/{id}/diff-evidence`、`/tasks/{id}/diff` | `doc_paths`、`completion_gate.is_zero_diff` |
| 评审 | `reviews.py` | 评审队列、历史、自审 409 缓解 | `GET /tasks/reviews/queue`、`GET/POST /tasks/{id}/reviews` | `Run`（最近执行者）、`doc_path_of(t,"review")`、`events` |
| 依赖与图 | `task_graph.py`、`plans.py`、`dependency.py`、`planner.py`、`composite.py` | 全图 / 子图 / 泳道、环检测、关键路径 | `GET /tasks/graph`、`/tasks/{id}/graph`、`/tasks/{id}/workstream` | `task_deps`、`detect_cycle` |
| 事件与 WS | `api/events.py`、`events.py` | 单调 `seq` 增量订阅、TaskEvent 时间线、commit 后 WS 广播 | `GET /events`、`/tasks/{id}/events`、`WS /ws` | `models.Event`、`models.TaskEvent`、SQLAlchemy `after_flush`/`after_commit` 钩子 |
| 看板与统计 | `board.py`、`cockpit.py`、`observability.py` | 汇总、概览、窄门禁灰度统计 | `GET /board/summary`、`/board/overview`、`/board/diff-gate-stats`、`/status` | `Run.diff_summary`、`dependency`、`composite`、`diff_gate.gate_mode` |
| 健康与就绪 | `main.py` 内联 | 存活、就绪、指标、看板 HTML、日志查询、告警 | `GET /healthz`、`/readyz`、`/metrics`、`/dashboard`、`/api/v1/logs`、`/api/v1/alerts` | `db.check_connection`、`ops/backup.get_backup_status` |
| 配置 / 调度 / 更新 / 想法（支撑域） | `config.py`、`scheduled_jobs.py`、`scheduler_control.py`、`nightrun.py`、`update.py`、`insights.py`、`memory.py`、`mio_runtime.py`、`material_panel.py`、`value_discovery.py`、`ideas*.py`、`adr.py`、`discussions.py`、`templates.py`、`draft.py`、`deviations.py`、`task_lessons.py` | 配置、定时任务、自更新、观测脚手架、想法与 ADR | `/scheduled-jobs/...`、`scheduler_control`、`update`、`insights` | 观测脚手架单向依赖 `observability/`；冻结项代码与接口保留 |

### 1.3 分层结构与目录组织

| 层 | 目录 / 文件 | 职责 | 现状 |
|---|---|---|---|
| 接入层 | `main.py`、`middleware.py`、`mcp_server.py`、`api/events.py`（WS 部分） | app 装配、lifespan、中间件、`include_router` 顺序、静态挂载、认证、MCP 薄代理 | 路由注册顺序有显式约束：`hall` / `inbox` / `blockers` / `readiness` / `tasks.router` 必须按 `main.py:225-240` 顺序，否则 `/tasks/hall`、`/tasks/inbox` 被 `/tasks/{task_id}` 抢先匹配 |
| 应用层 | `api/`（44 个 `.py`） | 参数校验、业务编排、出参组装、错误码 | `memory` / `mio_runtime` / `material_panel` / `value_discovery` / `scheduler_control` / `observability` / `insights_router` 挂载时**未统一显式加** `prefix="/api/v1"`（`main.py:270-277`），见 §11【待确认】 |
| 领域层 | `workflow/`、`models.py`（646 行）、`doc_paths.py`、`doc_chain.py`、`doc_lifecycle.py`、`doc_quality.py`、`ratchet.py`、`read_evidence.py`、`diff_gate.py`、`completion_gate.py`、`dependency.py`、`planner.py`、`composite.py`、`hierarchy.py`、`decomposition.py`、`next_action.py`、`policy_guard.py`、`mcp_risk.py`、`net_guard.py` | 三条正交维度、四层校验、安全策略 | 三条维度挂在 `Task` 上互不覆盖：`state` 列 / `stage` 列 / JSON 列 `doc_paths`、`doc_statuses` |
| 基础设施层 | `db.py`（126 行）、`migrations.py`（598 行）、`events.py`（142 行）、`ops/backup.py`、`ops/git_sync.py`、`ops/data_fixes.py` | engine / session、迁移、单调 `seq` 事件流、在线备份 | `get_session()` 是 `with Session(engine) as session: yield session`，**退出只 close 不 commit**（`db.py:56-58`），这是 §8.7 窄门禁必须显式 commit 的根因 |
| 后台调度层 | `background.py`（717 行）、`scheduling/`、`update/` | 7 业务线程、`ThreadRegistry` 反向关停、cron 自管线程、自更新 | `ThreadRegistry` 与 `_thread_registry` 在 `background.py` 中**重复定义两次**（:541-583 与 :586-636），后者覆盖前者，见 §10 |
| 可观测（冻结） | `observability/` | Prometheus 文本端点、告警、日志、SLO 快照 | 该域已冻结；F8 otel 未生效；SLO 目标值为目标非实测 |
| 打包分发层 | `packaging/`、`installer/`、`payload_layout.py`、`scripts/make_zip.py` | 单 EXE / 安装包 / 确定性 zip | 不在本章展开 |

### 1.4 依赖方向（禁止循环依赖）

```
接入层 main.py / mcp_server.py / middleware.py
   ↓ 单向
应用层 api/*.py
   ↓ 单向
领域层 workflow/ · doc_* · diff_gate · completion_gate · read_evidence · ratchet · dependency · planner · composite · *_guard · *_risk · models.py
   ↓ 单向
基础设施层 db.py · migrations.py · events.py · ops/
```

- `models.py` 是唯一权威模型，被所有层单向引用，不反向 import 任何 `api/` 或 `background.py`。
- `events.py` 只被上层通过 `emit_event(db, ...)` 调用，不 import `api/`。
- `background.py` import `api.claim.claim_for`（:9）与 `api.runs.create_run`（`api/claim.py:7`），属后台层 → 应用层单向依赖；`runs.py` 不 import `background.py`，无环。
- `completion_gate.is_zero_diff(run)` 读 `Run.diff_summary`（`completion_gate.py:214-221`），而 `diff_summary` 由 `runs.py::_apply_diff_gate` 写入，这是**数据依赖**不是模块循环依赖，但它决定了门控顺序（见 §8.7）。

### 1.5 不拆微服务、不拆多进程的判据与演进触发条件

| 判据 | 依据 |
|---|---|
| 1–5 agents 规模，单进程建模足够 | 架构 §6 决策 6（v3 审计定性结论） |
| 单库单文件 SQLite(WAL) + QueuePool（pool_size=5 / max_overflow=10，`db.py:26-28`），状态与事件在同一库事务内写入 | `db.py`、`events.py` after_commit 广播钩子 |
| 事件广播是进程内 `asyncio.run(ws_manager.broadcast(...))`（`events.py:77-102`），无 broker、无 IPC、无第二份状态 | 引入独立进程需跨进程共享 SQLite 并重建 Outbox→WS 通道 |
| 单 EXE 免安装、零云依赖、单人维护、Windows 优先 | 事实包 §1、§3 |
| P1-4 并发实验零重复 / 100% 成功率，当前事务边界未成为瓶颈 | 事实包 §6 N6；任务 837b156f 已关闭 |

演进触发条件（任一成立才重估，`docs/roadmap/03` §6.1 N6）：并发 >1000 / 跨进程写 / 多租户 → 迁 PostgreSQL，保留 SQLModel，改 engine 与连接串并复核 32 个 naive datetime 用例；后台任务需在 hub 崩溃后独立存活 → 才考虑独立进程托管。出现第二个真实用户 → 重估 SLO / tracing，不自动触发服务拆分。

---

## 2. API 清单与契约

### 2.1 前缀、认证范围与统一响应

- REST 统一挂 `/api/v1`。文档页 `/docs`、`/redoc`、`/openapi.json`、`/dashboard`、`/landing`、`/healthz`、`/readyz`、`/metrics`、`/ws` 在根路径，不带 `/api/v1` 前缀。
- `app.mount("/", StaticFiles(directory=WEB_DIR, html=True))` 在 `main.py:453-454` 最后注册，前面定义的 API 路由优先匹配。
- 成功响应为各端点自定义 JSON 对象；失败响应沿用 FastAPI `HTTPException`：字符串 `detail` 或结构化 `detail` 对象。**当前不是全局单一错误 schema**，4 类门控 422 的 detail 结构各不相同（见 §9），是否收敛标【待确认】。
- 4xx 共同点：结构化 detail 都带可执行的 `message` / `hint`（`_read_gate_detail` 注释说明 MCP 端截断 200 字符、体积尽量小）。
- `RequestIDMiddleware` 对所有响应注入 `X-Request-ID`，并把 `application/json` 补 `; charset=utf-8`（`middleware.py:53-58`），避免中文 Windows GBK 解码乱码。

### 2.2 按域分组端点清单

约定：所有路径带 `/api/v1` 前缀（表中省略）；入参标注位置（p=path、q=query、b=body）。字段级约束（位置 / 类型 / 必填 / 默认 / 约束 / 示例）在 §2.3 对本交付范围接口逐条给出四子块；其余端点的字段级约束由 FastAPI/Pydantic 自动生成的 `/openapi.json` 承载。

**任务与状态**

| 方法 | 路径 | 入参 | 出参 | 状态码 | 说明 |
|---|---|---|---|---|---|
| POST | `/tasks` | b: title/description/priority/state(初始不可为 done/cancelled/review)/stage/doc_paths/spec_path/plan_path/workspace/depends_on/gate_tier/gate_tier_reason/doc_gate_exempt/grab_mode/max_retries 等 | `{id,title,state,priority,created_at,depends_on,grab_mode,doc_gate_exempt,parent_id,role,complexity,gate_tier,gate_tier_reason}` | 200/400/422 | `gate_tier` 由 `completion_gate.infer_tier()` 推断，显式降级必须给 `gate_tier_reason`（否则 400 `gate.tier_downgrade_reason_required`） |
| GET | `/tasks` | q: state / agent_type / stage / cancelled(默认 false) | 任务数组（含 `task_kind`、`completion_run_id`、`gate_tier`） | 200 | 默认排除 `CANCELLED` |
| GET | `/tasks/{task_id}` | p: task_id | 详情，含 `runs[].diff_summary` / `runs[].annotations` / `runs[].rejections`、`doc_statuses`、`diagnostic_hint` | 200/404 | `diff_summary` 未采集为 `null`（非 `{}`） |
| PATCH | `/tasks/{task_id}` | b: 可编辑字段含 `diff_gate_exempt`、`doc_gate_exempt`、`gate_tier`、`workspace` | 更新后详情 | 200/400/404/422 | `depends_on` 变更触发 `validate_depends` + `check_cycle`；`files`/`labels` 变化触发 `upgrade_tier` 只升不降 |
| DELETE | `/tasks/{task_id}` | q: confirm / reason(默认 manual，枚举 CANCEL_REASONS) / detail | `{ok,state,policy,reason,detail}` | 200/400/404/409 | 取消即终态；`policy_guard.guard_action("taskhub:delete-task")` |
| POST | `/tasks/{task_id}/retry` | b: 可选 | `{id,state,stage,attempt,max_retries,retry_at}` | 200/404/409 | 仅 `FAILED`/`RETRYING` 可重试；清空 `bounce_count` |
| POST | `/tasks/{task_id}/confirm` | b: confirmed_by(必填) / note / scope | `{id,human_confirmed_at,human_confirmed_by,human_confirm_note}` | 200/404/409/422 | 执行者自确认 409 `gate.self_confirmation`，除非 `MIO_ALLOW_SELF_CONFIRM=1` |
| GET / POST | `/tasks/{task_id}/discussions` | b: topic / agent / conclusions / messages | 讨论对象 / 列表 | 200/404 | 进 `design` 要求至少一条 discussion（`requirement` 已 approved 时豁免） |
| GET | `/tasks/{task_id}/children` | p: task_id | `{parent_id,children,children_total,children_done}` | 200/404 | 只读 |
| GET | `/status` | q: agent | `board_summary` 结果 | 200 | 别名端点，`main.py:280-283` |

**claim / run / submit**

| 方法 | 路径 | 入参 | 出参 | 状态码 | 说明 |
|---|---|---|---|---|---|
| POST | `/tasks/claim` | q: agent(必填) / agent_type / task_id / project / workspace / files / project_scope | `{id,task_id,state,agent_name,attempt,required_reads,required_fr,documents,read_evidence_required,lessons,note,gate_tier,readiness,route}` | 200/204/404/409 | 幂等复用已有活跃 run；无候选返回 204；`required_reads` 默认 `spec,api,requirement`；内联 `documents` 是预览不是已读 |
| POST | `/agents/register` | b: name(必填) / agent_type | `{name,status}` | 200/400 | upsert，已存在置 ONLINE |
| POST | `/agents/heartbeat` | b: name(必填) | `{name,status,last_heartbeat}` | 200/400 | upsert，不写事件（防心跳风暴） |
| POST | `/runs/{run_id}/heartbeat` | q: agent；b: progress / checkpoint / pid | `{id,state,progress}` | 200/403/404/409 | `state=FINISHED` 返回 409 `run.not_active`；CAS `WHERE state != FINISHED` 与 reaper 竞争，rowcount≠1 → 409 `run.race_lost` |
| POST | `/runs/{run_id}/result` | q: agent；b: success / result / exit_code / evidence | `{id,state,result,task_state[,retry_at,backoff_seconds]}` | 200/403/404/409/422 | 门控汇聚点，四道校验顺序见 §8.7；成功与失败都置 `progress=100` |
| GET | `/tasks/hall` | q: 过滤项 | 只读大厅 | 200 | `grab_mode=true` 任务，复用既有 `claim()` CAS |

**文档读写与状态 / 修订 / 质量 / 棘轮**

| 方法 | 路径 | 入参 | 出参 | 状态码 | 说明 |
|---|---|---|---|---|---|
| GET | `/tasks/{id}/doc` | q: kind(必填，须在 DOC_KINDS) / run_id / agent | `{kind,path,content,truncated,missing,status[,read_evidence]}` | 200/400/404 | 传 `run_id` 时落 ReadEvidence（含 `document_version` 指纹）；正文 ≤200000 字符截断 |
| PUT | `/tasks/{id}/doc` | q: kind；b: content(必填，str) / path / mode(overwrite\|append) / overwrite | `{kind,path,created,mode,size,status,quality}` | 200/400/409/413/422/500 | `quality` 为写时 lint 反馈，不阻断；`MAX_WRITE_CHARS=1_000_000` |
| GET | `/tasks/{id}/documents` | p: task_id | `{workspace,documents,counts}` | 200/404 | 4 级来源排序；扫描发现项不挂生命周期状态 |
| GET | `/tasks/{id}/file` | q: path(必填) | `{path,content,truncated,size}` 或二进制提示 | 200/400/404 | 越界 400 |
| GET | `/tasks/{id}/raw` | q: path(必填) | 原始文件流 | 200/400/404 | 二进制安全 |
| POST | `/tasks/{id}/docs/scaffold` | b: kinds / overwrite | `{chain,created,skipped,doc_paths}` | 200/400/404/422 | 七件套骨架，已存在默认不覆盖 |
| POST | `/tasks/{id}/doc/{kind}/status` | p: kind；b: state(必填) / note / force | `{kind,status,doc_paths,quality}` | 200/400/404/422 | 三门控汇聚：生命周期转移、质量门 422、棘轮 422 |
| GET | `/tasks/{id}/doc/statuses` | p: task_id | `{statuses,lifecycles}` | 200/404 | 各 kind 当前态 + `allowed_next` |
| GET | `/tasks/{id}/doc/{kind}/revision` | p: kind | `{kind,quality,revision_prompt}` | 200/400/404 | 无质量问题时 `revision_prompt=null` |
| GET | `/tasks/{id}/doc/quality` | p: task_id | `{docs,traceability,summary}` | 200/400/404 | `traceability` 含 `fr_total` / `fr_covered` / `fr_uncovered` |
| GET | `/tasks/{id}/ratchet` | p: task_id | `{task_id,enabled,baselines}` | 200/404 | `MIO_RATCHET_DISABLED=1` 关闭 |

**读证与 diff 证据**

| 方法 | 路径 | 入参 | 出参 | 状态码 | 说明 |
|---|---|---|---|---|---|
| GET | `/runs/{id}/read-evidence` | p: run_id | `{run_id,task_id,required_reads,read_ok,missing,missing_files,stale,enforced,passed,gate_kinds}` | 200/404 | `check_read_gate` 直接返回 |
| GET | `/runs/{id}/diff-evidence` | p: run_id | `{run_id,task_id,mode,would_block,skipped,reason,files_changed,before,after,changed}` | 200/404 | 与 `submit_result` 同一套 `check_diff_gate` 判据；只读，不落事件、不改 run |
| GET | `/tasks/{id}/diff` | q: fr_ref | `{task_id,workspace,reason,stat,changed_files}` | 200/403/404 | 任务级只读 diff，三路证据聚合；E3 假门埋点写本地 jsonl |
| GET | `/board/diff-gate-stats` | 无 | 统计对象（12 个顶层键，见 §2.3） | 200 | 纯统计，不参与任何流程判定 |

**阶段推进**

| 方法 | 路径 | 入参 | 出参 | 状态码 | 说明 |
|---|---|---|---|---|---|
| GET | `/tasks/stages/requirements` | 无 | `{stages:{...document_kinds,requires_discussion,accepts_text,text_field,error,lifecycle_gate},doc_kinds}` | 200 | 单一事实源 |
| POST | `/tasks/{id}/stage` | b: target_stage(必填) / force / doc_paths / spec_path / plan_path / review_result | `{id,stage,spec_path,plan_path,doc_paths,review_result,state}` | 200/400/404/409/422 | `STAGE_PIPELINE` 累计门控，`_check_lifecycle_gate` 用 `_gates_upto(dst)` 取目标及其之前所有阶段门槛并集 |
| POST | `/tasks/{id}/stage/move` | 同上 | 同上 | 200/400/404/409/422 | 拖拽路径，`strict=False`；`done` 只从 `review` 经 T5+T6 进入；终态源阶段 400 |

**评审**

| 方法 | 路径 | 入参 | 出参 | 状态码 | 说明 |
|---|---|---|---|---|---|
| GET | `/tasks/reviews/queue` | 无 | `{tasks,count}` | 200 | `stage=REVIEW` 列表 + `wait_seconds` |
| GET | `/tasks/{id}/reviews` | p: task_id | 评审记录数组 | 200/404 | 不可变，只追加 |
| POST | `/tasks/{id}/reviews` | b: decision(approve\|reject\|comment) / reviewer / summary / comments / checklist / artifacts / self_review | `{id,decision,review_duration_sec}` | 200/404/409/422 | 自审 409 `review.self_approval`；approve 结论 ≥8 字符（`MIN_APPROVE_SUMMARY=8`），有 review 文档时豁免 |

**依赖与图 / 事件与 WS / 健康检查**

| 方法 | 路径 | 入参 | 出参 | 状态码 | 说明 |
|---|---|---|---|---|---|
| GET | `/tasks/graph` | 无 | `{nodes,edges,missing,has_cycle,cycle_path}` | 200 | `planner.detect_cycle` |
| GET | `/tasks/{id}/graph` | p: task_id | 祖先 / 后代 / 子图 / `has_cycle` / `cycle_path` | 200/404 | |
| GET | `/tasks/{id}/workstream` | p: task_id | 依赖分层、阻塞、`critical_path_ids`、统计 | 200/404 | `part-of:<id>` 聚合 |
| GET | `/events` | q: after_seq(ge=0) / limit(1..1000，默认 200) | `{events:[{seq,type,entity,entity_id,run_id,payload,at}],next_seq}` | 200 | 不传 `after_seq` 返回最近 limit 条；=0 从 seq 1 分页；=N 返回 `seq>N` 增量 |
| GET | `/tasks/{id}/events` | q: limit(默认 50) | `{task_id,events:[TaskEvent 时间线]}` | 200/404 | M1 状态变更记录 |
| WS | `/ws` | q: `token` 或 Authorization Bearer | `task_update` / `idea_update` / `discussion_update` / `memory_update` / `metrics_snapshot` / `event_update` | close 4401 | `MESSAGE_TYPES` 不含 `run`，落在 `event_update`；鉴权失败 close(4401) |
| GET | `/healthz` | 无 | `{status:"ok"}` | 200 | 存活探针 |
| GET | `/readyz` | 无 | `{status,db,backups:{count,latest}}` | 200/503 | `check_connection()` 执行 `SELECT 1`；异常 → 503 `{status:degraded,db:error}` |
| GET | `/metrics` | 无 | Prometheus 文本 | 200 | 任务 / 事件 / agent 计数、`slo_*`、`dep_latency_*`、uptime、memory store 计数 |
| GET | `/dashboard` | 无 | HTML | 200 | 优先读 `web/dist/dashboard.html`，缺失回退 `_DASHBOARD_HTML` |
| GET | `/api/v1/logs` | q: level / logger / limit(默认 100) | `{logs}` | 200 | 环形缓冲 1000 条 |
| GET | `/api/v1/alerts` | 无 | `{alerts,active_count}` | 200 | AlertManager |

### 2.3 接口明细（四子块）——本交付范围 7 个接口

按本仓 `api` kind 约定（`doc_quality.QUALITY_SPEC['api']`）：每个参数给 位置 / 类型 / 必填 / 默认 / 约束 / 示例；每个接口给 请求参数 / 响应字段 / 错误码 / 示例 四个子块。以下为门控主链路 + 窄门禁 4 个变更接口 + 统计端点的完整明细（示例 JSON 从略，见成员原文与 `d468e633/api.md`）。

#### POST /api/v1/runs/{run_id}/result

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|---|---|---|---|---|---|---|
| `run_id` | path | string | 是 | — | 8 位十六进制（uuid4 前 8 位） | `a1b2c3d4` |
| `agent` | query | string | 否 | — | 传了则必须等于 `Run.agent_name`，否则 403 `run.not_owner`；不传跳过校验（已知限制） | `claude-code` |
| `success` | body | bool | 否 | `true` | 直接采信 Agent 自报；`true` 时触发读证门控 + 窄门禁 + 完成门禁 | `true` |
| `result` | body | string | 否 | `""` | 执行结果正文；`completion_gate` 在 block 档要求长度 ≥10（`gate.result_too_thin`） | `已完成 useFacilityMap 染色改造` |
| `exit_code` | body | int | 否 | `success?0:1` | 显式传入以该值为准；`exit_code==0` 且 `task.state==COMPLETED` 是重复提交判据 | `0` |
| `evidence` | body | object | 否 | `{}` | `verification.{commands,tests,artifacts}`；`commands[].exit_code` 必须 int、`tests[].passed` 必须 bool、`artifacts` 须 workspace 相对路径 | `{"verification":{"commands":[{"cmd":"pytest -q","exit_code":0}]}}` |
| `progress` | body | int | 否 | — | 本接口内部固定置 100，不接受客户端值 | — |

**响应字段**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | run id |
| `state` | string | 是 | 提交后 `finished` |
| `result` | string | 是 | 回显 `result` |
| `task_state` | string \| null | 是 | `completed` / `retrying` / `failed` 或真实状态；`null` 表示 task 缺失 |
| `retry_at` | string \| null | 否 | 仅 `task_state=retrying` 时返回，ISO UTC |
| `backoff_seconds` | number \| null | 否 | 仅 `retrying` 时返回，`(2^max(1,attempt))*2.0` |

**错误码**

| HTTP | 业务码 | 含义 | 处理建议 |
|---|---|---|---|
| 403 | `run.not_owner` | 传了 `agent` 但与持有者不符 | 用正确 agent 名重试 |
| 404 | — | run 不存在 | 核对 id |
| 409 | `run.result_already_submitted` | `run.state=FINISHED` 且 `exit_code=0` 且 `task.state=COMPLETED` | 不重试 |
| 409 | `run.empty_diff_repeated` | 连续被拒超过上限（源码 `rejections>2`，即第 3 次） | 实际完成改动后重新领取；或置 `diff_gate_exempt=true` |
| 422 | `run.empty_diff` | `MIO_DIFF_GATE=block` 下声明成功但双快照判定零改动 | 按 `detail.hint` 补改动或置豁免 |
| 422 | — | ReadEvidence 门控未满足 | 按 `detail.missing` / `detail.stale` 补读后重提 |
| 422 | `gate.*` / `decompose.*` | `MIO_COMPLETION_GATE=block` 下 `check_submit` 抛 `GateError` | 按 `detail.code` 补证据 |

**示例**：`curl -X POST "http://127.0.0.1:48620/api/v1/runs/a1b2c3d4/result?agent=claude-code" -H "Content-Type: application/json" -d '{"success": true, "result": "已完成染色改造"}'`。拦截响应 `detail = {code:"run.empty_diff", message:"拒绝提交：声明 success=true，但本次 run 期间工作区零改动（0 文件 / 0 行）", hint:"...", files_changed:0, rejections:1, max_rejections:2}`。

#### GET /api/v1/runs/{run_id}/read-evidence

**请求参数**：`run_id`（path / string / 必填 / — / 8 位十六进制 / `a1b2c3d4`）。

**响应字段**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` / `task_id` | string | 是 | run / 所属任务 id；task 缺失时 `task_id` 返回空串 |
| `required_reads` | array[string] | 是 | 已登记的必读 kind，默认 `spec` / `api` / `requirement`，按 `gate_tier` 取 `TIER_KINDS` |
| `read_ok` | array[string] | 是 | 已落 evidence 且指纹一致的 kind |
| `missing` | array[string] | 是 | 未落 evidence 或文件缺失（`MIO_READ_GATE_MISSING=block` 时）的 kind |
| `missing_files` | array[string] | 是 | 已登记但文件不存在的 kind |
| `stale` | array[string] | 是 | 文档被改动、`document_version` 指纹不符的 kind |
| `enforced` | bool | 是 | `bool(required) and gate_enabled()`；`MIO_READ_GATE=0/off` 可关闭 |
| `passed` | bool | 是 | `not missing and not stale` |
| `gate_kinds` | array[string] | 是 | 当前生效的必读项（`MIO_READ_GATE_KINDS` 可改） |

**错误码**：404（run 不存在）。**示例**：`{"run_id":"a1b2c3d4","task_id":"d468e633","required_reads":["spec","api","requirement"],"read_ok":["spec"],"missing":["api"],"missing_files":[],"stale":["requirement"],"enforced":true,"passed":false,"gate_kinds":["spec","api","requirement"]}`。

#### GET /api/v1/runs/{run_id}/diff-evidence

**请求参数**：`run_id`（path / string / 必填 / — / 8 位十六进制 / `a1b2c3d4`）。

**响应字段**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` / `task_id` | string | 是 | run / 所属任务 id |
| `mode` | string | 是 | 实际生效档位 `off` \| `warn` \| `block` |
| `would_block` | bool | 是 | `verdict.blocked`；仅 `block` 档且命中零改动时为 `true` |
| `skipped` | bool | 是 | 因豁免 / 无法采集而跳过判定 |
| `reason` | string | 是 | 原因码，见 `Verdict.reason` 枚举全集 |
| `files_changed` | int | 是 | 本次后快照变更文件数，最小 0 |
| `before` / `after` | object \| null | 是 | 前 / 后快照，取自 `Run.diff_summary`；未采集为 `null` |
| `changed` | bool | 是 | `Run.diff_summary.changed`，由 `_apply_diff_gate` 写入 |

`Verdict.reason` 全集：`gate_off` / `no_task` / `no_workspace` / `task_exempt` / `task_kind_exempt` / `concurrent_run` / `collect_failed` / `no_before_snapshot` / `has_changes` / `empty_diff_warn` / `empty_diff`。调用方须能处理未知取值（按「跳过判定」处理）。

**错误码**：404（run 不存在）。**示例**：`{"run_id":"a1b2c3d4","task_id":"d468e633","mode":"warn","would_block":false,"skipped":false,"reason":"empty_diff_warn","files_changed":0,"before":{"head":"9f2c1ab","porcelain":"","files":0,"insertions":0,"deletions":0,"paths":[]},"after":{"files":0,"insertions":0,"deletions":0},"changed":false}`。

#### GET /api/v1/board/diff-gate-stats

**请求参数**：本接口不接收 path / query / body / header 参数，全部输入为库内 `Run` 表当前快照；`mode` 由环境变量 `MIO_DIFF_GATE` 决定并回显。

**响应字段**（12 个顶层键为本次源码复核 `api/board.py:371-386` 补齐的确切 schema，需求 §8.2 第 3 条【待确认】已收口。主理人复核注：本章初稿记 13 字段属误数，源码逐行核为 12 键，验收按 12 键逐键断言）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | string | 是 | `diff_gate.gate_mode()` 当前档位 |
| `scanned_runs` / `judged_runs` / `unjudged_runs` | int | 是 | 扫描总数 / `diff_summary` 非空数 / 为空数 |
| `by_reason` | object | 是 | `reason → 数量`，对应 M5 跳过原因分布 |
| `blocked_run_count` | int | 是 | `rejections>0` 的 run 数（M1 代理） |
| `blocked_runs` | array | 是 | 前 50 条，含 `run_id/task_id/agent_name/rejections/state/exit_code/finished_at/annotation` |
| `warned_run_count` | int | 是 | `reason=empty_diff_warn` 的 run 数 |
| `skipped_run_count` | int | 是 | `reason` 以 `gate_off/no_/task_/collect_/concurrent_` 开头的 run 数 |
| `suspected_false_positive_count` | int | 是 | `rejections>0` 且 `exit_code==0` 的 run 数（M3 疑似误拒信号） |
| `suspected_false_positive` | array | 是 | 前 50 条，含 `run_id/task_id/rejections` |
| `note` | string | 是 | 说明 M2 / M3 需人工回看；`warn` 档不产生拦截记录 |

**错误码**：本接口不返回 4xx / 5xx 业务错误；库查询异常按 `RequestIDMiddleware` 兜 5xx，按 `X-Request-ID` 查 `/api/v1/logs`。**示例**：`curl -s http://127.0.0.1:48620/api/v1/board/diff-gate-stats | python -m json.tool`。

#### PATCH /api/v1/tasks/{task_id}（含 `diff_gate_exempt`）

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|---|---|---|---|---|---|---|
| `task_id` | path | string | 是 | — | 8 位十六进制 | `d468e633` |
| `diff_gate_exempt` | body | bool | 否 | `false`（新建任务）；存量任务迁移时被置 `1` | `true` = 本任务跳过空 diff 判定（`reason=task_exempt`），可回溯 | `true` |
| `doc_gate_exempt` | body | bool | 否 | `false` | `true` = 跳过 `LIFECYCLE_GATE` 阶段文档门控 | `false` |
| `workspace` | body | string | 否 | 保持原值 | 设为空串等价于窄门禁豁免（`reason=no_workspace`） | `E:/work/code/agent-dev/mio-taskhub` |
| `gate_tier` | body | string | 否 | 保持原值 | `light` \| `standard` \| `high`；显式降级必须给 `gate_tier_reason` | `standard` |
| `title` / `description` / `priority` / `labels` / `files` / `deliverables` / `depends_on` | body | 各类型 | 否 | 保持原值 | `depends_on` 变更触发环检测；`files`/`labels` 变化触发 `upgrade_tier` 只升不降 | `{"title":"修复染色"}` |

**响应字段**：返回体是 `_task_detail()` 全量详情（`api/tasks.py:446`），与窄门禁相关的字段为 `id` / `diff_gate_exempt` / `doc_gate_exempt` / `doc_paths` / `gate_tier` / `gate_tier_reason`。

**错误码**：404（任务不存在）；400 `gate.tier_downgrade_reason_required`（显式降级未给 reason）；422（`depends_on` 非法引用或成环）。**示例**：`curl -X PATCH "http://127.0.0.1:48620/api/v1/tasks/d468e633" -H "Content-Type: application/json" -d '{"diff_gate_exempt": true}'`。

#### POST /api/v1/tasks/{task_id}/doc/{kind}/status

**请求参数**：`task_id`（path/string/必填/8 位十六进制）、`kind`（path/string/必填/必须在 `doc_lifecycle.DOC_LIFECYCLE` 中，否则 400）、`state`（body/string/必填/目标生命周期态，`validate_transition` 严格向前，不许回退 / 跳级 / 终态改判）、`note`（body/string/否/`""`/ADR superseded 的替代编号）、`force`（body/bool/否/`false`/`true` 可绕过质量门与棘轮，须留痕）。

**响应字段**：`{kind, status:{state,at,note}, doc_paths, quality}`，`at` 为 ISO UTC；`quality` 为本次质量报告，无质量规格的 kind 或未触发门控时为 `null`。

**错误码**：400（kind 无生命周期）；404（任务不存在）；422 非法生命周期转移（字符串 detail，含 `allowed_next` 引导）；422 `{message,quality}`（质量门：`errors!=0` 或 `score<MIN_SCORE`，requirement/spec/api=80）；422 `{message,ratchet,hint}`（棘轮：指标低于历史最好值）。**示例**：`{"detail":{"message":"quality gate: api has 2 blocking issue(s)...","quality":{"score":45,"errors":["..."],"warns":[]}}}`。

#### POST /api/v1/tasks/claim

**请求参数**：`agent`（query/string/必填/已注册但非 ONLINE 的 agent 不得领新任务）、`agent_type`（query/string/否/回查 `Agent.agent_type`/用于关联度排序）、`task_id`（query/string/否/指定领取，主任务不可被普通 worker 领走）、`project_scope`（query/string/否/逗号分隔多值）、`project`/`workspace`/`files`（query/string/否/仅当任务对应字段为空时回填，不覆盖已有值）。

**响应字段**：`{id, task_id, state:"claimed", agent_name, attempt, required_reads, required_fr, documents, read_evidence_required, readiness, route}`，其中 `documents` 明确标注「预览不等于已读」，`readiness` 含 `required_reads` / `missing` / `completion_checks`（`result_not_empty` / `deliverables_exist` / `verification_passed` / `no_unresolved_high_deviation` / `not_zero_diff`），`route` 为 TH-01 路由决策，异常降级 `null`。

**错误码**：204（无可用候选任务）；404（指定 `task_id` 不存在）；409 `gate.doc_missing`（`MIO_CLAIM_GATE=block` 下领取就绪度不满足）；409 `decompose.children_incomplete`（主任务的子任务未完成）。**示例**：`curl -X POST "http://127.0.0.1:48620/api/v1/tasks/claim?agent=opencode"`。

#### GET /api/v1/tasks/{task_id}/doc

**请求参数**：`task_id`（path/string/必填）、`kind`（query/string/必填/必须在 `doc_paths.DOC_KINDS`，否则 400）、`run_id`（query/string/否/传了则调用 `record_read()` 落 ReadEvidence，`(run_id,kind)` upsert）、`agent`（query/string/否/写入 ReadEvidence 的 `agent_name`）。

**响应字段**：`{kind, path, content, truncated, missing, status, [read_evidence]}`；`content` 超过 200000 字符截断；`read_evidence` 含 `{kind,run_id,task_id,document_version,requirement_ids,recorded_at}`，`document_version = "sha256:" + hexdigest`。

**错误码**：400（kind 不在 `DOC_KINDS`，或相对路径且无 workspace 返回 `missing=true` 文本提示而非抛错）；404（任务不存在 / 该 kind 未登记）。**示例**：`curl -s "http://127.0.0.1:48620/api/v1/tasks/d468e633/doc?kind=spec&run_id=a1b2c3d4&agent=opencode"`。

---

## 3. 鉴权与权限

### 3.1 认证流程

| 环节 | 实现 | 码位 |
|---|---|---|
| token 来源 | `get_token()` 优先 `MIO_TASKHUB_TOKEN`，其次 `--token`，都没有则 `generate_token()`（`secrets.token_urlsafe(24)`）并打印 | `main.py:25-30`、`476-482` |
| HTTP 认证 | `make_auth_middleware()`：`app.state.auth_token` 为空直接放行；否则仅对 `request.url.path.startswith("/api/")` 校验 `Authorization: Bearer <token>`，用 `secrets.compare_digest` 常量时间比较；不匹配 401 `{"detail":"unauthorized"}` | `main.py:33-44`、`365-366` |
| Web UI 与文档页免认证 | `/`、`/docs`、`/redoc`、`/openapi.json`、`/dashboard`、`/landing`、`/healthz`、`/readyz`、`/metrics` 不以 `/api/` 开头 | `main.py:38` |
| WebSocket | 不走 HTTP 中间件，在 `websocket_endpoint` 内单独校验 `?token=` 或 Authorization Bearer，失败 `close(code=4401)` | `main.py:373-390` |
| 非 loopback 启动守卫 | `net_guard.is_loopback_bind(args.host)`：仅 `127.0.0.0/8` 与 `::1` 判为 loopback；非 loopback 且 `get_token()` 为空 → 直接 `SystemExit` 拒绝启动，除非 `MIO_TASKHUB_ALLOW_INSECURE_NETWORK` ∈ `1/true/yes/on`（打印 WARNING 但仍启动） | `main.py:484-498`、`net_guard.py:57-75` |
| 默认态 | `app.state.auth_token = os.environ.get("MIO_TASKHUB_TOKEN","")`（`main.py:365`），默认空 → 本地单用户默认无认证；`--auth` 或显式设环境变量才启用 Bearer | `main.py:365`、`476-482` |

`d468e633/api.md`（approved，2026-10-20 v1.1）写「无认证、401 不使用」，描述的是默认无 token 的本地态，不是「没有认证机制」。该文档与 `main.py` 现实现的差异标【待确认】。

### 3.2 权限模型

本项目**没有 RBAC / ABAC，没有多租户，没有角色表**。这不是遗漏，是「本地单用户、零联网」定位的决定（事实包 §1 明确非目标；需求 §6 N8）。权限由三层替代：

| 层 | 机制 | 权限点 / 判定 | 越权结果 |
|---|---|---|---|
| 网络边界 | 默认 loopback；非 loopback 强制鉴权 | `net_guard.is_loopback_bind` | `SystemExit` |
| 资源所有权 | run 级 `assert_run_owner(run, agent)` | `agent` 传入时校验 `Run.agent_name`；不传跳过（已知限制，彻底关闭需 run 级凭证，列后续任务） | 403 `run.not_owner` |
| 操作风险 | `mcp_risk.classify/check` 统一执行层 | destructive 两条：`DELETE /tasks/{id}`、`POST /tasks/{id}/doc/{kind}/status`；默认拒绝，`MIO_MCP_ALLOW_DESTRUCTIVE=1` 放行 | MCP 侧拒绝并给 reason |
| 策略确认 | `policy_guard.guard_action("taskhub:delete-task", confirm)` | 破坏性操作需 `confirm=true` | 409 `policy_risk_high` |
| 自审 / 自确认隔离 | `submit_review`、`confirm_high_risk` | reviewer / confirmer 为最近执行者 → 409 `review.self_approval` / `gate.self_confirmation` | 409 |
| 出站 SSRF | `net_guard.check_outbound_url` | 默认拒绝 loopback / private / link-local / reserved / multicast / unspecified；字面量 + DNS 解析双重判定；`allow_private=true` 显式放开 | cron webhook 记 `WebhookFailed` |
| 文件系统越界 | `_resolve_within_workspace(base, path)` | `target.relative_to(base)` 抛 `ValueError` → `invalid path: escape workspace` | 400 |

`mcp_risk` 的 destructive 清单是刻意收窄的：`update_task` / `move_to_stage` 归 write 放行，因为它们本身受状态机与阶段门控约束，不是删除也不绕过门控（`mcp_risk.py:23-33`）。

### 3.3 越权防护与敏感数据处理

- 所有权先于状态校验：`submit_result` 先 `assert_run_owner` 再 `assert_run_submittable`（`runs.py:323-326`），避免把「别人的 run 状态」泄漏给未授权调用方。
- 认证范围仅 `/api/*`；`/openapi.json` `/docs` `/dashboard` 免认证，靠默认 loopback 约束。`main.py:270-277` 有 7 个 router 挂载时未显式加 `/api/v1` 前缀，其自身是否带 `/api/` 前缀需核对，直接影响是否落在认证范围内，标【待确认】。
- 敏感数据：`Snapshot.brief()` 不返回 `error` 绝对路径（`diff_gate.py:63-72`）；`d468e633/api.md` 约定绝对路径不出现在响应体、git stderr 不回显、日志不记录 `result` 正文与 diff 明细。
- 文档路径：`doc_paths` / `doc_statuses` 存相对路径，读写经 `_resolve_within_workspace()`，逃逸 400（`task_documents.py:309-320`）；`completion_gate._safe_rel` 另拦绝对路径与 `..`（`completion_gate.py:97-104`）。
- 单 token 无过期、无轮换；无 cookie 故无 CSRF；SQLModel ORM 参数化防注入；DOMPurify 用于前端渲染。

---

## 4. 异步任务与守护线程

### 4.1 七个业务守护线程（`start_background_jobs()`，`background.py:639-675`）

| 线程（register 名） | 周期 | 职责 | 失败影响 | 开关 |
|---|---|---|---|---|
| `heartbeat`（HeartbeatSweep） | `poll_interval=10.0s`（`heartbeat.py:59`）；`DEFAULT_TIMEOUT_SECONDS=360`，可由 `MIO_TASKHUB_TIMEOUT_SECONDS` 覆盖 | 扫描 claimed/running run，`_on_timeout` 判死回收：区分 `never_started`（progress=0，不消耗 attempt，有界 bounce）/ `agent_offline` / `heartbeat_timeout`，写 `reaper_decision` 证据 | run 无法自动回收，卡死任务；`thread_failure` 记连续失败 | 无独立开关 |
| `scheduler`（Scheduler） | `interval=30.0s`（`background.py:687`）；内部 `AGENT_TIMEOUT_SECONDS=180` | `_scheduler_tick` 四步：`_mark_stale_agents` → `_requeue_retries` → `_release_dependencies` → `_assign_to_idle_agents`；`grab_mode=true` 任务不进派单候选 | 无自动派单 / 依赖不放行 / 重试不退避 | 无独立开关 |
| `idea-review`（IdeaReviewScanner） | 扫描器自定 | 想法评审扫描 | 冻结项，`MIO_IDEA_REVIEW_ENABLED` 默认关 | `MIO_IDEA_REVIEW_ENABLED` |
| `mio-digest`（MioDigestJob） | job 自定 | Mio 运行时摘要 | 观测旁路，不影响主链路 | 默认仍开，不在 8 项冻结清单 |
| `mio-contract`（ContractJob） | job 自定 | Mio 契约作业 | 观测旁路 | 同上，默认仍开 |
| `mio-outcome`（MioOutcomeJob） | job 自定 | 消费 `MioOutbox` 把结果写回 runtime（`key=run:<run_id>:outcome` 唯一，幂等） | 写回延迟，任务已完成不受影响 | 无独立开关 |
| `materials-ingest`（MaterialIngestJob） | job 自定 | 素材摄取 | 素材库不更新 | `MIO_MATERIALS_AUTOSTART`（默认 `1`，置 `0/false/no/off` 不启动，`background.py:665-674`） |

### 4.2 lifespan 内另起的观测 / 运维线程

| 线程 | 周期 | 职责 | 注册到 ThreadRegistry |
|---|---|---|---|
| `backup`（SQLiteBackup） | `DEFAULT_INTERVAL=3600s`（`ops/backup.py:16`） | SQLite Online Backup API；`keep_hourly=24` + `keep_daily=7` = **31 份**，`_prune()` 自动清理最旧 | 是（`main.py:83`） |
| `git-sync` | worker 自定 | `start_git_sync_worker()` / `stop_git_sync_worker()` | 否，lifespan 单独 stop |
| `night-runner` | 窗口驱动 | `start_night_runner()` / `stop_night_runner()` | 否，单独 stop |
| `cron-engine` | `POLL_INTERVAL=10s` | `start_cron_engine()` / `stop_cron_engine()` | 否，单独 stop |
| `slo-snapshot` | `time.sleep(300)` | `capture_slo_snapshot()` | 否 |
| `dep-persist` | `time.sleep(300)` | `DepMetricsPersist.snapshot()` + `cleanup()`（补 P1-OBS，防 `depmetricssnapshot` 无界增长） | 是（`main.py:123`） |
| `insights-eval` | `asyncio.sleep(60)` | `InsightsEngine.evaluate` + `InsightsRemediator.consume` | 是（`main.py:151`） |
| `remediation-eval` | `asyncio.sleep(120)` | `evaluate_stalled_tasks` → `requeue_task`（取前 5 条） | 是（`main.py:168`） |
| `ws-push` | `start_ws_push()` 自定 | `metrics_snapshot` / alerts 推送 | 否 |
| observer autostart | `threading.Timer(15.0)` | `_mio_core.observer_ensure()` | 否；`MIO_OBSERVER_AUTOSTART` **默认关** |
| update service | `delay=20.0`，`interval_h=MIO_UPDATE_INTERVAL_H`（默认 6） | 自更新后台检查 | 否 |

口径差异：事实包 §3 记「7 个守护线程（heartbeat / scheduler / idea-review / backup / git-sync / night-runner / cron-engine）」，与本次源码复核 `start_background_jobs()` 实际注册的 7 个（上表 §4.1）不一致；backup / git-sync / night-runner / cron-engine 是在 `main.py:lifespan` 单独启动的，其中只有 backup 与 dep-persist / insights-eval / remediation-eval 进入 `ThreadRegistry`。两套清单标【待确认】。

### 4.3 关停顺序

`ThreadRegistry.stop_all()` 按 `reversed(self._entries)` 反向停止，逐条 `obj.stop()` + `thread.join(timeout=5)`，最后 `self._entries.clear()` 清空注册表（`background.py:605-614`）。`lifespan` 退出序列（`main.py:186-200`）：先对 `app.state.background` 逐个 `job.stop()`，再 `_thread_registry.stop_all()`，再 `stop_git_sync_worker()` / `stop_night_runner()` / `stop_cron_engine()`，再 `engine.dispose()`，最后 `shutdown_otel()`。`ThreadRegistry` 只解决优雅关停，不解决运行期隔离：主进程崩溃则全部后台任务停止。

### 4.4 cron 与 scheduledjob

- `scheduling/cron_engine.py` 自管线程：`CronEngine._thread`（daemon，name=`cron-engine`，`POLL_INTERVAL=10`），不进 `ThreadRegistry`；`croniter==6.2.4` 解析 5 字段 cron，按本机时区解释、返回 UTC；`apscheduler==3.11.3` 在钉死依赖内但本次未复核到调用点【待确认】。
- webhook 动作：`httpx.Client(timeout=job.timeout_seconds or 30)`，先过 `net_guard.check_outbound_url`（SSRF），2xx 之外抛 `WebhookFailed` 并如实记 `last_status=ERROR` + `ScheduledJobExecution`，不做假绿。
- `ScheduledJob.enabled` 现状：`seed.py` 只播种 1 个 `idea-generate`，`enabled=_idea_autogen_enabled()` 默认 `False`（`MIO_IDEA_AUTOGEN` 才开，`seed.py:286-292`）；启动时 `seed_idea_generate_job()` 还会把仍开启的老 job 关掉（`:252-257`）。事实包 §6 N4 记「3 个 scheduledjob 现全部 `enabled=0`，其一 `ERROR(502)`」，属**历史库状态**，另外 2 条的播种路径本次未复核到，标【待确认】。
- 冻结处置：3 个 job 保持 `enabled=0`，不重启调度（N4 定时创意生成属 FZ-1）；代码 / 接口 / 文档保留，停投入、停调度、移出主链路。事实包 §1 明确「勿擅自扩大范围」：`MioDigestJob`、`ContractJob` 默认仍开，它们是进程内 job，与 DB 定时任务的冻结不冲突。
- F9 `night_runner`：配置默认 `"enabled": False`（`night_runner.py:25`），`_spawn` 有 precheck（首 token 是否在 PATH / python 脚本是否存在），失败记 warning（`:149`）。处置：保持关闭，仅保留手动试跑（N5），不修重启。

---

## 5. 幂等与事务

### 5.1 异步与一致性方案

| 场景 | 异步方案 | 重试策略 | 补偿机制 | 一致性级别 |
|---|---|---|---|---|
| 状态与事件写入 | 同一 SQLite 库事务内写 `Task`/`Run` + `Event`（`emit_event` 只 `db.add` 不 commit，随业务 `db.commit()`） | 无（同步事务） | 事务回滚即全部撤销 | 强一致 |
| WS 广播 | SQLAlchemy `after_flush` 收集 `Event`、`after_commit` 逐事件 `broadcast_for_event`（`events.py:105-143`） | 无重试，异常静默并记 warning | 客户端按 `Event.seq` 增量订阅 `GET /events?after_seq=N` 补齐 | 最终一致（Outbox→WS） |
| 结果写回 mio-runtime | `MioOutbox` 表（`key=run:<id>:outcome` 唯一）由 `MioOutcomeJob` 消费 | outbox `attempts` / `next_at` 驱动 | 幂等键去重；失败不影响已完成的任务 | 最终一致（`submit_result` 在 commit 后才 `enqueue_outcome`，`runs.py:370-387`） |
| 定时 webhook | cron-engine 线程 + `ScheduledJobExecution` 执行日志 | `ScheduledJob.max_retries=3`（字段） | `last_status=ERROR` + `last_error` 留痕，不做假绿 | 最终一致 |
| ADR / idea 投影 | `OutboxEvent` 表 | `retry_count` / `max_retries=3` | 状态 `pending→processing→synced/failed` | 最终一致；自动清理状态标【待确认】 |
| 迟到结果纠正 | `late_result_recovery`（同步，在 `submit_result` 内） | 无 | `_is_system_timeout_failure` 判定后由 SYSTEM 改判回 COMPLETED | 强一致（同事务） |

### 5.2 幂等机制与代码依据

| 机制 | 实现 | 码位 |
|---|---|---|
| ReadEvidence upsert | `record_read()` 按 `(run_id, document_kind)` 查 `latest_evidence`，存在则原地更新 `document_version` / `read_at`，不存在新建；迁移补唯一索引 `uq_read_evidence_run_kind` | `read_evidence.py:140-170`、`migrations.py:85-94` |
| 内容指纹 | `document_version = "sha256:" + hexdigest`（`file_fingerprint` 对文档文件按 65536 字节分块）；只覆盖被读文档，不覆盖代码 | `read_evidence.py:69-80` |
| 领取幂等 | `find_existing_run()` 返回该 agent 已有 claimed/running run；`atomic_claim()` 用条件更新 `WHERE state=QUEUED` 抢占，`rowcount!=1` 回滚返回 None | `runs.py:80-84`、`claim.py:143-157` |
| 重复提交 | `assert_run_submittable()` 仅拦「已交接的成功结果」：`run.state=FINISHED` 且 `exit_code==0` 且 `task.state==COMPLETED` → 409 `run.result_already_submitted` | `runs.py:46-75` |
| 事件增量订阅 | `Event.id` 自增主键即单调 `seq`；`GET /events?after_seq=N` 返回 `id>N` 升序分页并给 `next_seq` | `models.py:373-380`、`api/events.py:15-31` |
| 棘轮基线 | `RatchetBaseline` 唯一键 `(task_id,kind,metric)`，`check_ratchet` 只升不降，`bumps` 先 commit 再判 `blocked` | `ratchet.py:69-98`、`migrations.py:73-82` |
| 标注合并 | `merge_annotations` 同一 `code` 只保留最新一条，防同 run 反复提交刷屏 | `diff_gate.py:345-371` |

### 5.3 事务边界与 N6 判据

- 事实包 §3 / N6 记「59 处分散 `commit`」。本次源码复核实测 `api/*.py` 内 `db.commit()` **72 处**，全仓 `mio_taskhub/` 的 `.commit()` **128 处**（含 `conn.commit()` / `session.commit()`）。三个数字口径不同，标【待确认】；N6 结论不受影响。
- N6「不做 RUoW 事务架构大手术」判据：P1-4 并发实验零重复 / 100% 成功率，任务 837b156f 已关闭；触发条件为并发 >1000 / 跨进程 / 多租户。当前保持分散 commit，不做统一工作单元重构。
- 分散 commit 的已知代价：`_apply_diff_gate` 放行路径与拦截路径都显式 `db.commit()`（`runs.py:451`、`478`），`set_doc_status` 棘轮段也先 `db.commit()`（`task_documents.py:651`）。这些是前置门控，主流程的 `db.commit()` 在 `runs.py:367`，抛 HTTPException 时到不了，所以必须各自落库。

### 5.4 四个必须如实登记的事务口径

| 项 | 实测行为 | 处置 |
|---|---|---|
| 并发 `set_doc_status` 对 JSON 列读-改-写丢更新但接口仍返回 200 | `statuses = dict(t.doc_statuses)` 读 → 改 → `t.doc_statuses = statuses` → `db.commit()`，无 CAS / 无锁 / 无独立表（`task_documents.py:588,660-669`）。两个并发请求各自基于旧副本写回，后写覆盖先写，且两次都返回 200 与「成功」状态对象，属**假成功** | 该端点必须串行（单写者锁或把 `doc_statuses` 迁到独立行表用条件更新）。登记为必修候选，触发条件：出现多写者并发命中。当前单用户单进程、写频次低，登记延后 |
| `submit_result` 成功与失败都置 `progress=100` | `runs.py:349` 无条件 `run.progress = 100` | 因此 `progress==100` 不能作为重复提交判据；判据固定为 `exit_code==0 且 task.state==COMPLETED` |
| 重复提交判定用 `exit_code==0 且 task.state==COMPLETED` | `assert_run_submittable()`，`runs.py:63-75` | 用 `progress==100` 会把「失败后迟到的成功」误判成重复提交，而那正是 `late_result_recovery` 必须放行的场景 |
| `late_result_recovery` 必须保留 | reaper 判超时把 run 置 finished 后，Agent 迟到的提交仍返回 200，由 `_handle_success` 依据 `_is_system_timeout_failure(db,task)` 决定是否从 FAILED 改判回 COMPLETED（`runs.py:522-582`）；要求最后一条 `TaskEvent` 为 `to_state=FAILED` 且 `actor_type=SYSTEM` 且 reason 以 `agent_offline:` / `heartbeat_timeout:` 开头 | `tests/test_timeout_misjudge.py` 三个用例是护栏；`assert_run_submittable` 不能简单「finished 就拒绝」，否则打断这条既有链路 |

---

## 6. 限流与容错

### 6.1 限流

| 项 | 实测 |
|---|---|
| 中间件 | `RateLimitMiddleware`（`app.add_middleware` 顺序：`GZipMiddleware` 最内层 → `RateLimit` → `RequestID`，`main.py:212-214`） |
| 默认阈值 | `_DEFAULT_RATE_LIMIT = 120` req/min，`MIO_TASKHUB_RATE_LIMIT` 可覆盖（`middleware.py:87,99`） |
| 窗口 | 滑窗 60s：`cutoff = now - 60`，`bucket[:] = [t for t in bucket if t > cutoff]`（`middleware.py:116-117`） |
| 分桶键 | `key = f"{client_ip}"`（`middleware.py:106,112`）。**仅 client IP，F11 单桶**，同机多 Agent 共用一个 120 req/min 桶，密集提交时互相挤兑 |
| 作用范围 | 仅 `request.url.path.startswith("/api/")`；非 `/api/` 直接放行（`middleware.py:103-104`） |
| 豁免 | `any(x in path for x in ("/healthz","/readyz","/metrics"))`（`:109`）。该判断在 `/api/` 前缀判断之后，而三个探针本身是根路径、不以 `/api/` 开头，天然不受限流；此处豁免实际只对包含这些子串的 `/api/` 路径生效，属子串匹配而非精确匹配 |
| 429 响应体 | `{"error":"rate_limited","retry_after_seconds":int(bucket[0]-cutoff)+1}`，带 `Retry-After` 头（`:120-124`）。与 401、门控 422 结构都不同 |
| 短期缓解 | 多 Agent 密集提交时把 `MIO_TASKHUB_RATE_LIMIT` 调大；三个探针天然豁免 |
| 处置 | **登记延后**（F11）。理由：单用户本地工具、默认 loopback、真实并发写者少；限流分桶改造属结构性投入，与 8 项冻结「停投入」冲突；复审条件为出现第二个真实用户或多写者密集命中 |

### 6.2 容错

| 场景 | 行为 |
|---|---|
| `/readyz` | `db.check_connection()` 执行 `SELECT 1`（`db.py:82-89`）；异常返回 503、`status=degraded`、`db=error`，并带 `backups.{count,latest}`（`main.py:408-418`） |
| `/healthz` | 只回 `{status:"ok"}`，不查库 |
| sqlite 锁竞争 | `PRAGMA journal_mode=WAL`、`busy_timeout=5000ms`、`synchronous=NORMAL`（`db.py:31-37`） |
| 广播失败 | `broadcast_for_event` / `broadcast_json` 异常静默；`_broadcast_after_commit` 逐事件 try/except，绝不让异常冒泡出 `db.commit()` 导致调用方误判写入失败并重复提交（`events.py:117-135`） |
| 观测旁路失败 | lifecycle 埋点、lessons、`enqueue_outcome`、`_e3_log_view` 全部 fire-and-forget，异常吞，不影响主流程 |
| 无熔断 / 无降级 | v3 审计定性结论；当前没有熔断器与降级开关，只有 fail-open 判据（`diff_gate`）与 503 探针 |

### 6.3 外部依赖治理

| 依赖 | 超时 | 重试 | 熔断 | 降级方案 | 成本记录 |
|---|---|---|---|---|---|
| git 只读子进程（`diff_gate._git`） | `GIT_TIMEOUT_SEC=5`（`diff_gate.py:29`），`subprocess.run` 参数数组传入、不走 shell、`CREATE_NO_WINDOW` | 无 | 无 | `snapshot()` 把所有异常转 `ok=False`，`check_diff_gate` 返回 `collect_failed` 跳过判定；`create_run` 拍 before 失败只记 `collect_error`，不阻断 claim | `Run.diff_summary` + `Run.annotations` 落判定原因 |
| SQLite（本机文件） | `busy_timeout=5000ms` | 无（CAS 靠条件更新） | 无 | `/readyz` 503 | `DepMetrics` 记录 select/insert/update/delete 延迟（`db.py:61-80`） |
| cron webhook（httpx） | `job.timeout_seconds or 30` | `ScheduledJob.max_retries=3`（字段）；堆积保护 `MIO_CRON_PENDING_GUARD` 默认开，未认领任务 ≥3 跳过生成 | 无 | SSRF 拒绝或 4xx/5xx → `WebhookFailed` → `last_status=ERROR` + 执行日志 | `ScheduledJobExecution` |
| SQLite Online Backup | 无网络调用 | 无 | 无 | 线程失败记 `thread_failure` | `get_backup_status()` 给份数与最新时间，`/readyz` 回显 |
| mio-agent-runtime 子进程（冻结项） | 冻结前约定 `MIO_MEMORY_*`：不可达 503 `memory_unavailable`、超时 504 `memory_timeout`、RPC 错误 502 `memory_rpc_error`、参数 422 | `MIO_MEMORY_MAX_RESPAWN=3` | 无 | 降级到结构完整性校验；`MIO_MEMORY_RATE_LIMIT=60`/min | 冻结不投入，解冻前需单独给超时 / 重试 / 降级 / 成本上限方案 |
| 大模型 | **无**。门控链路不调 LLM，`test` 只是文档 kind，系统不跑测试、不做静态分析 | 不适用 | 不适用 | 不适用 | 无按 token 计费。若未来引入 LLM 判定，架构 §5 要求补超时、重试上限、降级到结构完整性校验、单次 / 单任务成本上限四项后才可进主链路 |

### 6.4 可观测性现状

| 维度 | 现状 |
|---|---|
| 日志规范 | `RequestIDMiddleware` 注入 `X-Request-ID`，5xx 记 `logger.error("request_error")` 带 `request_id/path/status/duration_ms`，异常记 `logger.exception`（`middleware.py:69-77`）；`/api/v1/logs` 可按 level / logger / limit 过滤（环形 1000 条）。F3 结构化日志丢字段 → 登记延后 |
| 链路追踪点 | 全链路 `X-Request-ID`；`Event.seq` 单调增量；`TaskEvent` 状态迁移留痕；`after_flush`/`after_commit` 广播钩子；`DepMetrics`（sqlite op 延迟）；lifecycle 埋点 `agent_ready` / `probe_process_alive`（fire-and-forget）。F8 otel 未生效（`init_otel` / `instrument_*` 在 `main.py:52-55` 被调用，但埋点未生效）→ 冻结 |
| 核心指标 | `/metrics` Prometheus 文本：`taskhub_tasks_total{state}`、`event` / `agent` 计数、`taskhub_uptime_seconds`、`taskhub_slo_*`、`taskhub_dep_latency_*`、`taskhub_thread_alive` / `heartbeat_age`；窄门禁侧 `board/diff-gate-stats` 提供 M1~M5 代理指标 |
| 告警阈值 | SLO 目标（HTTP 99.9% / 任务 99.0% / MCP 99.5% / P50<100ms / P99<2000ms / 5xx<0.1%）为**目标值非实测**，且该域已冻结；`docs/SLO-SLI.md` 与 `packaging/alerts.yml` 的 24 条告警规则为冻结保留内容，不作为现行验收指标。F10 告警无处置流程 → 冻结 |

---

## 7. 四重质量闭环的服务端实现要点

| 层 | 落点 | 性质 | 服务端强制点 |
|---|---|---|---|
| 写时 lint（反馈不阻断） | `doc_quality.check_content`：`score = max(0, 100 - 20*errors - 5*warns)`；必备 H2 章节 / `min_table_rows` / 空壳占位 / 分点书写 / markdown 链接有效性；`MIN_SCORE = {requirement:80, spec:80, api:80}` | 启发式结构校验 | `PUT /doc` 响应挂 `quality`，不阻断写入（`task_documents.py:460`） |
| 状态门控（阻断） | `doc_lifecycle.validate_transition` 严格向前；推进 `review`/`approved`/`done` 要求 `errors==0` 且分数达标 | 确定性状态机 | 422 `detail={message, quality}`（`task_documents.py:633-638`）；`force=true` 绕过但留痕 |
| 棘轮基线（阻断） | `ratchet.check_ratchet`：`score` / `test_cases` 只升不降，`RATCHET_STATES=('review','approved','done','passed','failed')` | 确定性比较 | 422 `detail={message, ratchet, hint}`（`task_documents.py:653-657`）；`force=true` 绕过 |
| 读证 + 窄门禁（阻断 / 标注） | `read_evidence.check_read_gate`（`required_reads` 默认 `spec,api,requirement`；`document_version` 绑定 `(run_id,kind)`）；`diff_gate.check_diff_gate`（`off/warn/block` 默认 `warn`，`MAX_REJECTIONS=2`，`GIT_TIMEOUT_SEC=5`） | 服务端采集，不采信自报 | `submit_result` 必经路径：读证 422 `{message, required_reads, missing, stale, hint}`；零改动 422 `run.empty_diff`、连续超限 409 `run.empty_diff_repeated` |

配套落点与留痕：

| 项 | 实测 |
|---|---|
| `GET /api/v1/tasks/{id}/doc/{kind}/revision` | 返回 `{kind,quality,revision_prompt}`，逐条问题 + 章节写作指引 + 模板片段；无质量问题时 `revision_prompt=null`（`task_documents.py:710-726`） |
| `traceability()` | `doc_quality.py:368-374`，正则 `\bFR-\d+\b` 抽需求 FR-n，检查是否被 test 正文引用，返回 `{fr_total,fr_covered,fr_uncovered}`；挂在 `GET /doc/quality` 响应的 `traceability` 字段 |
| `force=true` 留痕 | **阶段门控**（`advance_stage` / `move_to_stage`）的 force 绕过 emit `Event(type="task_stage_gate_forced", entity="task")`（`task_stages.py:565-567`、`:645-647`）。**文档状态门控**（`set_doc_status`）的 force 绕过不 emit 该事件，而是在 `Event(type="task_doc_status")` 的 `payload.forced` 字段留痕（`task_documents.py:664-668`），棘轮基线抬高另 emit `task_ratchet_baseline`。两者是不同门控点的不同留痕方式，不可混用 |
| 两个口径 | 「服务端强制」= 门控位于 `submit_result` / `set_doc_status` / 阶段推进的必经路径，`git push --no-verify` 绕不过；「结构完整性校验」= `check_content` 只判断章节 / 表格 / 占位符等结构，判断不了内容是否真的对，对外不宣称内容质量，冻结项 FZ-7 明确「超出结构校验的文档质量阈值」不做 |

---

## 8. 窄门禁服务端实现规格（本期唯一投入项）

### 8.1 FR-B01~B07 → 服务端实现映射

| FR | 需求 | 源码实现 | 码位 |
|---|---|---|---|
| FR-B01 | 服务端自行采集改动证据，`Run.diff_summary` 只存摘要不存全文 diff，create_run 拍 before、submit 拍 after | `create_run()` 拍 before（失败只记 `collect_error`，不阻断 claim）；`_apply_diff_gate()` 拍 after 并写 `diff_summary`；`Snapshot.brief()` 只给 `{head,porcelain,files,insertions,deletions,paths[:50]}` | `runs.py:87-113`、`:421-430`；`diff_gate.py:63-72` |
| FR-B02 | 唯一拦截判据 `success==True` 且未豁免且可采集且 `changed==False` → 422 `run.empty_diff` | `check_diff_gate` 返回 `Verdict(blocked=True, reason="empty_diff")` 仅在 `block` 档；`_apply_diff_gate` 据此抛 `HTTPException(422)` | `diff_gate.py:337-340`；`runs.py:511-519` |
| FR-B03 | `MIO_DIFF_GATE=off/warn/block` 默认 `warn`，回退改 env 不需回滚代码或迁移 | `gate_mode()` 读环境变量，非枚举值按 `warn`；`off` 不采集不标注、`warn` 只写 annotations 并放行、`block` 才拒 | `diff_gate.py:210-213,291-293,331-340`；`runs.py:434-452` |
| FR-B04 | 只读统计端点 `GET /api/v1/board/diff-gate-stats` 纯统计不参与判定 | `diff_gate_stats()` 只读 `Run` 表聚合，无 emit、无标注、无流程分支 | `api/board.py:317-386` |
| FR-B05 | 拦截对人可见三链路 + 独立错误码 | ① `Event(type="run.result_rejected", entity="task")` → `task_update` WS 红 toast；② `HistoryEvent(type="rejected")` → 动态 tab 红行；③ `Run.annotations(level="block")` → 完成报告红框卡。`warn` 级只常驻提示行、不弹 toast、文案含「不影响放行」 | `runs.py:464-477`、`:436-446` |
| FR-B06 | 拒绝次数上限 `MAX_REJECTIONS=2`，不消耗 attempt，超限判本次执行失败 | `run.rejections += 1`；`rejections > MAX_REJECTIONS` 时置 `exit_code=1` / `state=FINISHED` / `result=verdict.message`，走 `_handle_failure` 重试，抛 409 `run.empty_diff_repeated` | `runs.py:455,480-509`；`diff_gate.py:33` |
| FR-B07 | 灰度观测 M1~M5，唯一成功判据落 M2 | `board/diff-gate-stats` 提供 M1（`blocked_run_count`）、M3 疑似（`suspected_false_positive_count`）、M5（`by_reason`）代理；M2 / M3 人工回看与 M4 可见性由 `docs/taskhub/diff-gate-grayscale-log.md` 承载 | `api/board.py:317-386` |

### 8.2 `diff_gate.py` 模块结构（371 行）

| 符号 | 职责 |
|---|---|
| `GIT_TIMEOUT_SEC = 5` / `MAX_PATHS = 50` / `MAX_REJECTIONS = 2` | git 子进程超时 / 变更路径上限 / 单次 run 允许被拒次数上限 |
| `GATE_MODES = ("off","warn","block")` / `DEFAULT_GATE_MODE = "warn"` | 三档，非枚举值按 warn |
| `EXEMPT_TASK_KINDS = ("idea_review","change_tracking")` | 无代码产出类型 |
| `ACTIVE_RUN_STATES = ("claimed","running")` | 并发判定用的活跃态（实际 `_has_concurrent_run` 用 `RunState.CLAIMED/RUNNING`） |
| `Snapshot` / `Snapshot.brief()` | 采集结果；`ok=False` 表示采集失败；brief 不含绝对路径等敏感信息 |
| `_git()` / `_numstat()` | 只读子命令 `rev-parse HEAD` / `status --porcelain` / `diff --numstat HEAD`；参数数组传入、不走 shell；二进制 `-` 不计行数但计文件数 |
| `snapshot(workspace)` | 采集一次；**任何异常转 `ok=False` + error，绝不向上抛**；`rc_st != 0` → `非 git 仓库`；空仓库（无 HEAD）不算失败，head 记空串 |
| `differs(before,after)` | 采不到信息时返回 `True`（视为有改动） |
| `_has_concurrent_run(db,task)` | 经 Task 表 join 找同 workspace 的其他活跃 run；`Run` 无 workspace 列 |
| `_before_snapshot(run)` | 从 `run.diff_summary['before']` 还原 Snapshot，缺失返回 `None` |
| `gate_mode()` | 读 `MIO_DIFF_GATE` |
| `check_diff_gate(db,task,run,body)` | 事实判定 + 策略判定收口，返回 `Verdict` |
| `merge_annotations(existing,new_items)` | 同 `code` 只保留最新一条 |

需求拆解 FR-B01 与 `narrow-gate-implementation-plan` 使用 `collect()` 作为采集函数名。**本次源码复核：`diff_gate.py` 没有独立的 `collect()` 函数**，采集由 `snapshot()` + `_numstat()` 完成；「`collect()` 抛错 → fail-open」在实现里体现为 `snapshot()` 返回 `ok=False`，`check_diff_gate` 返回 `reason=collect_failed`。标【待确认】为文档命名与实现命名差异。

### 8.3 `changed` 判据（`differs()`，`diff_gate.py:193-205`）

```
if not (before.ok and after.ok): return True        # 采不到信息一律视为有改动（fail-open）
if before.head != after.head: return True            # 有新 commit
if before.porcelain != after.porcelain: return True  # 工作区改动有变化（含新增未跟踪 ??）
return (after.insertions + after.deletions) > 0      # numstat 增删行合计
```

补充两点实测：`insertions`/`deletions` 来自 `git diff --numstat HEAD`（不是 `files` 字段），空仓库时该命令失败降级为 `(0,0,[])`，由 porcelain 兜底判定；任一快照 `ok=False` 直接返回 `True`，绝不因采集失败拦人。

### 8.4 `before` / `after` 双快照落 `Run.diff_summary`

| 时机 | 写入内容 |
|---|---|
| `create_run()`（`runs.py:103-111`） | 成功 → `{"before": snap.brief()}`（6 字段 + `paths[:50]`）；失败 → `{"before": None, "collect_error": snap.error}`；采集异常被 `except Exception: pass` 吞，绝不阻断 claim |
| `_apply_diff_gate()`（`runs.py:421-430`） | `summary["after"] = {"files":verdict.files_changed, "insertions":verdict.insertions, "deletions":verdict.deletions}`；`summary["changed"] = verdict.reason == "has_changes"`；`summary["reason"]`；`summary["mode"]` |
| `Run` 模型 | `diff_summary: Optional[dict] = Field(default=None, sa_column=Column(JSON))`，未采集为 `None` 不是 `{}`（`models.py:299-302`）；迁移 `_migrate_run_diff_gate` 补 `diff_summary` / `annotations` / `rejections` 三列，存量 run 留 `None` / `[]` / `0`（`migrations.py:297-314`） |
| `GET /tasks/{id}` | `runs[].diff_summary` / `annotations` / `rejections` 三个字段回显（`tasks.py:345-350`） |

口径差异：`d468e633/api.md` 第 194-195 行把 `before` / `after` 都描述为 `{head, porcelain, files, insertions, deletions}`；实测 `after` 只落 3 个字段（`files/insertions/deletions`），不含 `head` / `porcelain` / `paths`。标【待确认】。

### 8.5 豁免优先级（`check_diff_gate`，`diff_gate.py:283-340`）

开关 `off` > 无 task > 无 workspace > 显式豁免 > `task_kind` > 并发冲突 > 采集失败 > 无前快照。

| 序 | 条件 | `reason` | 行为 |
|---|---|---|---|
| 1 | `MIO_DIFF_GATE=off` | `gate_off` | 不采集、不拦、不标注 |
| 2 | `task is None` | `no_task` | 跳过 + 标注 |
| 3 | `task.workspace` 为空 | `no_workspace` | 跳过 + 标注 |
| 4 | `diff_gate_exempt=True` | `task_exempt` | 跳过 + 标注；存量任务由 `_migrate_task_diff_gate` 在首次加列时全量置 1（`migrations.py:212-227`） |
| 5 | `task_kind ∈ {idea_review, change_tracking}` | `task_kind_exempt` | 跳过 + 标注 |
| 6 | 同 workspace 存在其他活跃 run（≥2 活跃） | `concurrent_run` | 跳过 + 标注「快照会互相污染」 |
| 7 | `snapshot()` 返回 `ok=False`（workspace 不存在 / 非 git / git 不可用 / git 超时） | `collect_failed` | 跳过 + 标注，message 含「不影响放行」 |
| 8 | `run.diff_summary['before']` 缺失 | `no_before_snapshot` | 跳过 + 标注「无法判定是否零改动」 |
| 9 | before 快照本身失败 | 无独立 reason | `create_run` 记 `collect_error`；`_before_snapshot` 返回 None → 落到序 8；`differs()` 的 `not(before.ok and after.ok)` 也兜底返回 True |

以上任一命中一律 `Verdict(skipped=True, blocked=False)`，即 fail-open 跳过判定 + 标注 reason。测试是否通过 / diff 改动质量只标注不拦（产品决策 §2）。

### 8.6 三档、拒绝上限、422→409 升级

| 档 | 采集 | 命中零改动 | 标注 |
|---|---|---|---|
| `off` | 不采集 | 不拦 | 无 |
| `warn`（默认） | 采集，双快照落 `diff_summary` | `Verdict(reason="empty_diff_warn")`，放行 | `annotations` 写 `level=warn`，文案含「不影响放行」，不弹 toast |
| `block` | 采集 | `Verdict(blocked=True, reason="empty_diff")` → 422 | `annotations` 写 `level=block` |

拒绝升级：`run.rejections = (run.rejections or 0) + 1`；`if run.rejections > MAX_REJECTIONS:`（源码为 `>2`，即**第 3 次**）才置 `exit_code=1` / `state=FINISHED`、走 `_handle_failure`、抛 409 `run.empty_diff_repeated`。前 2 次是 422「请改完重试」，不消耗 attempt，同一 run 内可改完再交。源码注释（`runs.py:480-485`）明确「被拒 2 次以内可继续重试，第 3 次才是本次执行失败」。

口径差异：需求拆解 FR-B06 表格写「`≥2` → `exit_code=1`、state=FINISHED」，`d468e633/api.md` 第 101 行写「连续被拒达 2 次后本次执行判失败」；源码实际是 `>2`（第 3 次）。以源码为准，文档差异标【待确认】。

### 8.7 插入点、commit 坑、可见性事件、顺序不可交换理由

`submit_result` 实测链路（`api/runs.py:316-392`，门控段落在 `:323-344`）：

| 序 | 步骤 | 行号 | 顺序不可交换的理由 |
|---|---|---|---|
| 1 | `assert_run_owner(run, agent)` | 325 | 所有权必须先于状态校验，避免把「别人的 run 状态」泄漏给未授权调用方（源码注释 `:323-324`）。若后置，越权调用方能通过 409 / 422 反推他人 run 的状态 |
| 2 | `assert_run_submittable(run, task)` | 326 | 只拦「重复的成功提交」，必须在任何门控之前短路；用 `exit_code==0 且 task.state==COMPLETED`，不能用 `progress==100` |
| 3 | `success = body.get("success", True)` | 327 | 直接采信自报 `success: bool`；后续三道服务端口控据此决定是否触发 |
| 4 | `check_read_gate` → 422 | 329-333 | 读证纯查库、更便宜且更基础，先报读证问题（`:334-335` 注释）。读证不 spawn git 子进程，窄门禁判定最多 3–4 次 git 调用（每次 ≤5s）；先做便宜的可避免为「未读文档」的提交付采集成本 |
| 5 | `_apply_diff_gate` → `check_diff_gate` | 336-337 | 必须在读证之后（序 4 理由）、`completion_gate` 之前（序 6 理由）、任何状态落库之前（序 7 理由） |
| 6 | `completion_gate.check_submit` → `GateError` | 339-344 | `check_submit` 内部的 `collect_submit_problems` 调用 `is_zero_diff(run)`（`completion_gate.py:268`），而 `is_zero_diff` 读 `Run.diff_summary["changed"]`（`:214-221`），该字段由序 5 `_apply_diff_gate` 写入（`runs.py:427`）。**若序 6 先于序 5，`gate.no_changes` 判定会读到 before-only 的 `diff_summary` 而失真**；且 `check_submit` 会写 `run.evidence`（`:429`），先跑会让 422 拦截时留下半成品证据 |
| 7 | 写 `run.result` / `exit_code` / `finished_at` / `state=FINISHED` / `progress=100` | 345-349 | 任何门控通过后才写。若窄门禁放在状态落库之后，被拦的是「一个已经推进到 review 的任务」，口径污染且需回滚已写状态；`_apply_diff_gate` 抛 422 时主流程到不了 `:367` 的 `db.commit()` |
| 8 | `_handle_success` / `_handle_failure` | 353-358 | 状态机流转，含 `late_result_recovery`（§5.4），必须在门控之后 |
| 9 | `emit_event(type="task_result")` + `db.commit()` | 361、367 | 事务内写状态与事件；after_commit 钩子触发 WS 广播 |

需求拆解 FR-B02 给的插入点是「约 `:288` 后、`:289` 前」，实测是 `:336-337`（`runs.py` 现 636 行）。旧行号标【待确认】。

**抛 422 前必须 `db.commit()`**（`runs.py:478`，放行路径 `:451` 也有）：`db.get_session()` 是 `with Session(engine) as session: yield session`，退出只 close 不 commit（`db.py:56-58`）。`_apply_diff_gate` 是前置门控，抛 HTTPException 后主流程 `:367` 的 commit 不会执行。若不显式 commit，`Event(run.result_rejected)`、`HistoryEvent(rejected)`、`Run.annotations(level=block)`、`Run.rejections`、`Run.diff_summary` 全部随会话丢弃，AC-03 的查库证据 `SELECT * FROM event WHERE type='run.result_rejected' AND entity='task'` 查不到，唯一成功判据无法举证。这是 `d468e633/spec.md`「三个坑」之一，源码注释在 `runs.py:401-408`。

**可见性事件的三条约束**

| 约束 | 原因 | 码位 |
|---|---|---|
| `Event(type="run.result_rejected", entity="task", entity_id=task.id, run_id=run.id)` | `events.py:43-48` 的 `MESSAGE_TYPES` 只有 `task/idea/discussion/memory`，不含 `run`；entity 写成 `run` 会广播成 `event_update`，前端不弹红色 toast | `runs.py:465-469`、`events.py:43-48` |
| `HistoryEvent(task_id, type="rejected", payload=json)`，**不是 `TaskEvent`** | `TaskDetail.jsx` 对 TaskEvent 走 `compositeLabel(ev.to_state, ev.to_stage)`，`(null,null)` 会渲染成 `null·null` | `runs.py:470-477` |
| `Run.annotations(level="block")` 与 `level="warn"` 严格区分 | warn 级只常驻提示行、绝不弹 toast、文案写明「不影响放行」；block 级才进完成报告红框卡 | `runs.py:436-446,456-461` |

**两个只读端点**

| 端点 | 用途 |
|---|---|
| `GET /api/v1/board/diff-gate-stats` | 纯统计，不参与任何流程判定（与 `_delivery_quality` 同性质）；字段见 §2.3；M1 / M5 可算，M2 / M3 需人工回看 |
| `GET /api/v1/runs/{run_id}/diff-evidence` | 调用 `check_diff_gate(db, task, run, {})`，**与 `submit_result` 同一套判据**，`would_block = verdict.blocked`；只读，不改 run 任何字段、不落事件（`runs.py:284-313`）。`mode=warn` 时 `would_block` 恒为 `false`（`empty_diff_warn` 不设 `blocked`），这一点供 Agent 提交前自查时需知悉 |

### 8.8 剩余工作

| 项 | 现状 | 处置 |
|---|---|---|
| 灰度观察周期 | `diff-gate-grayscale-log.md` 起止仍为 `____-__-__`，M1~M5 全「待观察」，2026-10-21 记录「尚未产生真实拦截」 | 需作者定周期起止并填入 log；是否已进入观察、当前 `MIO_DIFF_GATE` 实际值【待确认】 |
| 切 `block` 三前置 | ≥3 个真实 run 完成、M3=0、M2≥1，当前均未满足 | 维持 `warn`；三条全勾才切 `block`；误伤则回退 `off`/`warn` 且不加严 |
| 唯一成功判据 M2 | 需人工回看「声明 success=true、本次 run 工作区零改动、本会放过的假完成」 | 未成立前不得对外宣称门控效应；G4 口径冻结 |
| 口径收口 | FR-B02 旧行号 `:288/:289`；`d468e633/api.md` 与实现的 5 处差异（无认证 / 157 路由 / 不限流无 429 / `after` 字段结构 / `≥2` 阈值）；README 正文「40 个 MCP 工具」与概览表 56 冲突 | 由 `scripts/gen_readme_stats.py` + CI 校验收口 README 数字（G3 判据）；文档行号与契约差异在下一版 `d468e633/api.md` 修订，不打回代码 |
| 单测护栏 | `tests/test_run_diff_gate.py` 10 用例（零改动 422 / 未 commit 有改动 200 / 已 commit 200 / exempt 200 / idea_review 200 / 非 git 200+标注 / 被拒后改完再交 200 / 连续拒绝超限判失败 / 迁移幂等 / 拦截事件落库） | AC-02 要求 `pytest tests/test_run_diff_gate.py -q --basetemp=<独立目录>` 全绿；AC-03 查库确认事件落库 |

成本 / 工期假设前提：单人全职 + Agent 辅助、熟悉本仓库、含设计 / 实现 / 测试 / 取证口径下重估；不含外部评审等待、审批墙钟、分发运营；墙钟主要依赖「一个真实使用周期」与「≥3 个真实 run」，由作者实际使用节奏决定，不由开发工时决定。

---

## 9. 错误体契约

当前不是单一 schema。4 类门控 422 的 `detail` 结构各不相同（实测码位）：

| 门控 | HTTP | detail 结构 | 码位 |
|---|---|---|---|
| 文档状态门控 | 422 | `{"message":"...quality gate: api has N blocking issue(s)...","quality":{"score":int,"errors":[...],"warns":[...]}}` | `task_documents.py:633-638` |
| 棘轮基线门控 | 422 | `{"message":"棘轮基线门控：...","ratchet":[{"kind","metric","current","baseline"}],"hint":"..."}` | `task_documents.py:653-657` |
| ReadEvidence 门控 | 422 | `{"message":"Read Evidence 门控：...","required_reads":[...],"missing":[...],"stale":[...],"hint":"..."}` | `runs.py:257-266,333` |
| 窄门禁（首次拦截） | 422 | `{"code":"run.empty_diff","message":"...","hint":"...","files_changed":0,"rejections":int,"max_rejections":2}` | `runs.py:511-519` |
| 窄门禁（连续超限） | 409 | `{"code":"run.empty_diff_repeated","message":"...","hint":"...","files_changed":0,"rejections":int,"max_rejections":2}` | `runs.py:500-509` |
| 阶段生命周期门控 | 422 | `{"message":"...","gate":[{"kind","current","required","reason"}]}` | `task_stages.py:199-205,236-242` |
| 统一完成门禁 | 422 / 409 | `{"code":"gate.*","message":"...","detail":...}`（`GateError.as_detail()`，detail 可选） | `completion_gate.py:46-50`；`runs.py:340-344` |
| 认证失败 | 401 | `{"detail":"unauthorized"}` | `main.py:43` |
| 限流 | 429 | `{"error":"rate_limited","retry_after_seconds":int}` + `Retry-After` 头 | `middleware.py:120-124` |
| 越权 / 冲突 | 403 / 409 | `{"code":"run.not_owner"...}` / `{"code":"review.self_approval"...}` 等 | `runs.py:39-43,71-75`；`reviews.py:91-99` |

共同点：4xx 结构化 detail 都带可执行的 `message` / `hint`；`code` 位于 `detail` 内部（不是响应顶层）；计数类字段是整数，布尔是 JSON 布尔。

**【待确认】是否收敛为单一 schema**：当前文档未声明统一错误 schema。若交付方案要写「统一错误体」，需明确是「4xx 统一带 `message`/`hint`」还是「单一字段契约」。收敛会触及 6 类门控 + MCP 端 200 字符截断约定 + 前端按 `detail.code` 分支渲染逻辑，属结构性改动，本期不动（8 项冻结含多入口合并，收敛同属「停投入」范围）。架构文档 §8 第 3 条同列此项。

---

## 10. 已知技术债与处置

| 编号 | 项 | 实测 / 口径 | 处置 | 依据 |
|---|---|---|---|---|
| F3 | 结构化日志丢字段 | `RequestIDMiddleware` 只带 `request_id/path/status/duration_ms`；`/api/v1/logs` 可过滤 | 登记延后 | 可观测域已冻结；5xx 已有 `logger.exception` + `X-Request-ID` 兜底 |
| F8 | otel 未生效 | `main.py:52-55` 调用 `init_otel` / `instrument_app` / `instrument_sqlalchemy` / `instrument_httpx`，但埋点未生效 | 冻结 | Prometheus/SLO/tracing 属 FZ-3，`/metrics` 文本端点是实际可用通道 |
| F10 | 告警无闭环 | 有 `AlertManager` + 24 条告警规则 + `insights`/`remediation` 评估线程，无告警消费处置流程 | 冻结 | 评估线程属观测脚手架，不新增功能 |
| F12 | 异常吞 | 事实包记 94 处；本次源码复核全仓 `except Exception` 出现 290 处（含大量合规的旁路 fire-and-forget），两者口径不同，标【待确认】 | 登记延后 | `RequestIDMiddleware` 对 5xx 记 `logger.exception` 带 `request_id`；逐处整改属结构性投入 |
| F13 | pre-push fail-open | 钩子校验 spec/api/plan approved + diff 引用 FR-n 真实存在，但 fail-open 且路径安装时固化 | 登记延后 | 不作为服务端强制点；强制点固定在 `submit_result` |
| F15 | Windows Proactor 噪音 | 本次在 `mio_taskhub/` 下 grep `Proactor` 无命中；具体触发位置【待确认】 | 登记延后 | 属运行时事件循环噪音，不在主链路 |
| God File | `mcp_server.py` 905 行 / `background.py` 717 行 / `models.py` 646 行；事实包旧口径 ~643 / ~580 | 登记延后 | 理想方案从 `/openapi.json` 自动生成 56 个 MCP 工具定义与转发，但 8 项冻结含「多入口合并（本轮不做）」，自动生成属新增工程投入，回归面覆盖 56 工具 + 1601 用例，当前不成立 |
| `background.py` 重复定义 | `ThreadRegistry` 类、`_thread_registry` 全局、`register_thread` 函数各定义两次（:541-583 与 :586-636），后者覆盖前者；两份实现差异在 `health_check()` 是否含心跳状态 | 登记延后【待确认】是否有意为之 | 本次源码复核新发现；行为上以后定义为准；删重复属清理，需回归 lifespan 关停路径 |
| 文档 kind 三同步点 | `doc_paths.DOC_KINDS` / `api/task_documents.py` 内 `discover_task_docs()` 的局部 `DOC_PATTERNS`（:82-107）/ `web/src/components/DocPanel.jsx` 的 `KIND_META`，无一致性测试 | 登记延后 | 补一条一致性测试属工程修复，待排期；当前靠新增 kind 时三处人工核对 + 文档专项用例 |
| `api/*.py` 模块数 | 实测 44 个（含 `__init__.py`），非 init 43 个；事实包记 30+ 模块 / 176 端点 | 口径标注 | 数字引用一律注明口径与日期；README 统计拟由 `gen_readme_stats.py` 生成且 CI 校验（G3 判据） |
| `d468e633/api.md` 与实现差异 | 「无认证、401 不使用」「157 路由」「不限流、无 429」「before/after 完整结构」「连续被拒 ≥2 判失败」5 处 | 登记延后 | 该文档为 2026-10-20 v1.1 approved 契约，范围声明「不含全量接口清单」，与现实现冲突处需下一版修订，不打回代码 |
| `MIO_MCP_ALLOW_DESTRUCTIVE` 与「47 处调用」 | `mcp_risk.py:13` 注释记「所有 47 处调用统一收口」，与 56 MCP 工具口径不一致 | 口径标注【待确认】 | 注释为历史数字；门控点本身在 `mcp_server._request` 统一收口，行为不受影响 |
| 数字口径三方不一致 | 事实包 176 REST / 56 MCP / 1601 用例；README 顶部表一致但 §4.1 正文「40 个 MCP 工具」、§11「119 端点 / 1305 用例 / 26 路由模块」；源码实测 44 个 `api/*.py` | 口径标注【待确认】 | 架构 §8 第 1 条；交付引用一律标注口径与日期 |

必修 / 登记延后 / 冻结 三分：本期唯一投入项是窄门禁，除此之外**无必修项**；`set_doc_status` 并发丢更新是已知正确性缺陷，因单用户单进程、写频次低、触发条件未成立，登记为延后必修候选，一旦出现多写者并发命中即上修为必修；F8 / F10 / FZ-3 属冻结；其余登记延后并给触发条件。

---

## 11. 【待确认】项

1. **`api/` 模块数与 commit 数口径**：实测 `api/*.py` 44 个（非 init 43）、`db.commit()` 在 `api/` 下 72 处、全仓 `.commit()` 128 处、`except Exception` 290 处；事实包记 30+ 模块 / 59 处分散 commit / F12 异常吞 94 处。统计范围与时点差异需确认。
2. **守护线程清单**：事实包 §3 的 7 线程与 `start_background_jobs()` 实际注册的 7 个不一致；备份等 4 个在 `main.py:lifespan` 单独启动。以哪套为对外口径需确认。
3. **认证覆盖与 7 个无前缀 router**：`main.py:270-277` 的 `memory` / `mio_runtime` / `material_panel` / `value_discovery` / `scheduler_control` / `observability` / `insights_router` 挂载时未显式加 `prefix="/api/v1"`，需核各模块自身 `APIRouter` 定义，确认 176 端点是否全部落在「仅 `/api/*` 需认证」范围。这是安全相关项。
4. **`d468e633/api.md` 与实现的 5 处差异**：无认证 / 157 路由 / 不限流无 429 / `before`+`after` 字段结构 / `≥2` 判失败阈值。以源码为准，文档修订排期需确认。
5. **FR-B02 插入点行号**：需求写「约 `:288` 后、`:289` 前」，实测 `runs.py:336-337`（文件现 636 行）。
6. **`collect()` 函数名**：需求文档与 `narrow-gate-implementation-plan` 使用 `collect()`，源码 `diff_gate.py` 只有 `snapshot()` / `_numstat()` / `check_diff_gate()`。
7. **窄门禁拒绝阈值**：源码 `rejections > MAX_REJECTIONS`（`>2`，第 3 次才 409），需求 FR-B06 与 api.md 写 `≥2`。以源码为准。
8. **`scheduledjob` 3 个 `enabled=0` 的历史状态**：`seed.py` 只播种 1 个 `idea-generate`（默认 `enabled=False`），另外 2 条的来源与 `ERROR(502)` 记录未在本次源码复核中定位。
9. **`apscheduler==3.11.3`** 在钉死依赖内，但调度主链路是 `scheduling/cron_engine.py` + `croniter==6.2.4` 自管线程，是否为残留依赖、有无调用点需确认。
10. **`OutboxEvent` 自动清理状态**：v3 审计列 P2 未修复，README §5.7 称「带自动清理」；`main.py:lifespan` 实测 `DepMetricsPersist.cleanup()` 只针对 dep 快照。
11. **`night_runner shell=True`**：v3 审计（2026-09-10）标记中风险，2026-11-06 取证当时仍在 `night_runner.py:152`。**2026-11-07 19:07 已随 `2e42305` 消除**：`split_command()` + `Popen(argv, shell=False)` + `MIO_NIGHTRUN_ALLOWED_CMDS` 白名单；19:15 `grep -rn "shell=True" mio_taskhub/` 仅 2 处注释。主方案 §0.3 第 18 条 / §7.1 #12 已收口，冻结 commit 复验 AC-23 后关闭。
12. **`dashboard` fallback HTML 内嵌外部 CDN**：`main.py:513` 实测引用 `cdn.jsdelivr.net/npm/chart.js@4.4.7/...`，与「零联网」主张存在张力；正式产物走 `web/dist/dashboard.html` 时是否也依赖外部 CDN 未核。
13. **F15 Windows Proactor 噪音**：本次在 `mio_taskhub/` 下 grep `Proactor` 无命中，具体触发位置与复现条件需确认。
14. **F3 / F12 的量化口径**：结构化日志丢字段的具体字段清单、异常吞 94 处的统计脚本与排除规则未取得。
15. **「统一错误体」是否收敛为单一 schema**：见 §9；需明确是「4xx 统一带 `message`/`hint`」还是「单一字段契约」。
16. **灰度观察周期起止与当前档位**：`diff-gate-grayscale-log.md` 起止为 `____-__-__`，M1~M5 全待观察，是否已进入观察、当前 `MIO_DIFF_GATE` 实际值需确认。
17. **「代码类任务」完整 `task_kind` 清单**：`models.py:60-63` 实测 `TaskKind` 只有 `NORMAL` / `CHANGE_TRACKING` / `REVIEW(idea_review)` 三值，`EXEMPT_TASK_KINDS=("idea_review","change_tracking")` 豁免后两个，剩 `normal` 参与判定。若未来扩 kind，需同步 `EXEMPT_TASK_KINDS`。
18. **F9 `night_runner` 的处置是否含「移除失败重试」**：事实包 N5 记「保持关闭、仅保留手动试跑」，当前默认 `enabled=False` 已满足，是否还需清理历史 `spawn … 脚本不存在` 50 条记录需确认。
19. **`MIO_TASKHUB_RATE_LIMIT` 生产取值**：默认 120 req/min，多 Agent 场景下作者实际配置值需确认；F11 触发上修的量化判据未定。
20. **G3 / G4 具体日历与成本**：本章不排日历、不给确定数字；假设前提与架构 §6.2 / 需求 §7.2 一致。具体日历由作者定。

（本段交付完毕。未对其他成员直接通信，全部产出回传主理人。）
