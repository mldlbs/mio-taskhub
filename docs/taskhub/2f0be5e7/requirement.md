# TH-01 历史决策闭环 需求规格

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-10-27 11:12 |
| 状态 | approved |
| 负责人 | opencode |
| 任务 | 2f0be5e7 |
| 适用范围 | 仅覆盖 claim 历史路由注入与 outcome 写回闭环；明确不含 creativity / insight / world model / observer 研究管线 / evolution / 记忆整理 |

---

## 背景与问题

mio-taskhub 与 mio-agent-runtime 的集成目前是**单向只读**：`mio_taskhub/mio_runtime.py` 提供状态、traces、memory、creativity、insight、ferment、policy 等只读读取，`policy_guard.py` 在 5 个危险端点做历史风险门控，`MioDigestJob` 定时写回上下文。runtime 产出的证据能被人看见，但 taskhub **自己不生产证据**。

痛点（按严重度排序）：

1. **派活是盲的**。`api/claim.py:64` 的 `pick_candidate_task` 只按关联度 + priority + FIFO 选任务；`lessons` 来自 `api/task_lessons.recent_lessons`，只查**同 workspace / 同 project**。换一个项目，同类坑会重新踩一遍，跨项目历史完全不参与派发。
2. **失败不变成资产**。`Run` 只有 `result`（文本）与 `exit_code`；`submit_result` 提交后就结束，没有任何经验回流（`claim.py` / `runs.py` 中不存在 `record_outcome` / `adopt` 调用）。第三次遇到同一失败时，系统仍然不知道前两次是谁、怎么失败的。
3. **排障靠猜**。能看见「run 失败 / exit_code=1」，看不见「死在哪个工具调用」，失败原因无法归因（这是 TH-02 的范围，本期只预留关联字段）。
4. **统计口径不完整**。taskhub 只能统计「自己派出去的活」；agent 在 taskhub 之外的会话表现完全没有进入成功率口径。

为什么现在做：只读集成（observer / digest / creativity / insight / policy 五项价值阶梯）已经稳定，契约冒烟任务 `ContractJob` 也已在跑。此时把「读」补成「读 + 写回」，是投入产出比最高的一步；再往后堆面板，只会增加噪声而不改变派发质量。

---

## 目标与非目标

| 类型 | 内容 |
|---|---|
| ✅ 目标 | claim 在**不改变候选排序**的前提下，附带跨项目历史路由建议（route 对象，允许为空） |
| ✅ 目标 | submit 之后把真实执行结果以**幂等、可重试**的方式写回 runtime（成功 / 失败 / 中止三态都写） |
| ✅ 目标 | 建立「决策记录 ↔ 结果记录」关联：每次写回都能追溯回当时的路由建议 |
| ✅ 目标 | 人工确认入口：pending 经验经人确认后才可能进入 verified 路由集；pending 不得自动升级 |
| ✅ 目标 | runtime 超时、异常、未安装时**完全降级**，claim 与 submit 行为与今天一致 |
| 🚫 非目标 | 不引入 creativity / insight / world model / observer 研究管线 / essays / evolution / 记忆 CRUD |
| 🚫 非目标 | 不让 route 影响候选排序、agent 选择、依赖判定、权限或并发控制（`agentHealth.best` 仅展示） |
| 🚫 非目标 | 不做工具级错误归因（TH-02 `trace.query`） |
| 🚫 非目标 | 不把失败结果改写成成功；confirm 的含义是「这条经验值得复用」，不是「任务判定成功」 |
| 🚫 非目标 | 不双写 runtime 的 `memory.jsonl`；不把 taskhub 变成 runtime 的存储 |

---

## 功能需求

| FR | 需求描述 | 优先级 | 验收要点 |
|---|---|---|---|
| FR-1 | 上游 `mio task route` 增加 `--persist-query`：不传时保持 `persistQuery:false` 原行为，只有 taskhub 显式传入才写 `queries.jsonl` | P0 | 不传参数时 `queries.jsonl` 无新增行；传入后新增 1 行且 `agent`/`project` 为真实值；其余 CLI 行为不变 |
| FR-2 | `mio_taskhub/mio_runtime.py` 新增 `task_route()`：以 `task.project` 为 project、以实际领取 agent 为 agent 调用 CLI；`task.project` 为空时**不调用**；任何异常 / 超时（1.5–3s）返回 None，不抛错 | P0 | project 为空时无子进程调用、无查询记录；runtime 不可用时 claim 仍成功；返回结构与 runtime 实际 schema 一致 |
| FR-3 | `claim` 在 `build_claim_context(task)` 之后附加 `route` 对象（可为 null），**不参与** `pick_candidate_task` 排序；`route` 字段包含 `count`、`top_experience_id`、`agent_health_best`、`degraded_reason` | P0 | 同一队列下，route 命中与否不改变被领取的任务；响应保留原有全部字段 |
| FR-4 | 新增 `RouteDecision` 决策记录表：`id`（`rd-<run_id>`）、`task_id`、`run_id`、`agent`、`project`、`query`、`route_count`、`top_experience_id`、`agent_health_best`、`degraded_reason`、`at` | P0 | 每次 claim 至多 1 条；降级时同样落一条并写明 `degraded_reason`；可按 run_id 反查 |
| FR-5 | 新增 `MioOutbox` 结果投递表：`key`（唯一，`run:<run_id>:outcome`）、`run_id`、`task_id`、`payload`、`status`（pending/sent/failed/dead）、`attempts`、`next_at`、`last_error`、`at` | P0 | 同一 run 重复 submit 只产生 1 行；进程重启后 pending 行继续投递 |
| FR-6 | `submit_result` 在 `db.commit()` **之后**入队，不内联调用 runtime；成功、失败、超时三类都写；超时命中 `_is_system_timeout_failure` 时映射 `aborted`，不写 `failure` | P0 | runtime 不可用时 submit 返回不变；三类结果各产生 1 条 outbox；`traceId="taskhub:<task_id>:<run_id>"` |
| FR-7 | 新增 `mio-outcome` 后台重试 job（复用 `Scheduler`）：指数退避、最大尝试次数后置 `dead`；成功置 `sent` | P0 | 首次失败后按退避重试；到达上限不再重试且状态可查；恢复 runtime 后积压可清空 |
| FR-8 | 新增 `GET /api/v1/mio/experience/pending` 与 `POST /api/v1/mio/experience/{id}/confirm`；confirm 必须调用 runtime 真实确认逻辑（`mio experience confirm --ids <id> --by taskhub`），不得只改本地状态；重复 confirm 返回 `already` | P0 | confirm 后 runtime 侧 `confirmed=true`；重复调用不报错且不重复写入；**失败来源的 pending 返回 409 并标注不可确认** |
| FR-9 | MioRuntimeView 增加 pending 列表与确认按钮；失败来源条目显示「不可确认（outcome 未改善）」并禁用按钮 | P1 | 只能对 success 来源条目操作；确认后列表刷新；接口不可用时显示降级文案 |
| FR-10 | `ContractJob` 增加 route / outcome / experience 三条 schema 断言，并固化 `--persist-query` 能力探测 | P0 | runtime 升版改 schema 或去掉该 flag 时告警可见，而非静默失效 |
| FR-11 | 降级与兼容：runtime 未安装、MIO_HOME 缺失、CLI 超时、返回结构异常时，claim / submit / 测试全部按原行为通过 | P0 | 故障注入下 claim 与 submit 全绿；不新增对外阻塞 |

---

## 非功能需求

| 类别 | 要求 |
|---|---|
| 时延 | claim 路径新增开销 p95 ≤ 300ms；单次 route 调用预算 1.5s，超时即降级，不重试 |
| 可用性 | runtime 为**增强依赖**：不可用时 taskhub 功能完整，仅 `route=null`、写回进入 outbox 待重试 |
| 幂等 | 以 `MioOutbox.key` 为唯一幂等键；runtime 侧无幂等能力，重复投递必须在 taskhub 侧拦截 |
| 可观测 | 每条写回可追溯：`run_id` → `RouteDecision` → runtime `trace_id`；失败原因落 `last_error` |
| 数据一致性 | 本地 SQLite 为权威；runtime 为派生与参考。写回失败**不得**回滚或修改已完成的任务状态 |
| 安全 | 不读取、不记录、不外泄 `config.json` 的 `llm` 段（含 apiKey）；outbox payload 不含凭据 |
| 兼容性 | 新增字段全部可选；旧客户端 / 旧 MCP 工具调用不受影响 |

---

## 边界与约束

已核对的 runtime 行为（`mio-agent-runtime@0.18.0`），实现时以本表为准，不得按早期草案假设：

| # | 事实 | 对实现的约束 |
|---|---|---|
| 1 | `mio.task.route` 返回 `{task, project, scope, count, routes[], relatedMemories[], agentHealth, gatedBy, summary}`，`routes[i]={experienceId, reuseCount, confirmed, sourceAgents, targetAgents, score, memory}` | 无 `decision_id` / `recommended_agent_type` / `confidence`；decision_id 由 taskhub 生成 |
| 2 | `mio.task.record_outcome` 入参仅 `{outcome(success\|failure\|aborted), task, summary, verification, agentId, project, traceId}` | 关联信息只能靠 `traceId` 承载，不存在的字段一律不臆造 |
| 3 | CLI `mio task route` 硬编码 `persistQuery:false`，auto-claim 只读 `queries.jsonl` | 不接 FR-1，闭环恒为空（这是先决条件，不是优化项） |
| 4 | `verified = reuse && behaviorChanged && outcomeImproved`，auto-claim 写入 `behaviorChanged:false` | 必须有人工 confirm 环节，否则新经验永不进入 route |
| 5 | runtime 无幂等：`appendJsonl` 无条件追加、`trace_id` 不去重、`agent.taskCount` 每次 +1 | 幂等只能在 taskhub 侧保证（FR-5） |
| 6 | 失败结果的 auto-claim 是 `outcomeImproved=false`，confirm 后仍不满足 verified | 确认面只能对 success 来源开放；失败来源必须显式标注不可确认 |

顺序约束：route 必须在 `pick_candidate_task` **之后**调用。原因有二：route 返回的是「这个任务的历史经验」而非 agent 排名；且 `queries.agent` 必须与后续 outcome 的 `agentId` 一致才能命中 auto-claim。因此 route 天然不参与排序，符合「route 提供建议、taskhub 保留派发权」。

---

## 追溯

| FR | 验收标准 | 主题 |
|---|---|---|
| FR-1 | AC-7b 前置 | 查询持久化 |
| FR-2 / FR-3 | AC-1、AC-2、AC-9 | 路由注入与降级 |
| FR-4 | AC-6 | 决策记录 |
| FR-5 / FR-6 / FR-7 | AC-4、AC-5、AC-8 | 幂等投递与重试 |
| FR-8 / FR-9 | AC-7b | 人工确认闭环 |
| FR-10 | AC-1、AC-2 | 契约冒烟 |
| FR-11 | AC-2、AC-3、AC-8 | 兼容与降级 |
