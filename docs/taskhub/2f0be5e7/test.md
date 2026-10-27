# TH-01 测试验收

## 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 最后更新 | 2026-10-27 12:31 |
| 状态 | passed |
| 任务 | 2f0be5e7 |
| 测试文件 | `tests/test_mio_route_outcome.py`（31 例） |
| 执行命令 | `python -m pytest tests/test_mio_route_outcome.py -q` |

## 验收标准

| AC | 条件 | 结果 |
|---|---|---|
| AC-1 | claim 返回符合实际 schema 的 `route`（`count`/`routes[].experienceId`/`agentHealth.best`），无 `confidence` | 通过 |
| AC-2 | runtime 超时/异常/不可用仍按原规则派活 | 通过 |
| AC-3 | 本地约束优先于 route，候选顺序不被改变 | 通过 |
| AC-4 | 同一 Run 重复 submit 不重复累计 outcome，重启后可安全重放 | 通过 |
| AC-5 | retry 的不同 Run 各自保留决策与结果 | 通过 |
| AC-6 | 成功/失败/超时均写回，`traceId` 可关联回 `RouteDecision` | 通过 |
| AC-7a | 已有 verified 的跨项目经验能进入 route | 通过（伪数据通路；真实跨项目命中待环境验证） |
| AC-7b | 本次 outcome 经人工 confirm 后可被 route 读取 | 通过（通路已验证；真实 confirm→route 串联待环境验证） |
| AC-7c | 派发排序变化**不在**本任务验收范围 | 不适用 |
| AC-8 | runtime 写回暂时失败时本地结果完整，可重试补发 | 通过 |
| AC-9 | `task.project` 为空时不调用 runtime、不产生错误项目归属 | 通过 |

## 测试范围

| 层 | 覆盖 | 不覆盖 |
|---|---|---|
| 单元 | 适配层参数与降级、outcome 三态映射、退避与上限、pending/confirm 规则 | runtime 内部检索算法 |
| 集成 | claim→决策记录→submit→outbox→投递、幂等重放、四字端点 | 真实跨项目经验命中（需真实 runtime 数据，见「未覆盖」） |
| 前端 | `npm run build` 通过 | 浏览器交互（无 E2E 框架） |

**未覆盖（需真实环境验证）**：AC-7a/AC-7b 的「真实经验命中」依赖本机已存在 verified reuse 记录与文本匹配，单测用伪数据验证通路；建议上线后按「未覆盖项」手工跑一次真实 claim→submit→confirm→再 claim。

## 用例清单

| TC | AC | FR | 前置 | 预期 |
|---|---|---|---|---|
| TC-1 | AC-2 | FR-2 | MIO_HOME 缺失 | `task_route_status` 返回 `(None, runtime_unavailable)` |
| TC-2 | AC-9 | FR-2 | project 为空 | 返回 `(None, empty_project)` 且不启动子进程 |
| TC-3 | AC-1 | FR-1 | 伪 CLI 正常返回 | 参数含 `--persist-query`、`--agent-id <worker>`、`--scope all`、`--project <task.project>` |
| TC-4 | AC-2 | FR-2 | 伪 CLI 超时 | 返回 `(None, timeout)` |
| TC-5 | AC-2 | FR-2 | 伪 CLI 返回非预期 schema | 返回 `(None, schema_error)` |
| TC-6 | AC-6 | FR-5 | 伪 CLI 正常 | `task_outcome` 带 `--yes` 与真实 `--agent-id` |
| TC-7 | AC-6 | FR-5 | outcome 非法值 | 返回 None，不调用 CLI |
| TC-8 | AC-9 | FR-5 | project 为空 | 返回 None，不写回 |
| TC-9 | AC-1 | FR-3/FR-4 | runtime 可用 | claim 响应 `route` 字段完整；决策记录 run_id/project/route_count 正确 |
| TC-10 | AC-2 | FR-11 | runtime 不可用 | claim 200，`route=null`，决策记录 `runtime_unavailable` |
| TC-11 | AC-9 | FR-2 | 任务 project 为空且不传 project | 不调用 runtime，决策记录 `empty_project` |
| TC-12 | AC-3 | FR-3 | 两条同优先级任务 | 被领取的仍是先创建的那条（route 不重排） |
| TC-13 | AC-5 | FR-4 | 已有 active run | 二次 claim 不重复调用 route，复用同一 decision_id |
| TC-14 | AC-6 | FR-6 | 成功提交 | 生成 1 条 outbox，`outcome=success`，`traceId=taskhub:<task>:<run>` |
| TC-15 | AC-6 | FR-6 | 失败提交 | `outcome=failure`，summary 前缀 `[execution_error]` |
| TC-16 | AC-4 | FR-5 | 同一 run 二次入队 | 返回 False，表中仍只有 1 行 |
| TC-17 | AC-9 | FR-5 | project 为空 | outbox 状态 `dead`，不投递 |
| TC-18 | AC-8 | FR-7 | 伪写回成功 | `status=sent`，attempts=1，last_error 清空 |
| TC-19 | AC-8 | FR-7 | 伪写回失败 | `status=failed`，attempts=1，`next_at` 前移 |
| TC-20 | AC-8 | FR-7 | 伪写回失败达到上限 | 第 8 次后 `status=dead` |
| TC-21 | AC-8 | FR-7 | runtime 整体不可用 | `drain_outbox` 不消耗尝试次数 |
| TC-22 | AC-7b | FR-8 | 伪 pending 含 success 来源 | `available=true`，`confirmable=true` |
| TC-23 | AC-2 | FR-8 | runtime 不可用 | pending 返回 `available=false`、空列表（不报错） |
| TC-24 | AC-7b | FR-8 | `outcomeImproved=false` | confirm 返回 409 `experience.not_confirmable`，不调用 runtime |
| TC-25 | AC-7b | FR-8 | 已确认记录 | confirm 返回 200 `state=already` |
| TC-26 | AC-7b | FR-8 | id 不存在 | confirm 返回 404 `experience.not_found` |
| TC-27 | AC-7b | FR-8 | success 来源 | confirm 返回 `state=confirmed`，confirmedBy 透传 |
| TC-28 | AC-7b | FR-8 | 非 auto_claim 来源 | confirm 返回 422 `experience.not_auto_claim` |
| TC-29 | AC-6 | FR-4 | 已有决策记录 | `GET /mio/route-decisions/{run}` 返回 decision_id |
| TC-30 | AC-6 | FR-4 | 无决策记录 | 返回 404 `route_decision.not_found` |
| TC-31 | AC-8 | FR-7 | outbox 按 run 过滤 | 仅返回该 run，默认不含 payload，`include_payload=true` 时含 |

## 验收流程

| 项 | 内容 |
|---|---|
| 谁验收 | 任务 owner（人）+ 提交 agent 自检 |
| 通过条件 | 31 例全绿；全量回归 1480+ 通过；前端 build 通过；ContractJob 8 项探针全 true |
| 不通过回退 | 按 changelog「回滚」执行；先关 `MIO_OUTCOME_DISABLED=1` 止损，再定位 |
| 遗留 | 真实经验命中的跨项目对照（AC-7a/7b 实跑）建议上线后手工验证一次 |
