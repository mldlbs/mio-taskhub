# mio-taskhub 价值闭环度量报告

| 项 | 值 |
|---|---|
| 日期 | 2026-10-01 |
| 约束 | **只读生产库、纯查询、不改代码、不补埋点** |
| 数据源 | `~/.mio_taskhub/taskhub.db`（唯一来源） |
| 时间窗 | task 2026-08-12 ~ 10-01；run 08-12 ~ 10-01；insight 09-15 ~ 10-01 |
| 样本原则 | n 小即标 `n=`；建不起因果链即标"不可测/证据不足" |

---

## 0. 事实数据（原始，未加工）

### 0.1 规模与分母
| 项 | 值 |
|---|---|
| task 总数 | **299** |
| 有 run 的 task | **99**（分母 299） |
| 有 evidence 的 task | **10** |
| 有 doc_paths 的 task | **45** |
| COMPLETED | **73** |
| run 总数 | **137**（成功 80） |

### 0.2 insight 全量（`n=7`）
| id | 时间(UTC) | kind | metric | severity |
|---|---|---|---|---|
| 78 | 2026-09-15 12:05 | anomaly | taskhub_task_success_rate | critical |
| 79 | 2026-09-15 12:05 | anomaly | taskhub_task_failure_rate | critical |
| 82 | 2026-09-18 02:36 | anomaly | taskhub_task_failure_rate | warning |
| 83 | 2026-09-30 14:05 | remediation | (insight_autotask→db05763c) | info |
| 84 | 2026-09-30 14:05 | remediation | (insight_autotask→a4d6b5ab) | info |
| 85 | 2026-10-01 00:23 | remediation | (insight_autotask→330dd670) | info |
| 86 | 2026-10-01 00:24 | remediation | (insight_autotask→71710851) | info |

### 0.3 `[insight]` 派生任务（`n=4`）
| id | title | state | stage | created |
|---|---|---|---|---|
| db05763c | [insight] taskhub_task_failure_rate | COMPLETED | REVIEW | 09-30 10:05 |
| a4d6b5ab | [insight] taskhub_task_success_rate | COMPLETED | REVIEW | 09-30 10:05 |
| 330dd670 | [insight] taskhub_task_failure_rate | QUEUED | READY | 10-01 00:23 |
| 71710851 | [insight] taskhub_task_success_rate | QUEUED | READY | 10-01 00:24 |

### 0.4 人/Agent attribution **存在**（修正 R5 假设）
`taskevent.actor_type` 分布（前几）：
- `agent:opencode 108`、`agent:codex 20`、`agent:idle-worker-opencode 6`
- `user:api:cancel_task 104`、`user:api:advance_stage 30`
- `system:auto:finalize 64`、`system:scheduler:timeout 37`

→ **可按 actor_type 区分人/agent/system**（此前 R5 误判为"无 attribution"）。

---

## 1. 指标计算（每个带分母/来源/局限）

### M1. Insight → Task 转化率
- **自动派生（可归因）**：`[insight]` 任务 `n=4`；派生记录 `remediation n=4`；
- **转化证据**：4 条 remediation 记录逐条对应 4 个 `[insight]` 任务（task_id 明确写入 description）；
- **转化率**：4 critical insight → 4 派生任务 = **100%**（但 **n=4**，且这 4 条 insight 是**同 2 个 metric 重复触发**：failure_rate ×2、success_rate ×2）；
- **局限**：`anomaly` insight 共 3 条（78/79/82），**无一派生任务**——因为派生器在 09-30 才上线，早于它的 anomaly 未被消费。**真正被消费的只有"上线后新触发"的洞察**。
- **判定**：**机制成立（n=4），但样本极小且 metric 单一**。

### M2. Task → Completed（按来源拆分，避免总体掩盖）
| 来源 | 分母 | 完成 | 完成率 |
|---|---|---|---|
| `[insight]` 派生 | 4 | 2 | **50%**（2 QUEUED 未消费） |
| idea 拆解（有 idea_id） | 62 | 5 | **8%** |
| 自动（label `auto`） | 104 | 1 | **1%** |
| 手动（其余） | 129 | 65 | 50% |
| **合计** | 299 | 73 | 24% |

- **关键**：总体 24% 掩盖了巨大差异——**自动类任务完成率 1%~8%，手动类 50%**。
- **局限**：自动类多为历史空转（已归档）；分母含大量废弃任务。

### M3. Completed → Evidence
- COMPLETED 总数 73；**有 evidence 的仅 8**（65 个 COMPLETED **无 evidence**）；
- 有 run 的 task 99，其中**有 evidence 的仅 10**；
- **判定**：Evidence 是**部分任务适用**（文档链任务），**非任务完成的必要证据**。
  → **"completed→evidence" 不成立为普遍链路**；evidence 只覆盖"跑了文档门控"的那批（n=10）。

### M4. Evidence → 新 Insight（最关键，做严格因果检查）
- anomaly insight 时间：**09-15 12:05、09-18 02:36**；
- evidence 最早时间：**09-22 03:11**；
- **→ anomaly 全部早于最早 evidence**；且 anomaly 由 `InsightsEngine.evaluate(metrics)` **阈值触发**（代码事实），**非 evidence 触发**。
- **判定**：**证据不足——无法建立 "evidence → 新 insight" 因果**。数量上也无对应（insight 7 条无一条来自 evidence）。
- **结论**：**此环当前不可测**（既非数量相等可推断，时间也不成立）。

### M5. Agent 重试率
需区分三种"多次执行"，分别算：
- **真 retry（显式）**：`taskevent.event_type='retried'` / `retry_count>0` → task `retry_count>0` = **7**；
- **失败后重新 claim**：`never_started:requeue` / `scheduler:timeout` 产生的 requeue → 37 条 `scheduler:timeout`；
- **同 task 多 run**：run 按 task 分组 → 1×70, 2×20, 3×8, 5×1（**49 个 task 有多 run**）；
- **run attempt 分布**：attempt1 100 / attempt2 30 / attempt3 9。
- **局限**：同一 task 多 run ≠ 重试（可能是超时回收后重跑，也可能人工多次）；**无单一"重试率"可信定义**。
- **判定**：**给三个分离数（7 / 37 / 49），拒绝合成单一"重试率"**。

### M6. 人工介入次数
- attribution **存在**（actor_type=user），可数 `user:*` 事件：
  - `user:api:cancel_task 104`、`user:api:advance_stage 30`、`user:api:move_to_stage 19`、`user:api:retry_task 2`…
- **但**：`user` 分支**不区分"真人操作"与"经 API/MCP 的 agent 触发"**——agent 调 MCP `advance_stage` 也记为 `user:api:advance_stage`。
- **判定**：**不可测**（无"人 vs agent 经 API"的可信区分）→ 按你的要求标**不可测**，不以状态变化数替代。

---

## 2. 数据覆盖率（缺失数据清单）

| 链路环节 | 覆盖 | 缺失 |
|---|---|---|
| Task | 299 | — |
| Task→Run | 99/299 = 33% | 200 个 task 无 run |
| Run→Completed | 73 | — |
| Completed→Evidence | 8/73 = 11% | **65 个 COMPLETED 无 evidence** |
| Evidence→Insight | 0 可建立 | **无因果** |
| Insight→Task | 4/7 | 3 条 anomaly 未消费（早于派生器） |
| attribution | 有 actor_type | **人 vs agent-经-API 不可分** |

---

## 3. 端到端闭环率（核心指标）

**定义**：`Insight → Task → Agent Run → Completed → Evidence → 后续 Insight`

逐环：
```
Insight→Task   : 4/7=57%（仅上线后新洞察）   ✅机制成立(n=4)
Task→Agent Run : 2/4  [insight] 任务被 agent 执行
Agent→Completed: 2/2 执行者完成
Completed→Evidence: 0/2  ❌（2 个 [insight] 任务无任何 evidence）
Evidence→新Insight: 不可建立 ❌
```

**端到端闭环率 = 0/7 = 0%**（无一条 insight 走完"→…→ Evidence → 后续 Insight"全链）。

**可证明的半链**：`Insight → Task → Agent Run → Completed` 有 **n=2** 的真实贯通（db05763c / a4d6b5ab）。

---

## 4. 可证明结论（事实支撑）

1. **Insight→Task 派生机制成立**（n=4，remediation 记录逐条可归因）。
2. **`[insight]` 任务被真实 agent 执行并完成**（n=2：run ac7f64de/4ae6740c，exit_code=0，COMPLETED）。
3. **无人/Agent attribution 可区分**（actor_type 存在）。
4. **自动类任务完成率（1%~8%）显著低于手动类（50%）**——总体 24% 掩盖了来源差异。
5. **anomaly insight 由 metrics 阈值触发**，非 evidence 触发（时间+代码双重证据）。

## 5. 不可证明结论（证据不足）

1. **Evidence→新 Insight 因果**：时间不成立（anomaly 早于 evidence）+ 无数量对应 → **不可测**。
2. **端到端闭环率**：`Completed→Evidence` 断（2/2 insight 任务无 evidence）→ **闭环未闭**，率 = 0%。
3. **人工介入次数**：无"人 vs agent-经-API"区分 → **不可测**。
4. **单一"重试率"**：三种"多次执行"混同 → **拒绝合成**。
5. **可疑反思**：`[insight]` 任务完成却**零 evidence**——因为 agent 用 `idle_worker` 提交，**未走文档门控/未产生 evidence**。所谓"闭环打通"在 evidence 环**实际断裂**。

---

## 6. 下一步实验（建议，非本轮执行）

| 实验 | 目的 | 前置 |
|---|---|---|
| 让 `[insight]` 任务走**完整文档门控** | 补上 Completed→Evidence | 任务需带 doc_paths（改派生器——需解冻） |
| **evidence 触发的 insight** 专属埋点 | 验证 Evidence→Insight | 需加"证据变化→评估"入口 |
| **可信 attribution**：区分人/agent-经-API | 使人工介入可测 | 需 MCP/API 标注调用者身份 |
| 持续真实消费积累样本 | 提高 n | 空闲计划常驻 |

---

## 7. 一句话总结

> **价值闭环"半链已通（Insight→Task→Agent→Completed，n=2），全链未闭（Completed→Evidence 断，端到端率 0%）。**
> **最诚实的发现**：此前认为"洞察闭环打通"，经度量发现**只到 Completed 为止，Evidence 环缺失**——
> 两个被消费的 `[insight]` 任务**没有任何 evidence**，"闭环"实际止于"agent 跑完"，未进入"可追溯"。

**证据档位**：§0 全为**事实**；§1 为**计算（含分母/局限）**；§4 为**可证明结论**；§5 为**证据不足/不可测**。所有 n 已标注；小样本未包装为系统能力。
