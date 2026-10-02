# 真实样本实验报告 B：扩样本（可追溯执行稳定性）

| 项 | 值 |
|---|---|
| 日期 | 2026-10-02 |
| 目的（技术负责人裁定 B） | 扩"可靠执行+留痕"样本，确认稳定性；暂不做 Evidence→Insight |
| 约束 | 只读验证；不改代码；不手工补 evidence；失败样本保留 |
| Agent | opencode CLI via idle_worker |
| cohort | 修复后（≥2026-10-02 03:20）生成的 `[insight]` 任务 |

---

## 1. 最终结果（以 DB 终态为准）

**n=16，16/16 最终 COMPLETED 且带 ReadEvidence（0 个最终失败）。**

| task_id | runs | ReadEvidence | 最终状态 |
|---|---|---|---|
| 4652def8 | 1 | 2 | COMPLETED |
| c1ccca2c | 2 | 2 | COMPLETED |
| eddb4814 | 1 | 2 | COMPLETED |
| 2a8f4de3 | 1 | 2 | COMPLETED |
| d049aa0a | 1 | 2 | COMPLETED |
| 6ddfcca7 | 1 | 2 | COMPLETED |
| f0dc4e27 | 2 | 4 | COMPLETED |
| 19365eb8 | 1 | 2 | COMPLETED |
| 6c638419 | 1 | 2 | COMPLETED |
| 6bd57c29 | 3 | 2 | COMPLETED |
| 9943f076 | 2 | 2 | COMPLETED |
| 2ba7b06d | 1 | 2 | COMPLETED |
| e59b8096 | 2 | 2 | COMPLETED |
| f0ec340b | 2 | 2 | COMPLETED |
| 11f13703 | 1 | 2 | COMPLETED |
| ad059149 | 3 | 4 | COMPLETED |

- **总 run = 25**（16 任务）→ **9 次重试**（重试来自生命周期失败，最终全部成功）。

---

## 2. 指标（回答 B 的 5 问）

| # | 问题 | 结果 |
|---|---|---|
| 1 | required_reads → read_document 是否稳定 | ✅ **16/16**（每个成功 run 都产生 evidence） |
| 2 | ReadEvidence 是否稳定产生 | ✅ **16/16**，每任务 ≥2 条（spec+requirement） |
| 3 | Completed → Evidence 是否仍近 100% | ✅ **16/16 = 100%**（最终态） |
| 4 | never_started 是否只是偶发 | ⚠️ **非纯偶发**：cohort 内失败尝试 10 次，`never_started 5` + `agent_offline 5`——**每 2.5 个任务就遇 ~1 次生命周期失败**，但**全部靠 requeue 重试最终成功** |
| 5 | 可靠执行 vs 证据驱动反馈 | **可靠执行=成立**；证据驱动反馈=**未建立**（本轮未触及） |

---

## 3. 关键发现

1. **可追溯执行高度可靠**：`required_reads→read_document→2×ReadEvidence→submit→COMPLETED` 在真实 agent 下 **16/16 最终成立**；
2. **失败均属"生命周期类"、且可自愈**：10 次失败尝试全是 `never_started` / `agent_offline`（agent 进程未起/掉线），**无一例门控或阅读问题**；经 heartbeat requeue **最终 100% 恢复**；
3. **重试成本客观存在**：25 run / 16 task = **1.56 run/task**，即 ~56% 的执行需要重试；
4. **insight 生成是 metrics 驱动、自限的**：系统变健康后（成功 run 增加），**anomaly 不再触发 → 不再派生新 `[insight]` 任务** → cohort 自然停在 n=16，**无法被动扩到 n=20**（这是机制特性，非缺陷）；
5. **实验中途 hub 崩溃过一次**（连接拒绝），重启后 reaper 回收了卡住的 phantom run——暴露"hub 是单点、实验依赖它在线"。

---

## 4. 诚实声明与边界

1. **n=16（目标 20，因自限机制未达）**——**明确记录为 n=16，不包装**；
2. **"0 最终失败"不等于"0 失败"**：10 次尝试失败、9 次重试；**最终成功率 16/16 是"含重试"的口径**；
3. **未做 Evidence→Insight**（按裁定 B）；
4. 中途 hub 崩溃一次，已重启；phantom run 由 reaper 回收（非人工干预结果）；
5. 未手工补 evidence / 未改门控 / 未改 prompt（本轮）。

---

## 5. 结论（回答"可靠执行 vs 证据驱动反馈"）

| 能力 | 判定 | 证据 |
|---|---|---|
| **可靠执行 + 留痕** | ✅ **成立**（n=16，含重试最终 100%） | 16/16 completed-with-evidence |
| **证据驱动反馈** | ⬜ **未建立**（本轮未测，上轮已证无通路） | Evidence→Insight 无通路 |

**系统当前稳定提供**：「**可靠执行并留痕（含生命周期自愈）**」。
**尚未提供**：「证据驱动反馈」。

**执行侧成熟度**：7/10 —— 机制稳定，但**生命周期失败率偏高（~40% 的 run 首次失败）**，虽可自愈，**成本与延迟显著**。

---

## 6. 下一步（待技术负责人裁定）

- 是否值得做 **A（Evidence→Insight）**——取决于"证据驱动反馈是否 TaskHub 必须承担的产品价值"；
- 是否处理 **生命周期失败率**（never_started/agent_offline ~40% 首次失败）——属执行/agent 启动问题；
- cohort 自限现象说明：**要持续获得样本，需要系统"有东西可洞察"**（当前健康时不派生）。
