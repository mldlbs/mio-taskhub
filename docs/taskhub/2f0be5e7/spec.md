# TH-01 历史决策闭环 设计规格（Spec）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-10-27 11:21 |
| 状态 | approved |
| 负责人 | opencode |
| 任务 | 2f0be5e7 |
| 上游依据 | 需求规格 requirement（FR-1 ~ FR-11） |
| 下游关联 | 接口契约 api（route 与 outcome 两个结构）、测试验收 test |

---

## 1 模块职责与边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| `mio_taskhub/mio_runtime.py` 新增函数 | `task_route()` / `task_outcome()` / `experience_pending()` / `experience_confirm()`：CLI 子进程调用、超时、JSON 解析、失败降级 | 不直接读写数据库、不决定何时调用、不做业务判断 |
| `api/tasks.py` `claim_task` | 在**选完候选之后**附加 `route`；写一条 `RouteDecision`；任何异常不影响领取 | 不让 route 参与候选排序；不因 route 失败回滚 run |
| `api/runs.py` `submit_result` | 在 `db.commit()` 之后把结果写入 `MioOutbox`；唯一键保证幂等 | 不内联调用 runtime；不因写回失败改变提交结果 |
| `RouteDecision` / `MioOutbox` 表 | 决策记录与结果投递的唯一本地权威 | 不重复存储 runtime 的跨项目历史 |
| `MioOutcomeJob`（`background.py`） | 异步投递 outbox、指数退避、超限置 `dead` | 不修改任务状态、不触发 retry |
| `api/mio_runtime.py` 新增端点 | pending 查询、人工确认、决策反查、outbox 查询 | 不自动 confirm；不改任务判定 |
| `MioRuntimeView` | 展示 pending 与确认按钮 | 不承担批量运维、不展示 runtime 内部文件 |
| `ContractJob` | 固化 route / outcome / experience 三条 schema 断言与 `--persist-query` 能力探测 | 不替代业务测试 |

边界总原则：

1. **runtime 是建议源，taskhub 是决策源**。任何 runtime 返回都不得直接改变任务状态、排序、权限或并发。
2. **本地 SQLite 为权威**，runtime 为派生参考。写回失败不回滚本地结果。
3. **单向依赖**：只走「只读文件 + CLI 白名单子进程」，不 import Node 包、不维护 MCP 客户端。
4. **fail-open**：runtime 全部能力缺失时，claim / submit / 测试行为与今天完全一致。

---

## 2 详细设计

### 2.1 数据模型

`RouteDecision`（决策记录，一次 claim 至多一条）：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | PK | `rd-<run_id>`，taskhub 生成（runtime 无此字段） |
| `task_id` | TEXT | NOT NULL, index | |
| `run_id` | TEXT | NOT NULL, UNIQUE | 与 Run 一一对应 |
| `agent` | TEXT | NOT NULL | 实际领取者，必须与后续 outcome 的 `agentId` 一致 |
| `project` | TEXT | NOT NULL | 取 `task.project` |
| `query` | TEXT | NOT NULL | 传给 route 的文本：`title + 空格 + description 前 300 字` |
| `route_count` | INT | NOT NULL, default 0 | |
| `top_experience_id` | TEXT | nullable | `routes[0].experienceId` |
| `agent_health_best` | TEXT | nullable | `agent:rate:total` 快照，仅展示 |
| `degraded_reason` | TEXT | nullable | `runtime_unavailable` / `timeout` / `empty_project` / `schema_error` / `none` |
| `at` | DATETIME | NOT NULL | |

`MioOutbox`（结果投递，一次 run 至多一条）：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | PK | |
| `key` | TEXT | NOT NULL, UNIQUE | `run:<run_id>:outcome` —— 唯一幂等键 |
| `run_id` | TEXT | NOT NULL, index | |
| `task_id` | TEXT | NOT NULL, index | |
| `payload` | TEXT | NOT NULL | JSON：`{outcome, task, summary, verification, agentId, project, traceId}` |
| `status` | TEXT | NOT NULL | `pending` / `sent` / `failed` / `dead` |
| `attempts` | INT | NOT NULL, default 0 | |
| `next_at` | DATETIME | NOT NULL | 下次可投递时间 |
| `last_error` | TEXT | nullable | 最近一次失败原因（截断 500 字） |
| `at` | DATETIME | NOT NULL | 入队时间 |

建表策略：两张表以 SQLModel 声明，随 `metadata.create_all` 建立；无存量数据迁移。回滚方式 = 停止 job + 忽略两张表（只增表，不影响既有读写），必要时删除。

`MioOutbox` 状态机：

| 当前 | 事件 | 下一 | 副作用 |
|---|---|---|---|
| pending | 投递成功 | sent | 记录 attempts+1 |
| pending | 投递失败且未达上限 | failed | attempts+1、`next_at=now+backoff`、写 `last_error` |
| failed | 到 `next_at` 且投递成功 | sent | |
| failed | 投递失败且达上限 | dead | 停止重试，保留可查 |
| sent / dead | 任意 | 不变 | 终态，不再投递 |

### 2.2 claim 流程（FR-2 / FR-3 / FR-4）

```
claim_task(agent, ...)
  ├─ 已有 claimed/running run → 复用已有 RouteDecision（若存在），不新建
  └─ claim_for(...) → run, task
       ├─ 回填 task.project / workspace / files → commit（run 已建立）
       ├─ build_claim_context(task)
       ├─ route = task_route(task, agent)          # 新增
       │    ├─ task.project 为空 → None，reason=empty_project，且不启动子进程
       │    ├─ 子进程超时 → None，reason=timeout
       │    ├─ 返回非 JSON / 缺 routes 字段 → None，reason=schema_error
       │    └─ 正常 → {count, top_experience_id, agent_health_best, routes_summary}
       ├─ 写 RouteDecision（try/except，失败只记日志，不影响响应）
       └─ resp["route"] = route（可为 null）
```

关键约束：

- route 调用**必须晚于** `pick_candidate_task`；`queries.agent` 与 outcome 的 `agentId` 必须同为本次领取者，否则 auto-claim 永远不命中。
- `query` 不塞 `acceptance_criteria`，避免检索噪声（score 用 token 重叠）。
- route 超时预算 1.5s；`pick_candidate_task` 与 `build_claim_context` 逻辑零改动。

### 2.3 submit 与投递（FR-5 / FR-6）

`submit_result` 在现有逻辑与 `db.commit()` 全部完成、响应对象构造之前，追加：

```
key = f"run:{run.id}:outcome"
INSERT MioOutbox(key, run_id, task_id, payload, status="pending", next_at=now)
  └─ IntegrityError → 已入队，直接跳过
  └─ 其它异常 → 记日志，忽略（绝不影响提交结果）
```

结果映射（唯一的字段转换点）：

| taskhub 事实 | runtime 参数 | 规则 |
|---|---|---|
| `success=True` | `outcome=success` | |
| `success=False` 且 `_is_system_timeout_failure` | `outcome=aborted` | 系统超时不是 agent 能力问题，不得记为 failure |
| `success=False` 其它 | `outcome=failure` | |
| `task.title` | `task` | |
| `"[{failure_type}] " + (result or "")[:200]` | `summary` | `failure_type` 取值 `timeout` / `gate_rejected` / `execution_error` / `unknown`，无法归类即 `unknown`，不臆造根因 |
| gate / diff 结论摘要 | `verification` | 只写已有事实 |
| `run.agent_name` | `agentId` | |
| `task.project` | `project` | 为空则 `dead`（不投递），避免错误归属 |
| `taskhub:<task_id>:<run_id>` | `traceId` | 关联 RouteDecision 的唯一路径 |

被门控拒绝的提交（422 read evidence / diff gate）不产生 outbox 行 —— 因为 run 并未被接受。

### 2.4 重试 job（FR-7）

`MioOutcomeJob(Scheduler)`：间隔 60s，每轮取 `status in (pending, failed) and next_at <= now` 至多 20 条。

- 退避：`min(30s * 2^attempts, 1800s)`，`attempts >= 8` 置 `dead`。
- 单条投递失败不影响同批其它条目。
- `MIO_OUTCOME_DISABLED=1` 时 no-op（便于测试）。
- 注册方式与 `mio-digest` / `mio-contract` 一致。

### 2.5 pending 与人工确认（FR-8）

| 动作 | 数据源 | 说明 |
|---|---|---|
| 查询 pending | `mio experience list --json --status pending [--project P] --limit N` | 返回 `{count,total,status,records[]}`，只透传 `id`、`targetAgent`、`experienceId`、`outcomeImproved`、`project`、`timestamp` |
| 确认 | `mio experience confirm --ids <id> --by taskhub [--no-improved]` | 返回 `{confirmed, confirmedCount, confirmedIds, notFound, rejected, alreadyConfirmed}` |

语义规则（来自已核实的 runtime 行为）：

- pending 记录是 **reuse 记录**，确认要用它的 `id`，不是 `experienceId`。
- `confirm` 只对 `source=auto_claim` 生效；非 auto_claim 返回 `rejected`。
- 重复确认返回 `alreadyConfirmed`，接口据此回 `already`（200），不报错、不重复写入。
- **失败来源不可确认**：auto-claim 对失败结果写 `outcomeImproved=false`，即使 confirm 也不满足 `verified`（verified 要求 reuse 且 behaviorChanged 且 outcomeImproved 同时为真）。因此接口在 `outcomeImproved=false` 时直接返回 409 `experience.not_confirmable`，不调用 runtime。这是把「确认」限定为「值得复用」，而不是把失败改写成成功。
- 确认后经验是否真能进 route，还取决于 project / scope / 文本匹配 —— 接口响应必须能区分「确认失败」「查询条件不匹配」「过滤规则未通过」三种情形，避免"确认了却没生效"。

### 2.6 降级矩阵（FR-11）

| 场景 | `route` | `RouteDecision.degraded_reason` | claim | submit |
|---|---|---|---|---|
| MIO_HOME 缺失 | null | `runtime_unavailable` | 成功 | 成功，outbox 保留 |
| `mio` CLI 不存在 | null | `runtime_unavailable` | 成功 | 成功，outbox 保留 |
| CLI 超时 | null | `timeout` | 成功 | job 稍后重试 |
| 返回结构异常 | null | `schema_error` | 成功 | job 稍后重试 |
| `task.project` 为空 | null | `empty_project` | 成功 | outbox 直接 `dead` |
| 正常 | 对象 | `none` | 成功 | `sent` |

### 2.7 与既有门控的关系

- Read Evidence、diff gate、completion gate 全部**先于** outbox 入队执行；入队只发生在提交成功路径。
- outbox 写失败不得回滚 run；不得改变 HTTP 响应体。
- `RouteDecision` 不参与任何门控判定，只用于追溯与后续统计（TH-03）。

### 2.8 前端（FR-9）

`MioRuntimeView` 增加「经验待确认」段：列表（experienceId 摘要、project、时间、outcomeImproved 标记）+ 单条确认按钮。失败来源条目标注「不可确认（outcome 未改善）」并禁用按钮。runtime 不可用时显示降级文案，不阻塞整页。

### 2.9 契约冒烟（FR-10）

`ContractJob` 增加三项断言：

| 探针 | 断言 |
|---|---|
| `task route` | 输出含 `count`（int）、`routes`（list）、`agentHealth`（object/null） |
| `task record-outcome` | 预演模式返回 `preview=true` 且 `applied=false`（未加 `--yes`） |
| `experience list` | 输出含 `count`、`total`、`records`（list） |

另加 `--persist-query` 能力探测：`mio task route --persist-query "<probe>"` 必须退出码 0；失败则告警，提示上游补丁缺失。
