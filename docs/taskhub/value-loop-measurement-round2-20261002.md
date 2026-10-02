# 价值闭环度量（第二轮 · 修复后 cohort 分离）

| 项 | 值 |
|---|---|
| 日期 | 2026-10-02 |
| 约束 | **只读生产库、纯查询、不改代码、不补埋点** |
| 数据源 | `~/.mio_taskhub/taskhub.db` |
| 与上一轮差异 | **修复前/修复后 cohort 严格分离**（不混成一个数字）；重点回答 item 5「Evidence→后续 Insight」 |
| 上一轮报告 | docs/taskhub/value-loop-measurement-20261001.md |

---

## 0. cohort 边界（事实）

- **cohort A（修复前）**：< 2026-10-02 03:06（执行侧修复 f7d46b5 之前）。anomaly 洞察 78/79/82（09-15/09-18），**无派生任务、无 evidence**。
- **cohort B（修复后）**：≥ 2026-10-02 03:20。`[insight]` 任务由修复后 hub 派生（带 doc_paths），由修复后 idle_worker 消费（prompt 含 required_reads）。

---

## 1. 逐环指标（分 cohort）

### cohort A（修复前）
| 环节 | 值 | 证据 |
|---|---|---|
| Insight(anomaly) | 3（78/79/82，09-15/09-18） | insight 表 |
| Insight → Task | **0**（无派生） | remediation 记录最早 09-30，且不复指 78/79/82 |
| Task → Run | — | — |
| Completed → Evidence | **0** | — |
| **E2E** | **0** | — |

### cohort B（修复后）
| 环节 | 值 | 证据 |
|---|---|---|
| Insight → Task | **7** 派生任务（10-02 03:20 起） | remediation 89–95 各对应一 task |
| Task → Agent Run | **6**（5 完成 + 1 never_started） | run 9d8957e3 等 |
| Run → Completed | **5/6** | 5×COMPLETED |
| **Completed → Evidence** | **5/5**（每样本 2 条 = 10） | readevidence spec+requirement |
| **E2E：Insight → … → Evidence（已消费样本内）** | **5/5** | 5 个已启动且完成的任务均形成完整 Evidence 链（n=5） |

---

## 2. 【最关键】item 5：Evidence → 后续 Insight

**问题**：可靠产生的 Evidence，是否真正改变了后续决策 / 生成新的有效 Insight？

**事实**：
1. `anomaly` 洞察**全部 3 条都在 09-15/09-18**——**早于**最早 evidence（09-22）；
2. 09-18 之后**再无任何新 `anomaly`**；insight 83–95 **全部是 `kind=remediation`**（消费器自身的日志，非新洞察）；
3. **代码事实**：`anomaly` 由 `InsightsEngine.evaluate(metrics)` **阈值触发**（输入=metrics），**不读 evidence**。

**判定**：
> **Evidence → 新 Insight 因果 = 不成立 / 不可测。**
> - 时间：anomaly 早于 evidence；
> - 机制：anomaly 由 metrics 触发，与 evidence 无通路；
> - 数量：0 条 anomaly 出现在 evidence 之后。

⚠️ 注意：09-18 后无新 anomaly，**可能因为指标已健康**（无阈值越界），而非"evidence 抑制"。**但无论如何，都证明 Evidence 没有产出新 Insight**——这条环**当前是断的**。

---

## 3. 修复前 vs 修复后（对照）

| 环节 | cohort A（修复前） | cohort B（修复后） |
|---|---|---|
| Insight → Task | 0 | 7 |
| Task → Evidence | 0 | **5/5（10 条）** |
| **E2E（可追溯执行）** | **无同类样本，不可计算** | **5/5（已启动且完成的任务）** |
| **Evidence → 新 Insight** | — | **0（未建立）** |

**结论（严谨）**：**修复后 cohort：5/5 个已启动且完成的任务均形成完整 Evidence 链；n=5。**修复前 cohort **无可比的 `[insight]` 执行样本，因此不能计算有效的前后提升率**。本次实证的是“**修复后机制成立**”，**不是**“系统整体从 0% 提升到 100%”。

---

## 4. 数据覆盖率

| 环节 | 覆盖 |
|---|---|
| Task → Run | 108/311 = 35% |
| Run → Completed | 73 |
| Completed → Evidence | 16/73 = 22%（修复后样本显著拉高） |
| Evidence → 新 Insight | **0** |

---

## 5. 可证明 / 不可证明

**可证明**：
- 修复后 `Insight→Task→Agent→Completed→Evidence` 闭环 **5/5 成立**（cohort B，n=5）；
- 修复前 cohort **无同类 `[insight]` 执行样本**（78/79/82 无派生）→ **不可计算前后提升率**；
- ReadEvidence 精确 2 条/样本，与 required_reads 对应。

**不可证明 / 不可测**：
- **`Evidence → 新 Insight`**：既无时间先后（anomaly 早于 evidence），也无机制通路（anomaly 由 metrics 触发）；**记为"未建立/不可测"**；
- 无"证据驱动后续决策"的任何证据。

---

## 6. 阶段门判定

按技术负责人的阶段门：
```
控制面约束        ✅
执行面协议        ✅
真实 Evidence     ✅ n=5
E2E 可追溯执行    ✅ 5/5 (n=5, 无前后提升率)
        ↓
证据 → 洞察        ❌ 当前 0 / 未建立   ← 停在这里
        ↓
价值闭环
```

**当前系统做到**：「**可靠执行并留痕**」（E2E 可追溯，n=5 证明）。
**尚未做到**：「**证据驱动反馈**」（Evidence→Insight 无通路）。

---

## 7. 下一步要回答的问题（不在本轮，需另行设计）

现在最值得回答的**已不是**"TaskHub 能否让 agent 正确执行"，而是：

> **这些可靠产生的 Evidence，是否/如何能改变后续决策或生成新的有效 Insight？**

要回答它，**需要一个"证据→评估"的通路**（当前 anomaly 只吃 metrics、不吃 evidence）。这属于**新能力/架构改动**，**本轮严格未动**，待你裁定。

**诚实边界**：cohort B 的 E2E=100% 是"**已消费的 5 个样本内**"；整体 cohort B 为 5/7=71%（2 个仍 QUEUED）。n=5 小样本，未包装为系统级能力。


---

## 8. 表述修订（2026-10-02，按技术负责人裁定）

**原表述「E2E 可追溯执行 0% → 100%」不严谨，已撤回**：
- cohort A（修复前）**没有可比的 `[insight]` 执行样本**（anomaly 78/79/82 从未派生任务）；
- 因此 **0% 不是"失败基线"，而是"无样本"**，**不能计算有效的前后提升率**。

**严谨表述**：
> **修复后 cohort：5/5 个已启动且完成的任务均形成完整 Evidence 链；n=5。修复前 cohort 无可比的 `[insight]` 执行样本，因此不能计算有效的前后提升率。**

本次实证的是"**修复后机制成立**"，不是"系统整体从 0% 提升到 100%"。

**下一步 = B（扩样本，非 A）**：把修复后 cohort 扩到 n≥20，确认
① required_reads→read_document 稳定；② ReadEvidence 稳定；③ Completed→Evidence 仍近 100%；
④ never_started 是否偶发。**暂不为闭环而造 Evidence→Insight**——语义上二者不天然相连，
需先确认它是否 TaskHub 必须承担的产品价值。
