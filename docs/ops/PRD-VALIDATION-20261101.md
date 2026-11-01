# PRD 阻塞问题工程核验（2026-11-01）

> 对象：`docs/ui/product-strategy/prd-mio-hub-2026-11-01.md`
> 方法：只读生产 API + SQLite 快照 + 代码核对；未改数据。
> 结论：**PRD 的“诚实性/一致性”方向成立，但三个关键归因需要修正**；按原假设开工会修错地方。

## 1 79.2% 不是符号取反

PRD 假设：`failure = (total − failed) / total` 符号 bug。
实际代码：`mio_taskhub/observability/report.py`

```python
success_rate = completed / total
failure_rate = failed / total
cancel_rate = cancelled / total
# total = COMPLETED + FAILED + CANCELLED（全部终态）
```

当前真实数据（`GET /api/v1/observability/report`）：

| 指标 | 值 | 口径 |
|---|---|---|
| COMPLETED | 192 | 全部终态 |
| FAILED | 1,528 | 全部终态（含 1,490 条历史修复/insight 噪声） |
| CANCELLED | 210 | 全部终态 |
| total_terminal | 1,930 | 192+1,528+210 |
| report success_rate | **9.95%** | 192/1,930 |
| report failure_rate | **79.17%** | 1,528/1,930 |

同时：

| 来源 | 成功率 | 失败率 | 口径 |
|---|---|---|---|
| `/metrics` | **78.07%** | **21.93%** | 近期窗口，`completed/(completed+failed)` |
| 看板（278 条） | **69.1%** | **20.9%** | 过滤历史噪声后的 192/278、58/278 |
| 统计页（1,970） | **10%** | — | 192/1,970（分母含 QUEUED/非终态） |
| observability report | 9.95% | **79.17%** | 全部终态含 CANCELLED |

**结论**：不是取反，是**四套口径并存**：

1. 分母是否含 CANCELLED；
2. 分母是否含 QUEUED/未终态；
3. 是否剔除 `insight-auto` / `repaired:p1-1` 历史噪声；
4. 时间窗是近期还是全量。

`(192+28)/278 = 79.14%` 只是巧合；按代码复算，79.17% 来自 1,528/1,930。

**对 R07/R08 的修正**：不是 2 人天算术修复，而是口径注册表 + 显式 scope/as-of（R09/R10 必须先做或同批）。先改公式会把 79.17 改成 88.8%，不是变好。

## 2 0 活跃告警：没有规则，不是规则不触发

`GET /api/v1/alert-rules` 返回 `{"rules": []}`；数据库 `alertrule` 表 **0 行**。

因此：

- 不是“规则只统计 active 态所以永不触发”；
- 不是“告警被静默丢弃”；
- 是**根本没有规则**。R02–R05 建好指标后，还必须新建规则，否则依旧 0 告警。

## 3 3 条 cron：全部 `enabled=0`

数据库 `scheduledjob` 实际值：

| 名称 | enabled | next_run_at | last_run_at | run_count | last_status |
|---|---|---|---|---|---|
| 每日数据同步 | 0 | NULL | 2026-09-08 | 6 | OK |
| idea-generate | 0 | NULL | 2026-09-27 | 98 | OK |
| 每日创意生成 | 0 | NULL | 2026-10-08 | 14 | ERROR |

代码 `api/scheduled_jobs.py` 在 pause 时把 `next_run_at` 置 None，resume 时重算。所以 **disabled 任务的 `next_run` 为 NULL 是预期行为**，不是“调度器状态丢失”。

PRD 的 R05“`next_run IS NULL` 即硬告警”会误报全部停用任务。正确判据：

- `enabled=1` 且 `next_run_at IS NULL` → 状态丢失（严重）；
- `enabled=0` → 视为停用，不告警；
- `enabled=1` 且 `now > next_run + 宽限` → 漏执行。

“288 次漏执行”无法由当前数据证明；任务长期 disabled，真实漏执行可能为 0。

## 4 其他阻塞项

| 问题 | 实测 | 判定 |
|---|---|---|
| CPU 216.1% | `report.process.cpu_percent=0.0`；integrity 16.8%；23 线程 | 当前不可复现，需采样确认，不能直接升 P0 |
| SLO 4.4% | report `0.04273`；error_budget `0.0` | 数值存在；R11/R12 成立 |
| 0 Agent 在线 | `agents.online=0`，26 OFFLINE | 与停摆一致；但没有规则，所以无告警 |
| 队列 28 | 当前 `QUEUED=40`（数据已变化） | 数字会漂移；R02 必须实时算，不能写死 |
| 278 vs 1970 | 看板 278、统计 1970 | 口径差异成立；R10 成立 |

## 5 对 PRD 优先级的修正

| PRD | 修正 |
|---|---|
| R07/R08 是 2 人天符号 bug | 实为口径统一，依赖 R09/R10；工作量至少 5–7 人天，且需产品先定主口径 |
| R05 `next_run NULL` 硬告警 | 改为 `enabled=1` 才判丢失；否则误报 3 条停用任务 |
| R19 入口恢复 | 先确认 3 条 cron 是否应启用；若只是停用，启用是配置操作，不是 3 人天重建 |
| R02–R04/R06 | 方向正确，仍应作为 P0；但必须先有规则（当前 0 条） |
| CPU 216% | 保持 P2，先采样 |

## 6 建议开工顺序（修订）

1. **R09/R10 口径注册表 + scope/as-of**（先定主口径：278 还是 1970，产品决策）。
2. **R02–R04 活性指标 + 规则入库**（当前规则 0 条，指标建好也必须建规则）。
3. **R05 按 `enabled` 区分**，不误报停用任务。
4. **R06 as-of/STALE**。
5. **R07/R08 在注册表上重写**，不先改公式。
6. R19 先做“启用/禁用状态核对”，再决定是否重建调度。

## 7 结论

- PRD 的核心命题“系统在两头说谎”成立，但“79.2% 是符号取反”不成立；
- 真正问题是**四套口径**，修复必须以注册表和 scope 标注为中心；
- 0 告警的原因是**0 条规则**，不是规则逻辑；
- cron 的 `next_run NULL` 是 disabled 的预期状态，不能作为硬告警；
- 在上述修正前，不建议直接按原 PRD 的 27 人天阶段一开工。
