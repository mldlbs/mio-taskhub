# spec：insight 自喂养闭环修复（指标自度量 + ack 不止血 + 冷却到期复燃）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 更新时间 | 2026-10-03 08:15 |
| 状态 | draft |
| 作者 | opencode（[insight] 复核轮，源 insight #130/#136） |
| 适用范围 | `mio_taskhub/observability/metrics.py`、`insights.py`、`insight_remediator.py` |
| 上游 | task 78d5fac5 复核结论、task e83cc9e2 验收标准 1-7 |

## 模块职责与边界

| 模块 | 职责 | 本次改动 | 边界（不改） |
|---|---|---|---|
| `observability/metrics.py` | 渲染 Prometheus 指标；终态比率口径 | 是（分母排除监控自造任务） | 不改线程/连接池/延迟指标；不改 SLO 30d 口径 |
| `observability/insights.py` | 阈值判定 + insight 落库 + dedup | 是（dedup 刷新值 + 恢复检测） | 不改阈值常量本身 |
| `observability/insight_remediator.py` | 消费未确认 insight → 派生跟进任务 + ack | 是（终态即 ack、派生前复核） | 不自动执行修复；不新增调度线程 |

## 详细设计

### 1. 关键结论：唯一能清除 critical 告警的是指标分母，不是 ack

线上 `/metrics`（2026-10-03 08:05，窗口 30d）：

| 指标 | 值 | 阈值 | 严重度 |
|---|---|---|---|
| `taskhub_task_success_rate` | 0.8198 | warning < 0.9 | warning |
| `taskhub_task_failure_rate` | 0.1802 | critical > 0.15 | critical |
| `taskhub_task_attempted_total` | 111 | — | — |

分母构成（同库复算，DB 逐位吻合）：

| 口径（30d 窗口） | completed | failed | success_rate |
|---|---|---|---|
| 全部任务（= 线上口径） | 91 | 20 | 0.8198 |
| 仅 `insight-auto` 任务 | 19 | 18 | 0.5135 |
| 仅真实交付任务 | 72 | 2 | 0.9730 |

18 条 FAILED 的终态原因 **100% 为 `agent offline`**（`SELECT DISTINCT run.result`，n=18，最后一条 2026-10-02 23:37:33Z）。它们全部由 insight 派单器自己派生——**指标在度量自己派出的任务，数学上不可能靠"多派任务"把失败率压下去**。7d 窗口仅真实任务 40/40 = 1.0000；全历史仅真实任务 75/5 = 0.9375。

因此：
- 验收标准第 4 项（分母排除 `insight-auto`）是**唯一**能让 `failure_rate` 回到 critical 阈值以下的改动。
- 第 1/2/3 项（终态 ack、派生前复核、dedup 刷新）只降噪，**不会**清除当前 critical：实测条件仍成立（0.1802 > 0.15）。
- 排除后 30d 窗口内仍有历史 18 条 FAILED（要到 2026-11-01 才滑出窗口），故必须**同时**改口径；只做数据修复无法阻止指标再次被污染。

### 2. 变更 1：`metrics.py` 分母排除监控自造任务（最高优先）

`mio_taskhub/observability/metrics.py:264-273` 的 WHERE 追加：

```sql
AND (labels IS NULL
     OR (labels NOT LIKE '%insight-auto%' AND labels NOT LIKE '%insight-followup%'))
```

配套要求：
- 同时输出被排除的样本量，便于审计：`taskhub_task_excluded_monitoring_total`。
- 保留 `taskhub_task_monitoring_success_rate` 作为「监控自身派单的完成率」独立指标——这才是该子系统真正该盯的 SLO。
- 新增单测：构造含 `insight-auto` FAILED 与真实 FAILED 的库，断言 `success_rate` 只由真实任务决定。

### 3. 变更 2：终态即 ack（`insight_remediator.py:119-135`）

`_create_followup` 的去重分支当前只在 `state == COMPLETED` 时调 `_acknowledge_insight`，FAILED/CANCELLED 直接 `return None`。改为：命中冷却期内的任一终态（COMPLETED/FAILED/CANCELLED）即 ack 源洞察并返回。

### 4. 变更 3：派生前复核实时指标

`consume()` 在 `_create_followup` 之前用实时 metrics 复算：若当前值已回到 baseline 以内，ack 源洞察且不派生。注意本项在当前数据下**不会触发**（值未恢复），保留它是为了防止"延迟数小时的快照派单"（本次 78d5fac5 即为 8.7h 后出生）。

### 5. 变更 4：dedup 刷新 + 恢复检测（`insights.py store()`）

命中 dedup 时刷新 `metric_value`，并在值回到阈值内时把该 insight 标为已恢复/ack，避免化石快照永久驻留。

### 6. 已 refute 的假设（不必重复排查）

- **cf0523c 已部署**：`dist/mio-taskhub/mio-taskhub.exe` 构建于 19:56:52，commit `cf0523c` 于 19:55:51；线上 `/metrics` 已含 `taskhub_task_terminal_window_days 30` 与 `taskhub_task_attempted_total 111`，后者与本地 DB 独立复算逐位吻合。此前几轮「旧二进制 / 未部署」的判断对本次窗口化修复**不成立**。
- **ack 不是止血**：ack 掉 insight #130 后，下一个评估周期立即生成同值新行 #136（success_rate 0.8198, warning, acknowledged=0）。ack 只是把 dedup 复位，条件仍在就会持续再生。#82（2026-09-16 化石 warning）已一并 ack 清理。

### 7. 时间约束（决定优先级）

`[insight] taskhub_task_failure_rate` 最近一条 COMPLETED 任务终态于 2026-10-02 09:00:46Z，冷却期 24h 于 **2026-10-03 09:00:46Z（本地 17:00）到期**。届时若 critical 条件仍成立，insight_remediator 会派生第 N+1 个 `[insight] taskhub_task_failure_rate` 任务，循环重新自喂养。

| 处置 | 结果 |
|---|---|
| 变更 1 在 09:00Z 前上线 | failure_rate ≈ 0.027 → critical 消失，不再派生 |
| 变更 1 未上线 | 每 24h 派生一条新 insight 任务，每条再制造 1 条 FAILED（agent offline），失败率被进一步推高 |

### 8. 测试与部署判据

- 回归：`tests/test_metrics.py tests/test_insights.py tests/test_insight_remediator.py` 全绿；新增上述过滤分支单测。
- 部署判据（沿用 mem_1790935285758 教训）：线上 `/metrics` 出现 `taskhub_task_excluded_monitoring_total`，且 `taskhub_task_failure_rate ≤ 0.05`、`taskhub_task_success_rate ≥ 0.9`。只改工作树不算完成，必须重建 exe 并重启后复测。
- 数据修复（可选、需人工确认）：18 条 `insight-auto` FAILED 是监控工件而非交付物，建议经 `ops/data_fixes.py` 归一为 CANCELLED；口径修复落地后这一步不再紧急。

### 9. 非目标

- 不改阈值常量（0.9 / 0.15）来「消警」。
- 不给 idle_worker / agent offline 生命周期做修复（另见 task 5b4fa957 P1-OBS-2 埋点）。
- 不删除历史 insight 行。