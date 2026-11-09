# 前端信息架构与组件体系（Phase 2 / 任务 #5）

> 编制：颜可心（前端体验工程师）｜取证日 2026-11-06
> 对象：`E:\work\code\agent-dev\mio-taskhub\web\src\` 现有 React 18 + Vite 5 SPA，不是新建前端。
> 事实底稿：`docs/taskhub/_delivery-fact-pack.md`；上游章节 `phase1-requirements.md` / `phase1-architecture.md`（三份已完整读完）。
> 本次源码复核：`App.jsx` / `api.js` / `stageDocs.js` / `docLifecycle.js` / `cpm.js` / `constants.js` / `confirm.js` / `promptInput.js` / `noise.js` / `main.jsx` / `lib/overlayStack.js` / `lib/observatoryAdjacency.js` / `index.css` / `ledger.css` / `index.html` / `package.json` / `vite.config.js` / `components/` 下的 `DocPanel` / `StageAdvanceDialog` / `GateStatusCard` / `ErrorBar` / `ConnectionBanner` / `TaskCard` / `TaskDetail` / `ReviewPanel` / `BoardView` / `TopoView` / `GanttView` / `EmbedView` / `Rail` / `CommandPalette` / `MemoryView` / `Skeleton` / `ErrorBoundary`，以及 `mio_taskhub/main.py` 的 CDN 引用与 `docs/taskhub/grab-mode/` 文档链。
> 口径：现有前端的事实描述 + 窄门禁（FR-B05）所需的最小前端改动 + 剩余工作。不新增视图、不重做组件体系。冻结域按「端点保留、不新增投入、不补 UI」记。
> 与上游不一致处逐条列在 §9【待确认】，不编造。

---

## 1. 信息架构与视图清单

### 1.1 路由与入口机制

`web/src/` 无 `react-router`，视图切换是 `App.jsx` 内的 `view` state，不是 URL 路由：

| 入口方式 | 实现 | 备注 |
|---|---|---|
| `?view=<id>` query | `App.jsx:82` 优先读 `URLSearchParams` | 优先级最高 |
| `localStorage['mio.view']` | `App.jsx:84`，默认 `workflow` | 旧值 `board` / `stage` / `flow` 统一归一为 `workflow`（`:86`） |
| 数字键 1–9 | `App.jsx:463` `VIEW_IDS` 共 10 项，1–9 覆盖前 9 项 | `['workflow','list','plan','topo','gantt','ideas','templates','scheduled','stats','memory']` |
| `Ctrl/Cmd+K`、`/` | 命令面板 `CommandPalette`，VIEWS 13 项 | 覆盖数字键到不了的 `observability` / `mio` / `material` |
| 主导航 | `Rail.jsx` VIEWS 13 项 + 齿轮 `settings` | `HIDDEN_VIEWS = {ideas, scheduled, observability, memory}` 从主导航下线（`:94`），仍可命令面板进入 |
| `?theme=light\|dark` | `App.jsx:94` | 另有 `localStorage['mio.theme']` |
| `/#/embed` | `App.jsx:67` `window.location.hash === '#/embed'` 时**直接 return `<EmbedView />`** | 不渲染 shell / Rail / MissionBar / 横幅 / toast 容器 |

结论：对外可称之为「10+ 视图」，实现上是 13 个 `view` 分支 + `settings` + `embed` + 4 类浮层（任务详情抽屉、文档面板、命令面板、创建/确认/输入对话框）。冻结域入口在 Rail 里只下线了 4 个中的 2 个（`observability` / `memory`），`mio` / `material` 仍在主导航，见 §9。

### 1.2 逐视图清单

「数据来源端点」列：多数视图不自行取数，由 `App.jsx:169` 的 `GET /api/v1/tasks?cancelled=true` 全量拉取后以 `filteredTasks` props 下传，5 秒轮询兜底（`:288`）。这类在表中标「props（全量 tasks）」。

| 视图 | 路由 / 入口 | 主要职责 | 关键组件 | 数据来源端点 |
|---|---|---|---|---|
| 工作流泳道 | `workflow`（默认，键 1） | 按状态或阶段分组看板；拖拽改阶段、阶段推进、取消 | `WorkflowView` / `BoardView` / `TaskCard` / `StageAdvanceDialog` | props（全量 tasks）；`POST /tasks/{id}/stage`、`POST /tasks/{id}/stage/move`、`GET /tasks/{id}/documents` |
| 列表 | `list`（键 2） | 表格行、行内取消、加载骨架 | `ListView` / `Skeleton` | props（全量 tasks） |
| 空闲计划 | `plan`（键 3） | 夜间计划、cron 配置、手动 spawn / stop | `PlanView` | `GET /plans/night`、`/plans/night/saved`、`/plans/projects`、`/nightrun/full-config`、`/nightrun/cron-tasks`；`PUT /nightrun/config`；`POST /nightrun/spawn-now`、`/nightrun/stop`。对应 N5，night_runner 调度默认关 |
| 拓扑 DAG | `topo`（键 4） | 依赖分层、连通分量、CPM 关键路径 | `TopoView` / `cpm.js` / `DependencyGraph` | props（全量 tasks，本地按 `depends_on` 构图）；**不调** `GET /tasks/graph` |
| 甘特 CPM | `gantt`（键 5） | 时间轴条、并行度、关键路径，按任务/Agent/项目分组 | `GanttView` / `cpm.js` | props（全量 tasks） |
| 想法 | `ideas`（键 6，Rail 已下线） | 想法库、驾驶舱、ADR、讨论、拆解、发酵 | `IdeasView`（`React.lazy`）/ `IdeaSearch` / `GateStatusCard` / `InboxView` / `TemplatePicker` | `/ideas*`、`/ideas/{id}/cockpit`、`/ideas/{id}/history`、`/ideas/{id}/draft-fields`、`/discussions*`、`/mio/ferment*`、`/ideas/gate/status`、`/ideas/gate/force-generate`、`/ideas/inbox`、`/ideas/batch-triage` |
| 模板 | `templates`（键 7） | 模板 CRUD、版本恢复、从任务另存、从模板建任务 | `TemplatesView` / `CreateModal` | `/tasks/templates*`、`/tasks/templates/{id}/versions`、`/tasks/templates/{id}/restore/{v}`、`POST /tasks/templates/from-task/{id}`、`POST /tasks/from-template/{id}` |
| 定时 | `scheduled`（键 8，Rail 已下线） | 定时任务 CRUD、触发/暂停/恢复、执行历史、cron 校验 | `ScheduledJobsView` | `/scheduled-jobs*`、`/scheduled-jobs/validate-cron` |
| 统计 | `stats`（键 9） | 概览、Prometheus 指标、趋势、报告 | `StatsView` / `Skeleton` | `/board/overview`、`/metrics`、`/observability/report`、`/tasks?cancelled=true` |
| 观测台 | `observability`（Ctrl+K，Rail 已下线） | 摘要、告警、追踪、SLO 历史、洞察、进程信息 | `ObservabilityView` | `/observability/summary`、`/alerts`、`/traces`、`/slo/history`、`/insights`、`/mio/insight`、`/process` |
| 记忆 | `memory`（键 10 / Ctrl+K，Rail 已下线） | **单入口双 tab**：「记忆观测」/「记忆网关」 | `MemoryView` / `MemoryObservatoryView` / `lib/observatoryAdjacency.js` / `Skeleton` | `/memory/health`、`/metrics`、`/events?limit=200`（客户端按 `memory_` 前缀过滤）、`/mio/memory`；WS `memory_update` 由 App `setMemoryEvent` 传入 |
| Mio 运行时 | `mio`（Ctrl+K，Rail 未隐藏） | 运行时只读状态/轨迹/创意、经验确认、手动拉起 observer | `MioRuntimeView` | `/mio/status`、`/mio/traces`、`/mio/memory`、`/mio/creativity`、`/mio/experience/pending`、`POST /mio/experience/{id}/confirm`、`POST /mio/observer/start` |
| 素材面板 | `material`（Ctrl+K，Rail 未隐藏） | 价值发现、素材列表、选题 token 前端过滤 | `MaterialPanelView` | `GET /mio/material-panel`、`GET/POST /mio/value-discovery` |
| 设置 | `settings`（Rail 齿轮） | 更新检查/下载/应用/忽略、进程信息、Markdown 说明渲染 | `SettingsView` / `UpdateBanner` | `/update/status`、`POST /update/check\|download\|apply\|dismiss`、`/process` |
| 嵌入视图 | `/#/embed`（hash） | 无 shell 的流程板 + 精简任务详情，独立 WS + 5 秒轮询 | `EmbedView` / `FlowView` | `GET /tasks`、`GET /tasks/{id}`、`POST /tasks/{id}/stage`；`ws://${location.host}/ws`（固定 3 秒重连，无退避） |
| 任务详情抽屉 | 浮层（`openTask`） | 六 tab：就绪度 / 工作流 / 依赖 / 清单 / 讨论 / 动态；完成报告 | `TaskDetail` / `DependencyGraph` / `WorkstreamPanel` | `GET /tasks/{id}`、`/tasks/{id}/events`、`/tasks/{id}/readiness`、`/tasks/{id}/workstream`、`PATCH /tasks/{id}/subtasks/{sid}`、`POST /tasks/{id}/discussions`、`PATCH /tasks/{id}`、`POST /tasks/templates/from-task/{id}` |
| 文档面板 | 浮层（`React.lazy`，`onOpenDocs`） | 文档浏览/搜索/全文查找/目录/生命周期赛道/状态推进/打印 | `DocPanel` / `docLifecycle.js` | `GET /tasks/{id}/documents`、`GET /tasks/{id}/doc?kind=`、`GET /tasks/{id}/file?path=`、`GET /tasks/{id}/raw?path=`、`GET /tasks/{id}/doc/statuses`、`POST /tasks/{id}/doc/{kind}/status` |
| 评审面板 / 队列 | 组件文件存在，**当前无 import 方** | 审阅清单/提交/历史；评审队列 | `ReviewPanel` / `ReviewQueue` | `/tasks/{id}/reviews`、`/tasks/reviews/queue` |
| 命令面板 | 浮层（Ctrl+K / `/`） | 视图导航 + 任务模糊搜索 | `CommandPalette` / `overlayStack` | 无 API，使用 App 传入的 `tasks` |
| 决策面板 | 常驻条（非路由） | 待决策 / 待验收 / 失败待处置入口 | `DecisionInbox` | `/decisions?status=open`、`POST /decisions/{id}/resolve`、`/decisions/{id}/supersede`、`/board/summary` |
| 创建任务 / 确认 / 输入 | 浮层 | 建任务、危险操作二次确认、通用输入 | `CreateModal` / `ConfirmDialog` / `InputDialog` | `/tasks`、`/tasks/templates`、`/scheduled-jobs/validate-cron`、`POST /scheduled-jobs` |

### 1.3 冻结域标记

| 视图 | 冻结依据 | 本期处理 |
|---|---|---|
| 观测台 `observability` | FZ-3 Prometheus / SLO / tracing；SLO 目标值为目标非实测，该域已冻结 | 端点保留、入口保留（命令面板可达）、不新增投入、不补 UI |
| 记忆 `memory`（观测 + 网关双 tab） | FZ-4 memory gateway | 同上；`lib/observatoryAdjacency.js` 保持现状 |
| Mio 运行时 `mio` | FZ-1 / FZ-2（`MIO_OBSERVER_AUTOSTART` 已改默认关） | 同上；`POST /mio/observer/start` 按钮保留、不新增调度 |
| 素材面板 `material` | FZ-2 observer 机会发现 | 同上 |
| 想法 `ideas` | FZ-6 idea 子系统扩张（`MIO_IDEA_REVIEW_ENABLED` 默认关） | 视图存在、主导航已下线；不新增调度与 UI |

冻结定义按需求 §5.2：代码不删、接口不删、文档保留；停投入、停调度、停当价值宣称、移出主链路。

---

## 2. 组件体系分层

### 2.1 四层与目录

| 层 | 文件 | 职责 |
|---|---|---|
| 容器层 | `App.jsx`、`main.jsx` | `App` 单例持有全部 UI state 与副作用；`main.jsx` 用 `ReactDOM.createRoot` + `React.StrictMode`，引入 `index.css` / `ledger.css` |
| 视图层 | `WorkflowView` / `ListView` / `PlanView` / `TopoView` / `GanttView` / `IdeasView` / `TemplatesView` / `ScheduledJobsView` / `StatsView` / `ObservabilityView` / `MemoryView` / `MemoryObservatoryView` / `MioRuntimeView` / `MaterialPanelView` / `SettingsView` / `EmbedView` / `BoardView` / `FlowView` | 由 `view` state 条件渲染；主要吃 props，少数自行拉端点 |
| 通用组件层 | `TaskCard` / `TaskDetail` / `DocPanel` / `StageAdvanceDialog` / `GateStatusCard` / `ReviewPanel` / `ReviewQueue` / `DependencyGraph` / `WorkstreamPanel` / `CreateModal` / `CommandPalette` / `ConfirmDialog` / `InputDialog` / `ErrorBoundary` / `ErrorBar` / `ConnectionBanner` / `UpdateBanner` / `MissionBar` / `Rail` / `DecisionInbox` / `SideStatus` / `Skeleton` / `InboxView` / `IdeaSearch` / `TemplatePicker` | 卡片、抽屉、面板、浮层、横幅、命令入口 |
| 工具层 | `api.js` / `stageDocs.js` / `docLifecycle.js` / `cpm.js` / `confirm.js` / `promptInput.js` / `noise.js` / `constants.js` / `lib/overlayStack.js` / `lib/observatoryAdjacency.js` | 无 DOM 或框架无关纯逻辑、接口封装、静态映射 |

### 2.2 工具层组件清单与复用关系

| 组件 | 层级 | 职责 | 主要导出 / Props | 复用场景 |
|---|---|---|---|---|
| `api.js` | 工具 | `BASE='/api/v1'` 单一 `req(method,path,body)` 封装、`ApiError`、`gated()` | `api` 对象约 90 个具名方法；`ApiError{status,detail,url,userMessage,hint,error_code}` | 所有视图与面板 |
| `stageDocs.js` | 工具 | `STAGE_ARTIFACT` 静态副本（对齐后端 `task_stages.py::STAGE_ARTIFACT_REQUIREMENTS`）、审查结论模板、产出物请求体构造 | `artifactOf(stage)` / `artifactBody(stage,value,mode)` / `currentArtifact(task,stage)` / `REVIEW_PRESETS` | `App.advanceTaskStage`、`StageAdvanceDialog` |
| `docLifecycle.js` | 工具 | 状态 → 中文名 + 语义色调；生命周期赛道 | `DOC_STATE_LABELS` / `docStateLabel` / `docStateTone` / `stateOfDoc` / `stateOfStatus` / `lifecycleTrack(states,current)` | `DocPanel` |
| `cpm.js` | 工具 | 关键路径法纯函数，无 React/DOM 依赖 | `computeCPM(tasks,opts)` / `taskDuration` / `DEFAULT_DURATION=60` / `EPSILON=0.5` | `TopoView`（`:92`、`:144`）、`GanttView`（`:32`） |
| `confirm.js` | 工具 | Promise 式确认，框架无关单例；渲染方 `ConfirmDialog`（App 挂载一次） | `confirm(msg,{title,danger,okText,cancelText})` / `subscribeConfirm` | `App.cancelTask`、`api.js::gated`、`ReviewPanel`、`CreateModal` |
| `promptInput.js` | 工具 | Promise 式输入，替代 `window.prompt`；渲染方 `InputDialog` | `promptInput(msg,{title,defaultValue,multiline,required,...})` / `subscribePrompt` | 与 `confirm.js` 同构 |
| `noise.js` | 工具 | 机器噪声任务过滤（E7 结论） | `isNoiseTask(t)` / `countNoiseTasks(tasks)` | `App` 的 `tasks` 派生、`showNoise` 开关 |
| `constants.js` | 工具 | 状态/阶段唯一语义映射、复合标签、时间格式化、Agent 配色、冻结噪声 | `LANES` / `STAGES` / `STATE_META` / `STAGE_META` / `compositeLabel` / `parseUtc` / `fmtDate` / `isFrozenReviewNoise` | 几乎全部视图 |
| `lib/overlayStack.js` | 工具 | 全仓唯一 Esc 归属仲裁 | `useOverlayStack(onClose,active)` / `overlayDepth()` | `TaskDetail` / `DocPanel` / `StageAdvanceDialog` / `CommandPalette` / `ConfirmDialog` / `InputDialog` |
| `lib/observatoryAdjacency.js` | 工具 | 记忆观测关系 O(E) 邻接表 | `buildAdjacency(relations)` / `nodeEdges(adj,id)` | `MemoryObservatoryView`（冻结域） |

### 2.3 复用关系要点

- `App` 是唯一容器，无全局状态库。所有跨视图数据经 props 下传；`view` / `contrast` / `theme` / `showNoise` 写 `localStorage`。
- `WorkflowView` → `BoardView` → `TaskCard` 是主看板链；`EmbedView` → `FlowView` 是精简链；`TaskDetail` → `DependencyGraph` / `WorkstreamPanel` 是详情链。
- `IdeasView`（lazy）→ `GateStatusCard` / `InboxView`；`MemoryView` → `MemoryObservatoryView` / `Skeleton`；`DocPanel`（lazy）→ `docLifecycle.js`。
- `React.lazy` 仅两处：`IdeasView`、`DocPanel`。对应 `vite.config.js` 的 `manualChunks`：`hljs` / `markdown`（marked + dompurify）/ `vendor`。`DocPanel` 只注册 10 种 highlight.js 语言，避免全量 190+ 语言进包。
- `overlayStack` 栈序（源码注释）：`ConfirmDialog > CreateModal > DocPanel > TaskDetail > CommandPalette`，只有栈顶响应 Esc。
- `GateStatusCard` 内部**自建了一个本地 `confirm()`**（`innerHTML` 拼串，`:137-159`），未走 `confirm.js` / `overlayStack`，与全仓唯一确认点约定不一致（见 §9）。
- **未接线组件**：`ReviewPanel`、`ReviewQueue`、`IdeaSearch`、`TemplatePicker` 在 `web/src` 内无 import 方（只有自身 export 定义）。`IdeaSearch.jsx:60/65/88` 与 `TemplatePicker.jsx:22/49` 调用 `api.post(...)` / `api.get(...)`，而 `api.js` 导出的对象没有 `get` / `post` 方法，一旦挂载会 `TypeError`（见 §9）。`ReviewPanel` / `ReviewQueue` 未接线与需求 §6 N3「taskreview 降级保留，不接线」一致。

---

## 3. 状态管理与接口对接

### 3.1 状态管理

`App.jsx` 顶层 `useState` 集中持有：`allTasks`（全量，含已取消与噪声）、`ideas`、`view`、`contrast`、`theme`、`alerts`、`ws`、`error`、`modal`、`detail`、`docTask`、`loading`、`refreshing`、`lastSync`、`focus`、`filter`、`toasts`、`memoryEvent`、`wsRetryIn`、`cmdOpen`、`advanceDlg`。派生值：`tasks`（`:116`，按 `showCancelled` / `showNoise` 过滤）、`cancelledCount`、`noiseCount`、`filteredTasks`、`filteredIdeas`、`activeAlerts`。`tasksRef`（`:373`）专供长生命周期 WS 闭包取最新任务标题（`titleOf`）。

刷新通道三条并存：首屏 `loadTasks()` + `loadIdeas()`；`setInterval(..., 5000)` 全量轮询兜底；WS 事件驱动局部动作。`alerts` 另走 30 秒轮询（`:161`）。

### 3.2 `api.js` 封装与错误建模

| 项 | 实现 |
|---|---|
| 基址 | `const BASE = '/api/v1'` |
| 请求 | `req(method,path,body)`：`fetch` + `Content-Type: application/json`；`204` → `null`；`r.json()` 失败兜 `{}` |
| 错误 | 非 2xx 抛 `ApiError(msg, status, detail, url)`；`msg = typeof detail === 'string' ? detail : (detail?.detail \|\| detail?.error \|\| (detail ? JSON.stringify(detail) : 'HTTP '+status))` |
| 通用文案 | `ERROR_MESSAGES`：`422 → '数据校验失败，请检查必填项'`、`409 → '操作冲突，请刷新后重试'`、401/403/404/429/5xx 同类静态映射；挂到 `ApiError.userMessage` |
| detail 提取 | `hint` 取 `detail.hint`；`error_code` **仅取 `detail.error`** |
| 危险操作 | `gated(method,path,label)`：捕获 `409` 且 `error_code === 'policy_risk_high'` → `confirm()` → 通过后带 `?confirm=true` 重放一次；用于 `cancelTask` / `deleteTemplate` / `deleteScheduledJob` / `updateApply` |
| 认证 | **取证当时**：`req` 不注入 `Authorization` 头（只设 `Content-Type`）；认证中间件仅对 `/api/` 校验 Bearer，Web UI 与 `/docs` 免认证；`--auth` 时 token 来源在前端未见（见 §9）。**现状（2026-11-07 19:50）**：S-4 四条通道已落地，`web/src/auth.js` 统一注入 Bearer / `?token=` / blob 通道，无 token 时退化为原生行为（详见主方案 §4.4.1「第四次取证」）。**2026-11-08 裁决**：当前定位不做默认 auth，浏览器侧取 token 策略降为按部署形态条件化，不阻塞 G4 |
| 端点覆盖 | 约 90 个具名方法。**未封装** `PUT /doc`、`GET /doc/{kind}/revision`、`GET /doc/quality`、`GET /tasks/{id}/ratchet`、`GET /runs/{run_id}/diff-evidence`、`GET /board/diff-gate-stats`、`POST /runs/{run_id}/result` 的 UI 调用（后者仅定义 `api.result`，无调用点） |

### 3.3 WebSocket `/ws` 订阅、重连与连接态

| 项 | 实现 |
|---|---|
| 建连 | `App.jsx:247` `new WebSocket(\`ws://${location.host}/ws\`)`；**未带 `?token=`、未带 Bearer 头** |
| 订阅类型 | `task_update`（含 `event`）、`idea_update`、`memory_update`、`update_status` |
| 重连 | `onclose` → `schedule()`，指数退避 `delay = Math.min(1000 * 2 ** retry, 15000)`；`onopen` 重置 `retry=0`；`onerror` 主动 `close()` |
| 倒计时 | `nextRetryAt` + `tickId = setInterval(tick, 250)` → `setWsRetryIn(remain)` 秒数 |
| 卸载 | `closed=true`，清 `timer` / `interval` / `tickId`，`socket.close()` |
| 连接态 UI | `ConnectionBanner`（`role="status" aria-live="polite"`，`wsLive=false` 显示「连接断开 / Ns 后重试 / 最后同步」）+ `Rail` 底部 `rail__ws` 点 |
| 协议口径 | 架构 §4.2：`/ws` 在 `websocket_endpoint` 内单独校验 `?token=` 或 Bearer，失败 `close(code=4401)`。前端未注入 token 的差异见 §9 |
| EmbedView | 独立 WS（`EmbedView.jsx:26`），`onmessage` 只调 `load()`，固定 3 秒重连，无退避、无 `ConnectionBanner`，**不处理 `run.result_rejected`（见 §5、§9）** |

### 3.4 错误呈现

`ErrorBar.jsx`：`role="alert"`，支持 `error?.userMessage` / `hint` / `error_code` / `status` + 重试 + 关闭。**但 `App.jsx:596-603` 调用时只传 `message`（`'创建失败: ' + e.message` 这类拼接字符串）/ `errorType` / `onRetry` / `onDismiss`，没有传 `error` 对象**，因此 `userMessage` / `hint` / `error_code` 在 App 层实际不生效。浮层内的错误改用就近错误条：`StageAdvanceDialog.shownError`（`role=alert`）、`DocPanel.advErr`、`ReviewPanel.submitErr`、`GateStatusCard.forceErr`。

### 3.5 `TaskCard` 拖拽、乐观更新与服务端 422 门控

| 动作 | 前端实现 | 服务端约束 |
|---|---|---|
| 按状态拖拽 | `BoardView.jsx:46` `canDrag = groupBy === 'stage'`；按状态泳道禁止拖拽并给说明（`:128`）。`TaskCard` `draggable={canDrag}`，`onDragStart` / `onDragEnd` 回调 | `state` 不在 `PATCH /tasks/{id}` 可编辑白名单内，唯一写入口是 `apply_transition`（claim / runs / 阶段推进 / 调度） |
| 手动改 state | `App.moveTask()`（`:430`）**只弹 warn toast 解释原因、不写数据**；旧实现「只 setAllTasks 不调 API」的假成功已移除 | 无「人手动置 state」接口 |
| 键盘可达 | `TaskCard` `tabIndex=0` + `role=button`；Enter/Space 打开详情；`Alt+←/→` 触发 `onMove`；aria-label 含状态与操作说明 | — |
| 改阶段（乐观） | `App.moveTaskStage()`（`:437`）：先 `setAllTasks` 改 `stage` + `setDetail`，再 `POST /tasks/{id}/stage/move`；失败 `setError('移动阶段失败: ...')` 且 `loadTasks()` 全量回滚 | `422` 非法转移 / 阶段门控 / 依赖成环 |
| 阶段推进 | `App.advanceTaskStage()`（`:400`）是唯一入口，产出物统一由 `StageAdvanceDialog` 收集；`advanceStage()` 返回 `{ok,message}` 让浮层内显示错误 | 阶段门控 `422 {message, quality}`；棘轮 `422 {message, ratchet, hint}` |
| 文档状态推进 | `DocPanel.advanceDoc()`（`:369`）**不做乐观更新**：`POST /doc/{kind}/status` 成功后 `Promise.all` 重拉 `getDocStatuses` + `getTaskDocuments`；失败取 `d.message \|\| e.message` 在面板内显示 | 质量门 / 棘轮 / 非法转移三类 422 |

冲突处理口径：前端不做事字段级冲突合并，一律「失败 → 全量 `loadTasks()` 以服务端为准」或「浮层内显示 `message`」。乐观更新只用于 `stage`，且失败即回滚。文档状态推进与阶段推进不做乐观写入。

---

## 4. 文档面板与阶段门控交互

### 4.1 逐项落点（现状）

| 需求项 | 前端落点 | 现状 |
|---|---|---|
| 文档 kind 三同步点之三 | `DocPanel.jsx:36-63` `KIND_META`：22 类文档 kind（spec/plan/review/requirement/test/architecture/api/readme/changelog/decision/risk/setup/runbook/glossary/userguide/research/retro/milestone/data-model/security/troubleshooting/incident）+ 2 类挂载项（deliverable / file），共 24 键；`kindMeta(k)` 兜底未知 kind | 与 `doc_paths.DOC_KINDS`、`api/task_documents.DOC_PATTERNS` 并列第三处手工同步点 |
| `PUT /doc` 响应 `quality` | 无落点 | `api.js` 只有 `getTaskDoc`（GET），没有写文档方法；`DocPanel` 是只读浏览 + 状态推进。架构 §2.3 记 `PUT /doc` 响应挂 `quality`（`errors/warns/score`）不阻断，但 Web UI 未消费，只在 `advanceDoc` 失败时显示 `d.message`，不展示 `quality` 明细 |
| `StageAdvanceDialog` 收审查结论 | `stageDocs.js::STAGE_ARTIFACT`：`brainstorming→requirement`、`design→spec` 且 `requiresDiscussion`、`planning→plan`、`implementing→changelog`、`done→review` 且 `acceptsText=true` / `textField='review_result'`；`REVIEW_PRESETS` 四条结论模板；`artifactBody(stage,value,mode)` 支持 `'text'` / `'path'` 两模式；`done` 纯文本结论 ≥8 字校验；路径模式把 `GET /tasks/{id}/documents` 做成可点 chip；错误显示在浮层内部 | 已实现，替代 `window.prompt` |
| `GateStatusCard` 展示门控状态 | **名不副实**：调 `api.gateStatus()` = `GET /ideas/gate/status`，展示「生产闸门」加权总分 / INBOX / NEW / 评审带宽 / 软硬阈值 / 吞吐，30 秒轮询，含「强制生成」（`POST /ideas/gate/force-generate`）+ 本地 confirm 弹窗。被 `IdeasView.jsx:934` 引用 | 展示的是想法评审闸门（冻结域 FZ-6 相关），**不是**文档/阶段门控状态。文档阶段门控的可视化在 `DocPanel`（`docpanel__docstate` 徽标、`lifecycleTrack` 赛道、`allowed_next` 推进按钮）与 `TaskDetail` 就绪度 tab |
| `GET /doc/{kind}/revision` 修订指令 | 无落点 | grep `revision` 在 `web/src` 无命中；`api.js` 未封装。US-8 / AC-09 的取证靠 curl 或 MCP，不靠 UI |
| `GET /tasks/{id}/ratchet` 棘轮基线 | 无落点 | grep `ratchet` 无命中 |
| `FR-n ↔ TC-n` 追溯矩阵 | 无落点 | grep `traceability` / `doc/quality` 无命中；`GET /tasks/{id}/doc/quality` 返回的追溯矩阵无前端呈现 |

一次 grep 复核：`revision|ratchet|doc/quality|diff-evidence|diff-gate-stats|traceability|api.result|empty_diff` 在 `web/src` 返回 `No matches found`。即上述端点前端全部无引用，且 Web UI 没有 `submit_result` 调用点。

### 4.2 三同步点缺一致性测试的风险与最小补法

风险（事实包 §3 已知限制、架构 §7 风险表）：`doc_paths.DOC_KINDS` / `api/task_documents.DOC_PATTERNS` / `KIND_META` 三处各存一份 kind 清单，新增 kind 靠人工核对，缺自动一致性测试。本次复核发现**同类静态副本比登记的三处更宽**：

| 前端静态副本 | 对应的服务端/其他事实源 | 源码自述 |
|---|---|---|
| `stageDocs.js::STAGE_ARTIFACT` | `api/task_stages.py::STAGE_ARTIFACT_REQUIREMENTS` | 文件头注释「若后端表有变更，请同步本文件」 |
| `docLifecycle.js::DOC_STATE_LABELS` / `DOC_STATE_TONES` | `doc_lifecycle.py::DOC_LIFECYCLE` | 「词表变更时请同步本文件」 |
| `constants.js::LANES` / `STAGES` | `models.py::TaskState` / 7 段阶段 | 文件头「唯一语义映射」 |
| `noise.js::isNoiseTask` | `scripts/e7_decision_scan.py` | 「两边各存一份…改规则时两处同改」 |
| `DocPanel.jsx::KIND_META` | `doc_paths.DOC_KINDS` / `DOC_PATTERNS` | 登记的三同步点之一 |

最小补法（不扩 UI、不新增视图）：

1. 引入前端测试运行器（§8 ①），先加一条**枚举一致性测试**：把 `KIND_META` 键集（排除 `deliverable` / `file`）与构建期导出的 `DOC_KINDS` / `DOC_PATTERNS` fixture 比对，键集相等即通过；新增 kind 漏改任一处即红灯。
2. 同一测试文件顺带断言 `STAGE_ARTIFACT` 的 `kind` 值全部落在 `KIND_META` 键集内、`docLifecycle.js` 的状态词表覆盖 `doc/statuses` 返回的 `states` 序列——这两条是低成本扩展，不改运行时代码。
3. 更彻底的替代方案是构建时由后端导出 JSON 生成 `KIND_META`，消除第三处手工同步点；属结构性改动，按冻结口径本期不做，仅登记。

验收命令依赖测试框架落地，见 §8 ①。

---

## 5. 窄门禁所需前端改动（FR-B05，本期唯一投入项的前端部分）

### 5.1 结论先行：三链路已在当前工作树落地

源码注释明确标注 `d468e633 FR-5/6`。本期前端**不是从零实现三链路**，而是补「独立错误码呈现 + warn 文案兜底 + 红行样式 + 三链路自动化断言」，并与后端 FR-B01~B06 联调确认字段名。

| 链路 | 事件 / 字段 | 组件落点 | 现状 |
|---|---|---|---|
| ① 红色 toast（M4，4 秒内可见） | `Event(type=run.result_rejected, entity=task)` → WS `task_update` | `App.jsx:269-271` 分支 `addToast(\`「${titleOf(ev.entity_id)}」 提交被拦截：${p.message \|\| '零改动'}\`, 'danger', { sticky:true, taskId })`；容器 `App.jsx:607` `aria-live="polite"`，sticky 用 `role="alert"`，`.toast--danger`（`index.css:3866` 红左边框）、`.toast--sticky`（`:3868` 不自动消失，普通 toast 4 秒 `setTimeout` 移除） | **已实现**，且强于 4 秒要求（常驻）；`addToast` 对 sticky 免驱逐、同 `taskId` 去重（`:195-198`） |
| ②「动态」tab 红行 | `HistoryEvent(type=rejected)` | `TaskDetail.jsx:60` `HIST_TYPE_META.rejected = { label:'已拦截', tone:'danger' }` → `HistRow`（`:149`）渲染 `chip--danger` 标签 → `tab === 'activity'`（`:936`）的 `task.history` 时间线 | **部分实现**：标签为 danger 红色，但 `HistRow` 根节点固定 `className="hist-row"`，**没有按 `meta.tone` 给整行加红底/红边**。「红行」需补 |
| ③ 完成报告红框卡 | `Run.annotations(level=block)` | `TaskDetail.jsx:497-518`：`anns = r.annotations \|\| []`，`blocked = anns.filter(a => a.level === 'block')` → `.run-report__blocked`（`index.css:3687` 淡红底 + `border-left:3px solid var(--danger)`）；`warns = anns.filter(a => a.level !== 'block')` → `.run-report__warn`（`:3697` 琥珀提示行）；另有 `r.diff_summary.reason` → `.run-report__diff`（`:525`）、`r.rejections > 0` → 「被拒 N 次」chip（`:509`） | **已实现** |

### 5.2 `level=warn` 硬约束

- App 的 WS `onmessage` 只对 `run.result_rejected` 弹 toast；`level=warn` 的 `annotations` 不经过 WS toast 路径，只出现在完成报告的常驻 `.run-report__warn` 行。**「只常驻提示行、绝不弹 toast」已满足**。
- 「提示行写明『不影响放行』」目前**依赖后端文案**：`TaskDetail.jsx:496` 注释「文案已由后端保证含『不影响放行』」，前端不兜底。本期建议前端显式补，避免后端文案变更导致验收项 AC-07 失效。
- `.run-report__warn` 用 `--warn` 而非 `--danger`，与拦截卡视觉分级正确。

### 5.3 独立错误码呈现（未满足，本期改动）

| 项 | 现状问题 |
|---|---|
| `code=run.empty_diff`（422） | `ERROR_MESSAGES[422] = '数据校验失败，请检查必填项'`；`ApiError.msg` 对 `{code, message, hint, files_changed, rejections, max_rejections}` 结构因 `detail.detail` / `detail.error` 均不存在而 fallback 到 `JSON.stringify(detail)` → 人看到通用文案或整段 JSON，**套用了通用 422 口径** |
| `code=run.empty_diff_repeated`（409） | 同上，`ERROR_MESSAGES[409] = '操作冲突，请刷新后重试'` |
| `error_code` 提取 | `ApiError` 只读 `detail.error`，**读不到 `detail.code`**（架构 §4.2 记窄门禁错误体用 `code` 字段） |
| `message` 提取 | `req()` 未优先取 `detail.message`，后端给的可执行说明被丢掉 |
| 承载边界 | Web UI 无 `api.result()` 调用点，`submit_result` 的 422/409 直接消费方是 Agent（MCP/REST）。Web UI 侧「拦截对人可见」由三链路承担。因此独立错误呈现落在 `api.js` / `ApiError` 层，供 `ErrorBar` / 完成报告 / 未来任何 UI 提交入口复用，**不新增 UI 提交入口**（不扩范围） |

最小改动：`ApiError` 增加 `detail.code` 读取（兼容原 `detail.error`）；`req()` 抛错前 `message` 优先取 `detail.message`；新增 `DIFF_GATE_MESSAGES` 专用映射，两个 code 各给独立文案并带上 `files_changed` / `rejections` / `max_rejections` 与「同 run 内改动后可再交、不消耗 attempt」；`App` 调 `ErrorBar` 时改传 `error` 对象（或让 `userMessage` 按 `error_code` 覆盖），不再用拼接字符串丢 `hint`。

`GET /api/v1/runs/{run_id}/diff-evidence` 提交前自查入口：**可选落点**，在 `TaskDetail` 完成报告区加只读按钮（仅 `task.runs` 有活跃 run 时显示），`api.js` 加 `diffEvidence(runId)` 封装，展示 `mode` / `would_block` / `skipped` / `reason` / `before` / `after`。FR-B05 未强制、冻结口径「不补 UI」，本期默认不做，标【待确认】。

### 5.4 「组件 / 改动 / 验收方式」表

| 组件 / 文件 | 改动 | 验收方式 |
|---|---|---|
| `App.jsx` toast 分支 `:269-271` | 不改逻辑；确认 `run.result_rejected` 的 `entity='task'` 走 `task_update` 通道（后端 `events.py` 的 `MESSAGE_TYPES` 不含 `run`） | 查库 `SELECT * FROM event WHERE type='run.result_rejected' AND entity='task'`（AC-03）；构造拦截后断言 `.toast--danger` 在 4 秒内出现（AC-15 / M4） |
| `App.jsx` toast 容器 `:605-624` | 不改逻辑；确认 `aria-live="polite"` + sticky `role="alert"` + 红色 toast | 无障碍检查 + 冒烟脚本断言 DOM 属性与类名 |
| `TaskDetail.jsx` `HistRow` `:149-176` | 给 `hist-row` 根按 `meta.tone` 加修饰类（如 `hist-row--danger`），并在 `index.css` 补红左边框/淡红底；使「动态」tab 的 `rejected` 记录为整行红行 | 打开被拦任务的「动态」tab，断言存在 `hist-row--danger` 且 `chip--danger` 文案为「已拦截」 |
| `TaskDetail.jsx` `.run-report__warn` `:519-524` | 前端显式补「不影响放行」文案（不依赖 `a.message` 含该字样）；`level=block` 仍走 `.run-report__blocked` | 构造 `MIO_DIFF_GATE=warn` 的 run，断言提示行含「不影响放行」且**无** `.toast--danger` 出现（AC-05 / AC-07） |
| `TaskDetail.jsx` `.run-report__blocked` `:513-518` | 不改逻辑；确认 `level=block` 只在这里呈现红框卡 | 构造 `block` 档拦截，断言完成报告出现 `.run-report__blocked` |
| `api.js` `ApiError` / `req()` / `ERROR_MESSAGES` | 读 `detail.code`；`message` 优先取 `detail.message`；新增 `run.empty_diff` / `run.empty_diff_repeated` 专用文案；禁止这两个 code 回落通用 422/409 文案 | `pytest tests/test_run_diff_gate.py -q`（AC-02，10 用例）；对两个错误体各跑一条单测断言 `ApiError.userMessage` 不含「数据校验失败」「操作冲突」 |
| `ErrorBar.jsx` + `App.jsx:596-603` | App 传 `error` 对象而非拼接字符串，让 `hint` / `error_code` / `status` 可见 | 就近错误条断言含 `hint` 与 `[run.empty_diff]` 标记 |
| `api.js` `diffEvidence`（可选） | 见 §5.3，本期默认不做 | 若做：`curl -s .../api/v1/runs/{run_id}/diff-evidence \| python -m json.tool` 对照 UI 字段 |
| `index.css` 拦截样式 `:3686-3710` | 不改（已存在 `.run-report__blocked` / `.run-report__warn` / `.run-report__diff`） | 视觉回归核对 `--danger` / `--warn` 分级 |
| `EmbedView.jsx` | 本期不改（见 §9 第 11 条） | 明确 `/#/embed` 不在 M4 可见性口径内，或单独确认 |

成本前提：以上为单人维护、熟悉本仓库、含联调与取证的前端改动；不含新增视图、不引入新运行时依赖；具体工期不排日历，由主理人统一口径（需求 §7.2 末、架构 §6.2）。

---

## 6. 无障碍与响应式

| 项 | 实现 | 可验收项 |
|---|---|---|
| 高对比模式 | `App.jsx:143-146` 写 `document.documentElement.dataset.contrast = 'high'\|'standard'` + `localStorage['mio.contrast']`；色彩 token 真源 `ledger.css:155-174`（`--danger` 提亮为 `#ff9090` 等），`index.css:49` 焦点环 3px、`:5282-5295` 面板背景提对比；`Rail` 有 `aria-label` / `aria-pressed` 切换按钮 | 切换高对比后 `html[data-contrast="high"]` 生效；`:focus-visible` 轮廓 ≥3px；冻结/拦截红色仍可辨 |
| `aria-live` toast | `App.jsx:607` `.toast-container aria-live="polite"`；sticky `role="alert"`；`ConnectionBanner` `role="status" aria-live="polite"`；`alert-strip` / `ErrorBar` / `StageAdvanceDialog.shownError` 均 `role="alert"` | 屏幕阅读器能播报拦截 toast；`warn` 标注不产生 `role="alert"` toast |
| 键盘可达 | 全局 `Ctrl/Cmd+K`、`/`、数字 1–9、`n`；`e.isComposing \|\| e.keyCode===229` 组词态不响应；`contenteditable` 与 INPUT/TEXTAREA/SELECT 不抢键；`TaskCard` `tabIndex=0` + Enter/Space + Alt+方向键；`overlayStack` 栈顶 Esc 仲裁；`CommandPalette` `role=combobox`/`listbox`/`option` + `aria-activedescendant` + ↑↓/Enter | 纯键盘完成「切视图 → 打开详情 → 推进阶段 → 关闭浮层」；两层浮层同时开时一次 Esc 只关栈顶（`overlayDepth()` 可断言） |
| DOMPurify（XSS） | `DocPanel.jsx:177`、`IdeasView.jsx:1628`、`SettingsView.jsx:53` 均 `DOMPurify.sanitize(marked.parse(...), {ADD_ATTR:['target']})`；`DocPanel` 外链补 `target=_blank` + `rel=noopener noreferrer` | 用含 `<script>` / `onerror` 的 Markdown 文档验证渲染后被净化 |
| `Skeleton` 加载态 | `Skeleton.jsx`（`aria-hidden="true"`，variants text/line/circle/box）；`App.ViewSkeleton`（`role="status" aria-busy`）；`ListView` / `MemoryView` / `StatsView` 使用；`DocPanel` lazy 首次打开有 `docpanel--loading` 占位 | 冷加载 `IdeasView` / `DocPanel` 不白屏；首屏 `loading` 态有骨架 |
| `ErrorBoundary` | `ErrorBoundary.jsx` 类组件，fallback 降级 UI + 重试 / 刷新；只包 `App.jsx:628-702` 的 `<main className="viewport">` 视图区，浮层/横幅/toast 不在边界内；`onError` 上报到 `/api/v1/memory/observer/ingest`（冻结域端点） | 注入渲染异常断言出现 `.error-boundary` 而非白屏；记录「浮层不在边界内」为已知限制 |
| 响应式 / 减少动效 | 断点分散在 `index.css`（900 / 640 / 1180 / 720 / 560 / 760 / 1100 / 1680 / 1440 / 1220 等），无统一断点常量；`prefers-reduced-motion` 多处（`:1036`、`:3255`、`:4345`、ledger.css `:311`）关闭动效；`@media (hover:none)`（`:1270`） | 640px / 900px / 1180px 三档不出现横向溢出；开启减少动效后 toast / shimmer 无动画 |

已知不一致：`GateStatusCard.jsx:137-159` 的本地 confirm 用 `innerHTML` 拼 `title` / `message`，未走 `confirm.js` / `overlayStack`，与全仓唯一确认点及 Esc 仲裁约定不符（见 §9）。

---

## 7. 前端性能

### 7.1 加载策略

| 手段 | 实现 |
|---|---|
| 按需加载 | 仅 `IdeasView`、`DocPanel` 用 `React.lazy` + `Suspense`；其余 11 个视图 + 全部 shell 组件同步 import |
| 拆包 | `vite.config.js::manualChunks` 分 `hljs` / `markdown`（marked + dompurify）/ `vendor`；`DocPanel` 只注册 10 种 highlight.js 语言，避免全量约 950KB 进首屏 |
| dev 代理 | `/api` → `http://localhost:48620`；`/ws` → `ws://localhost:48620`（`ws:true`）。生产由 hub 挂载 `web/dist` 静态产物到 `/`，无独立 Node 进程 |
| 首屏数据 | `loadTasks()` 拉 `GET /tasks?cancelled=true` 全量 + `loadIdeas()`；`loading` / `refreshing` 两态 + `Skeleton` |
| 持续刷新 | 每 5 秒全量 `loadTasks()` + `loadIdeas()`，无增量、无分页、无 `seq` 游标；`alerts` 30 秒轮询；WS 事件只触发局部 toast / `loadTasks()` |

### 7.2 `cpm.js` 关键路径与大数据量瓶颈

`cpm.js` 是纯函数（无 React/DOM 依赖），`computeCPM(tasks, opts)`：Kahn 拓扑序 O(V+E) → 正向 ES/EF → 反向 LF/LS → `float` → 单条关键路径回溯 → `EPSILON=0.5` 容差下多关键路径 DFS（`allCriticalPaths`）→ 扫描线并行度曲线 → `resourceConflicts`；环节点被防御性排除（`:106-108`）。`taskDuration` 按 stage 退化（completed/done/cancelled = 0，implementing = 0.5×est，review = 0.25×est），`DEFAULT_DURATION=60`。

已知瓶颈（本次源码阅读的**定性判断，无实测数据**，标【待确认】）：

| 位置 | 问题 |
|---|---|
| `TopoView.jsx:92` | `computeCPM(ts)` 未 `useMemo`（`:144` 才用 `useMemo`），每次渲染重算；另有 `kahnLayers` O(V+E) 与无向连通分量 DFS |
| `GanttView.jsx` | 每行一条 DOM、无虚拟化；`ROW_H=44`、`TRACK_BASE=800`、刻度上限 60；任务数增长后 DOM 节点数线性上升 |
| `computeCPM` `allCriticalPaths` | 密集 `float≈0` 子图上 DFS 枚举全部 root→terminal 路径，组合数可能显著增长 |
| `TopoView` / `GanttView` 构图 | 用前端 `tasks[].depends_on` 本地构图，不调 `GET /tasks/graph`（`api.getFullGraph` / `getTaskGraph` 有定义但无调用点），环检测在后端（FR-A09），前端只做防御性忽略 |
| 全量轮询 | 5 秒全量拉 `tasks` + `ideas`，任务量大时网络与重渲染是主要开销；`noise.js` 只做**前端过滤**，仍全量拉取（对应 E7 1771 任务 / 88% 噪声的历史背景） |

后端侧瓶颈按架构 §5 记在 `board` 聚合查询与 `_task_detail` 子查询，缓存为建议项而非已实现项。

### 7.3 「零联网」张力【待确认】

| 证据 | 内容 |
|---|---|
| `mio_taskhub/main.py:513` | `_DASHBOARD_HTML` fallback 内 `<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js">`；`main.py:306-308` 优先读 `web/dist/dashboard.html`，找不到才用内嵌 fallback（架构 §8 待确认 5 已记） |
| `web/index.html:9-11` | **主 SPA** 也引 `fonts.googleapis.com` / `fonts.gstatic.com`（Unbounded / Sora / JetBrains Mono） |
| `ledger.css:18-20` | `--font-display` / `--font-body` 写的是 `"Inter","Segoe UI","PingFang SC","Microsoft YaHei"`，`--font-mono` 含 JetBrains Mono；与 `index.html` 引入的字体族口径不一致 |

与「本地单用户、零联网、零外部依赖」主张存在张力：离线时字体请求失败并回退系统字体，但浏览器仍会发起外部请求。正式打包产物是否保留字体外链、是否有本地字体回退与 CSP、`web/dist/dashboard.html` 是否也依赖 CDN，本轮未核，**标【待确认】**。本节只登记，不改前端设计、不新增投入。

---

## 8. 剩余前端工作与边界

### 8.1 零前端自动化测试（缺口）

取证：`Glob web/**/*.{test,spec}.*` → `No files found`；`web/package.json` 的 `scripts` 只有 `dev` / `build` / `preview`，无 `test` / `lint` / `typecheck`；`devDependencies` 仅 `@vitejs/plugin-react` + `vite`，无 vitest / jest / playwright / @testing-library。即 `web/` 下零测试、零测试框架。

最小补法（假设前提：单人维护、构建期依赖可在内网取得；「零外部依赖」按事实包口径指**运行时**无云服务/无外部进程，新增 devDependencies 属构建期，判断不冲突，仍需主理人确认）：

| 方案 | 内容 | 验收命令 |
|---|---|---|
| A（推荐，先落地） | 引入 `vitest` + `@testing-library/react`（devDependencies）；补 4 条单测：① `cpm.js::computeCPM`（DAG、成环防御、`EPSILON`、并行度）② `stageDocs.js::artifactBody`（text/path 模式）③ `docLifecycle.js::lifecycleTrack` ④ `KIND_META` 与 `DOC_KINDS` / `DOC_PATTERNS` 枚举一致性（§4.2） | `cd web && npm run build`；`npm run test`（`vitest run`） |
| B（冒烟，与 A 叠加） | 引入 Playwright，起 `vite preview` 或 hub 静态产物，跑一条链路：首屏加载 → 切视图 → 打开任务详情 → 注入 WS `run.result_rejected` → 断言 `.toast--danger` 4 秒内可见、「动态」tab 红行、完成报告 `.run-report__blocked`，并断言 `level=warn` 时**无** toast | `cd web && npx playwright test` |

三链路（FR-B05）与 M4「4 秒内可见」目前**没有自动化断言**，AC-07 / AC-15 靠人工观察；建议把上述三链路断言纳入 B，或至少在 A 里补 `addToast` / `HIST_TYPE_META` / `annotations` 分级的纯逻辑测试。`lib/observatoryAdjacency.js` 注释自述「可独立单测」，当前同样无测试。

### 8.2 B 批抢单前端（条件性任务，未触发不得进 ready）

证据：`docs/taskhub/grab-mode/` 下 7 份文档（requirement / architecture / spec / data-model / api / test / runbook）。`requirement.md:16` 明确不含「前端大厅 UI」；`:68`「前端大厅 UI｜另立任务；本次只提供 API」；`:117`「交付边界：不触碰前端」；`test.md:65`「前端大厅交互｜B 批另立任务」。事实包 §5：A 批 9 项（A-1 字段迁移 → A-9 统计）后端已立，B 批前端另立。

触发条件（`requirement.md` §7「重新启用的触发条件」，满足任一才重新评估）：

1. `attempt > 1` 的任务数持续 > 0；
2. 存在 ≥2 个 Agent 同时竞争同一任务；
3. ready 池长期 > 在线 Agent 数 × 3。

另需 A 批 9 项已交付可用。当前实测（`requirement.md` §1.2，2026-10-07）：195 个任务中 `attempt>1` 为 0、在线 Agent 0、ready 池 26（供给过剩而非稀缺）→ **三条均未触发**。按事实包 §4 硬纪律：条件性任务打 `labels += ["conditional"]`，前置结论未出不得进 `ready`；本期不建前端大厅 UI、不写 B 批用例（`test.md:16` 已把前端大厅 UI 排除）。

### 8.3 明确不做（本期边界）

| 不做项 | 依据 |
|---|---|
| 新增看板 / 甘特 / 拓扑类可视化扩展 | N9：能力已存在，继续扩张属功能面通胀，只修缺陷 |
| 多入口合并配套 UI（advance/move、create/create_from_template） | FZ-8 冻结 |
| custom alertrule 前端运营入口 | N2：保留 API，不补 UI |
| 观测台 / 记忆 / Mio 运行时 / 素材面板新 UI | 冻结域，端点保留、不新增投入 |
| taskreview 接线启用（把 `ReviewPanel` / `ReviewQueue` 挂进路由） | N3：降级保留，不接线 |
| 修订指令 / 棘轮基线 / 追溯矩阵 / `quality` 明细的前端呈现 | 本期未纳入，按 FZ-7 与不做项 A-7 登记为缺口（§4.1） |
| 新增 `submit_result` / `PUT /doc` 的 UI 入口 | Web UI 定位是人看板，提交由 Agent 走 MCP/REST；不扩范围 |
| 冻结域入口统一下线（`mio` / `material` 仍在 Rail 主导航） | 属导航调整，不属本期投入，见 §9 |

---

## 9. 【待确认】项（不编造，逐条给缺口与取证方向）

1. **WS 认证口径**：前端 `App.jsx:247` / `EmbedView.jsx:26` 均为 `new WebSocket(\`ws://${location.host}/ws\`)`，未带 `?token=`、未带 Bearer；架构 §4.2 记 `/ws` 单独校验、失败 `close(code=4401)`，`--auth` 时 token 优先取 `MIO_TASKHUB_TOKEN`。前端未注入 token，单 token 模式下 Web UI 与嵌入视图能否连上 WS【待确认】。取证：`main.py::websocket_endpoint` 与前端 token 注入点。
2. **独立错误码的 UI 承载边界**：Web UI 无 `api.result()` 调用点，`code=run.empty_diff` 422 / `run.empty_diff_repeated` 409 的直接消费方是 Agent。FR-B05「独立错误呈现」是只落 `api.js` / `ApiError` 映射层，还是需要在 UI 上新增一个提交入口来呈现【待确认】。本方案按前者写（§5.3）。
3. **`GET /api/v1/runs/{run_id}/diff-evidence` 自查 UI 入口**是否纳入本期。FR-B05 未强制、冻结口径不补 UI，本方案列为可选（§5.3）【待确认】。
4. **「不影响放行」文案归属**：当前由后端 `annotations[].message` 保证（`TaskDetail.jsx:496` 注释），前端未兜底。是否前端显式补（本方案建议补）【待确认】。取证：后端 `diff_gate.py` 生成 annotations 的文案。
5. **`GateStatusCard` 命名与需求措辞**：组件实际展示 `/ideas/gate/status` 的「生产闸门」（想法评审带宽），不是文档/阶段门控状态。需求文档里「GateStatusCard 展示门控状态」指哪一个、是否需改名或另建卡片【待确认】。本方案按源码事实描述，本期不改。
6. **「动态」tab 红行的验收口径**：现状是 `chip--danger` 红色标签，`HistRow` 根节点无 danger 修饰类；AC-07 的「红行」是否要求整行红底/红边【待确认】。本方案在 §5.4 按「需补」列改动。
7. **零联网张力**：`web/index.html` Google Fonts 外链 + `main.py:513` jsdelivr CDN，与 `ledger.css` 字体族口径不一致。打包产物是否保留外链、是否有本地回退 / CSP、`web/dist/dashboard.html` 是否也依赖 CDN【待确认】（架构 §8 第 5 条同理）。
8. **`revision` / `ratchet` / `doc/quality` / `traceability` 端点前端无落点**：是否属本期范围。按冻结口径判断为否，但需主理人确认后写进交付方案的「已知限制」【待确认】。
9. **前端测试框架引入**：新增 devDependencies 是否与「零外部依赖」口径冲突（本方案判断按运行时口径不冲突），以及是否纳入 G3 发布 checklist【待确认】。
10. **未接线组件与潜在缺陷**：`IdeaSearch.jsx` / `TemplatePicker.jsx` 调 `api.post` / `api.get`，而 `api.js` 导出对象无这两个方法；两组件当前无 import 方。`ReviewPanel` / `ReviewQueue` 同样未接线。是否清理、何时清理【待确认】。取证：`api.js` 导出对象 + 全仓 import 检索。
11. **`EmbedView` 是否需覆盖 FR-B05 三链路**：`/#/embed` 独立 WS `onmessage` 只调 `load()`，不弹 toast、不渲染 `TaskDetail` 全套；M4「4 秒内可见」是否包含嵌入视图【待确认】。本方案本期不改 `EmbedView`。
12. **冻结域入口是否统一下线**：`Rail.jsx:94` 只隐藏 `ideas` / `scheduled` / `observability` / `memory`，冻结域的 `mio` / `material` 仍在主导航【待确认】。冻结定义不要求删入口，本方案只登记。
13. **grab-mode 文档链口径**：事实包 §5 列含 `decision`，本次 Glob 在 `docs/taskhub/grab-mode/` 只见到 7 份（无 `decision.md`）；`requirement.md:20` 指向的是 `docs/taskhub/3fa41beb/decision.md`（另一目录）。且 `requirement.md` 为 v0.2（FR 收敛为 5 条），`spec.md` / `api.md` / `test.md` 仍含悬赏 / 竞争数 / 匹配分 / `GrabAttempt`（v0.3 口径）【待确认】。B 批前端触发条件按 `requirement.md` §7 三条引用。
14. **甘特 / 拓扑大数据量瓶颈**：§7.2 为源码定性判断，无实测（任务数阈值、帧率、DOM 节点数、CPM 耗时）【待确认】。
15. **规模口径**：事实包记 49 文件 / 12,532 行 / 10+ 视图；本次 `Glob web/src/**/*` 得 60 个条目（含 `.css`、`.backup`），视图入口 13 + settings + embed。交付方案引用数字时以哪个快照为准【待确认】（同架构 §8 第 1 条的数字漂移问题）。
16. **`GateStatusCard` 本地 confirm 的不一致**：`innerHTML` 拼串、未走 `confirm.js` / `overlayStack`，与全仓唯一确认点及 Esc 仲裁约定不符；是否在本期一并归一【待确认】。按冻结口径（该卡在 `IdeasView` 内，属 FZ-6 相关）本方案不改。

---

（本段交付完毕。三份必读文件已完整读完；三链路按源码现状标注「已实现」，未把现状写成待建；成本/工期只给假设前提，未给确定数字；未虚构事实包之外的功能。）
