# 活性告警上线记录（2026-11-02）

> 目标：把「0 活跃告警」从结构性问题变成真实告警。对应 PRD R02–R05 的最小闭环。

## 1 根因补充

除了 `alertrule` 表为空，`CustomAlertEvaluator.evaluate()` 也没有被周期调用；即使有规则也不会触发。本次不依赖自定义规则表，直接在 `AlertManager` 增加内置活性检查。

## 2 已上线告警

| 告警 | 条件 | 严重度 |
|---|---|---|
| `TaskQueueStale` | 最老 QUEUED 任务 ≥15min；≥60min critical；≥24h 标记事故 | warning/critical |
| `NoTaskTransition` | 队列非空且 2h 内 `taskevent` 无跃迁 | critical |
| `NoConsumers` | 队列非空且 0 个 agent `status=online` **持续 ≥5min** | critical |
| `SchedulerStateLost` | `enabled=1` 且 `next_run_at IS NULL` | critical |

`SchedulerStateLost` 不会对停用任务误报：当前 3 条 cron 全部 `enabled=0`，因此不触发；启用后若 `next_run` 仍为空才告警。

## 3 生产结果（2026-11-02）

部署后 `GET /api/v1/alerts` 返回 1 条事故级告警：

| 告警 | 现场 |
|---|---|
| `TaskQueueStale` | 最老排队 **4,453 分钟（约 3 天）**，40 条 QUEUED，`labels.incident=true` |

`NoConsumers` 需持续 5 分钟才触发，部署瞬间不出现；`NoTaskTransition` 在近期有事件时自动不触发。观测台与首屏状态条显示红条「系统异常」。

### 3.1 首屏持久状态条

`App` 每 30 秒拉取 `/api/v1/alerts`，有 active 告警时在 MissionBar 下方渲染不可关闭的状态条：

- critical → 红条「系统异常」；
- warning → 琥珀条「系统警告」；
- 文案列出告警类型（队列陈旧/无跃迁/无消费者/调度丢失）；
- 「查看观测台 →」一键跳转。

截图见 `banner.png`（本地）。G1 的「首屏 15 分钟内说出异常」已具备。

## 4 仍未完成

1. **告警去重/任务级下钻**：R13/R14 未做。
2. **心跳持续窗口的持久化**：`NoConsumers` 的 5min 防抖在进程重启后重新计时；未落库。
3. `alertrule` 自定义规则仍为空；本次走内置检查，不依赖它。
4. 首屏状态条只列出告警类型，未列出具体任务 ID；下钻仍需手动进观测台。

## 5 验证

| 检查 | 结果 |
|---|---|
| 单测 | `test_observability_alerts.py` 2 项通过 |
| 生产 `/api/v1/alerts` | 200，active_count=2–3（动态） |
| 首屏 | 红条「系统异常 2 条活跃告警」可见，不可关闭 |
| 观测台 | 活跃告警列表 |
| 部署 | exe sha256 `4686c1c0…`，PID 38688/43664 |

## 6 结论

「0 活跃告警」已闭环；首屏状态条也已完成。系统现在能主动说出「队列停了、没人消费、没人认领」。剩余是 24h 分级、去重与任务级下钻，以及心跳窗口防抖。
