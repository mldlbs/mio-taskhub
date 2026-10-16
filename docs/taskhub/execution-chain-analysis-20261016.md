# 执行链路分析（2026-10-16）

| 项 | 值 |
|---|---|
| 目的 | 从近期任务执行历史找出执行链路问题 |
| 数据 | 生产库 `run`/`agent` 全量 + 近期时间线 |

---

## 1. run 结果分布（按 agent）

| agent | total | ran(prog>0) | never_started | agent_offline | hb_timeout |
|---|---|---|---|---|---|
| opencode | 210 | 140 | **34** | **29** | 7 |
| inv1-a..e（幽灵） | 362 | 0 | **362** | 0 | 0 |
| idle-worker-opencode | 85 | 19 | 6 | **60** | 0 |
| workbuddy-agent | 8 | 4 | 4 | 0 | 0 |

---

## 2. 问题清单

### P1 · OFFLINE agent 的 run 被 120s 紧窗口提前回收（核心）

- `opencode` agent 注册陈旧（last_heartbeat 停在 **10-13**）→ 回收时 `agent_offline=True`
  → 用紧窗口 `AGENT_OFFLINE_TIMEOUT_SECONDS=120`（而非默认 360s）
- 其 never_started run 回收时长：**min=120 / p50=127 / max=680 秒**
- **34 个 never_started 里 30 个「同任务后续又完成」** → 每任务浪费一次尝试

**根因**：taskhub 把「agent 注册陈旧(OFFLINE)」与「agent 已死」混为一谈 → 合法长任务被提前回收。

### P2 · `never_started` 语义过宽

判定 = `progress==0`，把「真·进程没起来」与「agent 领了但没报进度」混为一谈。

### P3 · 幽灵 agent 反复抢占

`inv1-a..e` 共 **362 runs 全 never_started**（已由 5 处修复闭环）。

### P4 · agent 注册与执行脱节

`opencode`(10-13) / `idle-worker-opencode`(10-03) 的 **agent 心跳停更**但仍在跑任务
→ 注册状态 OFFLINE 与真实存活不符 → 其 run 走紧窗口被提前回收。

---

## 3. 修复（本轮）

**`mio_taskhub/background.py::_mark_stale_agents`**：**有活跃 run（CLAIMED/RUNNING）的
agent 不置 OFFLINE**——有活跃 run 即证明存活。否则其 run 会因 `agent_offline=True` 走
120s 紧窗口被提前回收。

> 效果：活跃 agent 保持 ONLINE → 其 run 用 360s 默认窗口（而非 120s）→ 不再 p50=127s 被误回收。
> 权衡：真死 agent 的 run 清理从 120s 延到 360s（仍有界）。

**测试**：`test_active_run_agent_not_marked_offline`（有活跃 run 不置 OFFLINE）。

---

## 4. 遗留

1. **agent 侧心跳纪律**：agent 应在 claim 后周期性 `taskhub_heartbeat`(run) +
   `taskhub_agent_heartbeat`（保 agent ONLINE）。本仓库已给 `idle_worker` 加保活；
   其他宿主（opencode 会话等）需自行遵守。
2. **`never_started` 语义细化**：区分「进程未起」vs「起了未报进度」（需 agent 侧启动埋点，暂缺）。
