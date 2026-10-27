# TH-01 变更记录（Changelog）

## 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 最后更新 | 2026-10-27 12:30 |
| 状态 | draft |
| 任务 | 2f0be5e7 |
| 分支 | `task-2f0be5e7` |
| 变更范围 | claim 路由建议 + submit 异步写回 + 待确认经验人工确认 |

---

## 变更摘要

把 taskhub 与 mio-agent-runtime 的集成从「单向只读」补成「历史决策闭环」。三件事：

1. **读**：`claim` 在选完候选之后附带跨项目历史路由建议 `route`，不参与排序。
2. **写**：`submit` 在本地提交成功后，把真实结果放入 `MioOutbox`，由后台 job 幂等投递到 runtime。
3. **确认**：写回产生的 pending 经验需人工确认后才可能进入 verified 路由集；失败来源不可确认。

runtime 全程是**建议源与写回通道**；taskhub 的 SQLite 仍是权威，所有 runtime 失败一律降级。

---

## 后端变更

| 文件 | 变更 | 关联 FR |
|---|---|---|
| `mio_taskhub/mio_runtime.py` | 新增 `task_route_status` / `task_route` / `task_outcome` / `experience_pending` / `experience_confirm`；白名单增加 `task route`、`task record-outcome`、`experience list`、`experience confirm`；ContractJob 增加 4 条探针（含能力探测） | FR-1、FR-2、FR-8、FR-10 |
| `mio_taskhub/mio_bridge.py`（新增） | 决策记录写入、outbox 入队与投递、pending/confirm 业务规则、查询视图 | FR-3 ~ FR-8 |
| `mio_taskhub/models.py` | 新增 `RouteDecision`、`MioOutbox` 两张表 | FR-4、FR-5 |
| `mio_taskhub/api/tasks.py` | `claim_task` 成功分支附加 `route` 并落决策记录；幂等领取复用已有决策 | FR-2、FR-3 |
| `mio_taskhub/api/runs.py` | `submit_result` 在 commit 之后入队（成功/失败/超时三态），异常不影响提交 | FR-5、FR-6 |
| `mio_taskhub/background.py` | 注册 `MioOutcomeJob`（默认 60s）；系统超时 / agent 掉线按 `aborted` 入队 | FR-6、FR-7 |
| `mio_taskhub/api/mio_runtime.py` | 新增 `GET /mio/experience/pending`、`POST /mio/experience/{id}/confirm`、`GET /mio/route-decisions/{run_id}`、`GET /mio/outbox` | FR-8 |

## 上游补丁（`mio-agent-runtime` bin/mio.js）

| 变更 | 原因 |
|---|---|
| `mio task route` 增加 `--persist-query`（**默认仍为 false**） | 不加时查询不写 `queries.jsonl`，后续 outcome 的 auto-claim 永远不命中，闭环恒为空 |
| `mio task route` 增加 `--agent-id`（默认 `cli`） | CLI 把查询记录的 agent 硬编码成 `cli`，与真实 worker 不一致会导致 auto-claim 不命中；同时避免查询文本被 flag 污染 |
| 备份 | `bin/mio.js.bak-20261027` |

⚠️ npm 升级会覆盖补丁。`ContractJob` 的 `mio_cli_flags` 探针会读 CLI 源文件确认两个标志仍在，缺失即告警。

## 前端变更

| 文件 | 变更 |
|---|---|
| `web/src/api.js` | 新增 `mioExperiencePending` / `mioExperienceConfirm` / `mioRouteDecision` / `mioOutbox` |
| `web/src/components/MioRuntimeView.jsx` | 新增「经验待确认」段：列表 + 确认按钮；失败来源标「不可确认（outcome 未改善）」并禁用；runtime 不可用时显示降级文案 |

## 数据模型

两张新表，只增不改，`metadata.create_all` 建表，**无存量数据迁移**：

- `route_decision`：`id`(rd-`<run_id>`) / `task_id` / `run_id`(unique) / `agent` / `project` / `query` / `route_count` / `top_experience_id` / `agent_health_best` / `degraded_reason` / `at`
- `mio_outbox`：`id` / `key`(unique，`run:<run_id>:outcome`) / `run_id` / `task_id` / `payload` / `status` / `attempts` / `next_at` / `last_error` / `at`

## 行为变化与兼容性

| 场景 | 变化 |
|---|---|
| `claim` 响应 | 只增 `route` 字段（可为 null）；原有字段与候选排序不变 |
| `submit` 响应 | **结构不变**；写回为异步，查询走 `/mio/outbox` |
| runtime 未安装 / 超时 | claim 仍成功（`route=null`，决策记录写 `degraded_reason`）；submit 仍成功（outbox 待重试） |
| `task.project` 为空 | 不调用 runtime、不写查询日志；outbox 直接 `dead`，避免错误项目归属 |
| 失败结果 | 产生 pending 但**不可确认**（`outcomeImproved=false`），不会变成 verified 经验 |

## 升级 / 部署注意

1. 先打上游补丁（或确认 `mio_cli_flags` 探针为 true），否则闭环只读。
2. `MIO_OUTCOME_DISABLED=1` 可关闭后台投递；`MIO_OUTCOME_INTERVAL_SEC` 调间隔（默认 60s）。
3. 本轮新增 4 个 REST 端点，README 统计已同步（161→165）。

## 回滚

| 层 | 动作 | 影响 |
|---|---|---|
| 上游 | 恢复 `mio.js.bak-20261027` | 回到只读 route，闭环退化为单向 |
| 后台 | `MIO_OUTCOME_DISABLED=1` | 停止投递，队列保留可查 |
| 代码 | 回退本分支 | 两张新表保留不用（只增表），无数据丢失 |
| 前端 | 隐藏「经验待确认」段 | 无功能缺失（确认本就需人工） |
