# 幽灵 Agent 发现报告：链路断统计被合成 Agent 污染（2026-10-16）

| 项 | 值 |
|---|---|
| 任务 | P1-OBS 后续专项（8820176b） |
| 触发 | P1-OBS 报告称"链路断 70.7% = inv1 系列 agent 心跳抖动" |
| 结论 | **该结论错误**——inv1 是**幽灵 agent**，其 run 被误判为链路断 |
| 约束 | 只读统计 + 最小修复；不做无证据结论 |

---

## 1. 发现：inv1-* 是幽灵 Agent

`agent` 表中 `inv1-a..e`（5 个）：

| 字段 | 值 | 异常 |
|---|---|---|
| agent_type | cli | — |
| status | **ONLINE** | 但… |
| last_heartbeat | **None** | **从未心跳** |
| registered_at | 2026-10-07 04:42:26（5 个 0.2s 内批量注册） | 合成 |

**它们抢走真实任务却从不启动**：run 的 `result="never started (no heartbeat)"`、`progress=0`。
被抢任务包括 roadmap-05 的 T1.4(089a1fee)/T2.5(1b7681bd)/T4.1(1c684c1c)，乃至本任务 P1-OBS(8820176b，2026-10-16 01:11)。

**规模**：inv1 共 **333** 条 run（10-13 单日 240 条爆发），占 reaper_decision 的 70.4%。

---

## 2. 两个 Bug

### Bug 1：`_mark_stale_agents` 漏掉"从未心跳"的 agent

```python
select(Agent).where(
    Agent.status != OFFLINE,
    Agent.last_heartbeat.is_not(None),   # ← 心跳=None 被跳过
    Agent.last_heartbeat < cutoff,
)
```

`last_heartbeat=None` 的 agent **永不置 OFFLINE** → 幽灵永久卡 ONLINE。

### Bug 2：`analyze_reaper` 用 agent_status 判链路断

幽灵卡 ONLINE → `agent_status=online` → 其 `never_started` run 被归为 **link_broken（链路断）**，
把"幽灵抢占"计成"agent 心跳抖动"。

---

## 3. 修复

| # | 文件 | 修复 |
|---|---|---|
| 1 | `mio_taskhub/background.py::_mark_stale_agents` | 心跳=None 时以 `registered_at` 兜底判超时 → 幽灵置 OFFLINE |
| 2 | `scripts/analyze_reaper.py::classify` | `reaper_kind=never_started` 优先，独立归 **never_started** 桶（不再混入 link_broken） |

---

## 4. 修正后的统计（2026-10-16，n=473）

| 桶 | 修复前 | **修复后** | Top agent |
|---|---|---|---|
| 真死 | 132 (28.0%) | **86 (18.2%)** | idle-worker-opencode(55)/opencode(30) |
| **链路断** | **333 (70.7%)** | **2 (0.4%)** | opencode(1)/lc-probe07(1) |
| 未启动（新增桶） | — | **383 (81.0%)** | inv1-a(96)/inv1-b(67)/inv1-c(58) |
| 观测不足 | 6 (1.3%) | 2 (0.4%) | — |

**关键修正**：**真实"链路断"（agent 在线但 run 心跳超时）仅 2 条（0.4%）**，
而非 70.7%。原 P1-OBS 的"链路断主因"是**幽灵 agent 假象**。

---

## 5. 对 P1-OBS 结论的更正

| 原 P1-OBS 结论 | 更正 |
|---|---|
| "链路断 70.7%，主因 inv1 系列 agent 心跳抖动" | **链路断实际 0.4%（2 条）**；70.7% 是幽灵 agent 的 never_started 被误分类 |
| "证实源审计 Q2『误回收』存疑（lag 307.4 仅超阈值 8s）" | **该"误回收"证据来自幽灵 run**——幽灵 run 的 lag≈超时阈值是 `claim 后从无心跳` 的自然结果，非"活跃 run 被误回收" |
| "T3.1 修复（360s 宽限）针对链路断" | T3.1 宽限**对真实链路断仍合理**（防御性），但**不是**针对 inv1 幽灵——幽灵问题需靠 Bug 1 修复 |

> **诚实边界**：源审计 2026-10-02 的 cohort（n=16，真实 agent）结论不受影响——
> 该 cohort 的 never_started/agent_offline 是真实使用；本次更正的是**全量统计口径**。

---

## 6. 遗留（未做）

1. **幽灵 agent 的来源**：仓库内无 "inv1" 字样 → 由**外部进程**直接写库/注册（未定位）。需排查谁创建 inv1-*。
2. **claim 侧防御**：`claim` 是否应拒绝 `last_heartbeat=None` 或 OFFLINE 的 agent？现幽灵仍可抢任务（本任务亦被抢）。
3. **生产清理**：`inv1-a..e` 仍 ONLINE（修复后下次 sweep 会置 OFFLINE）。
