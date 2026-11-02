# 活性告警上线记录（2026-11-02）

> 目标：把「0 活跃告警」从结构性问题变成真实告警。对应 PRD R02–R05 的最小闭环。

## 1 根因补充

除了 `alertrule` 表为空，`CustomAlertEvaluator.evaluate()` 也没有被周期调用；即使有规则也不会触发。本次不依赖自定义规则表，直接在 `AlertManager` 增加内置活性检查。

## 2 已上线告警

| 告警 | 条件 | 严重度 |
|---|---|---|
| `TaskQueueStale` | 最老 QUEUED 任务 ≥15min；≥60min 升 critical | warning/critical |
| `NoTaskTransition` | 队列非空且 2h 内 `taskevent` 无跃迁 | critical |
| `NoConsumers` | 队列非空且 0 个 agent `status=online` | critical |
| `SchedulerStateLost` | `enabled=1` 且 `next_run_at IS NULL` | critical |

`SchedulerStateLost` 不会对停用任务误报：当前 3 条 cron 全部 `enabled=0`，因此不触发；启用后若 `next_run` 仍为空才告警。

## 3 生产结果（2026-11-02）

`GET /api/v1/alerts` 返回 3 条活跃告警：

| 告警 | 现场 |
|---|---|
| `TaskQueueStale` | 最老排队 49 分钟，42 条 QUEUED |
| `NoTaskTransition` | 状态机 2 小时无跃迁 |
| `NoConsumers` | 0 个消费者在线，42 条无人认领 |

观测台已显示「活跃告警 3」。

## 4 仍未完成

1. **首屏持久状态条**：告警已在观测台可见，但首屏顶部还没有 PRD G1 要求的不可折叠红条。
2. **24h 事故级**：目前 60min 即 critical，未单独区分 24h。
3. **告警去重/任务级下钻**：R13/R14 未做。
4. **心跳持续 5min 才告警**：当前是即时 0 消费者；没有历史心跳窗口，可能对短暂重连敏感。
5. `alertrule` 自定义规则仍为空；本次走内置检查，不依赖它。

## 5 验证

| 检查 | 结果 |
|---|---|
| 单测 | `test_observability_alerts.py` 2 项通过 |
| 生产 `/api/v1/alerts` | 200，active_count=3 |
| 观测台 | 活跃告警 3，列出三类原因 |
| 部署 | exe sha256 `de658998…`，PID 12260/19640 |

## 6 结论

「0 活跃告警」已闭环为真实告警；系统现在能主动说出「队列停了、没人消费、没人认领」。但首屏状态条和任务级下钻还没做，G1 只完成一半。
