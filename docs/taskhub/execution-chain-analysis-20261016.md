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

### P5 · bounce_count 不因 retry 重置（retry 失去意义）

- 幽灵洪泛致合法任务被反复派给幽灵 → `never_started` requeue → `bounce_count++`
- 实测 **T0.1 卡 3 天**（10-10→10-13），被 dispatcher 派给幽灵 6 次 → bounce 累积到 **14**
- `bounce>=max_bounces(6)` → FAILED；手动 retry（T16）**未重置 bounce** → 首个 never_started 立即再 FAILED → retry 无效
- 事件统计：`task_assigned 397` / `reaper_decision 505` / `agent_offline 159`（幽灵驱动的高churn）

### P6 · 任务移到终态不关闭活跃 run（run 悬挂）

- `stage/move` 到 `done`/`cancelled` 只迁移 **task** 状态，**不关闭其 CLAIMED/RUNNING run**
- 实测 **run b5ae9267 悬挂 23.9h**（task 03:34 已 completed，run 24h 后才被 reaper 回收）
- 叠加历史「心跳重开 finished run」bug（**已修**：`runs.py` 心跳对 finished run 返回 409，`test_tc_s5` 覆盖）
- 长 run 全在 09-28~10-05（修复前）；10-06 后无

---

## 3. 修复（本轮）

**`mio_taskhub/background.py::_mark_stale_agents`**：**有活跃 run（CLAIMED/RUNNING）的
agent 不置 OFFLINE**——有活跃 run 即证明存活。否则其 run 会因 `agent_offline=True` 走
120s 紧窗口被提前回收。

> 效果：活跃 agent 保持 ONLINE → 其 run 用 360s 默认窗口（而非 120s）→ 不再 p50=127s 被误回收。
> 权衡：真死 agent 的 run 清理从 120s 延到 360s（仍有界）。

**测试**：`test_active_run_agent_not_marked_offline`（有活跃 run 不置 OFFLINE）。

**`mio_taskhub/api/task_stages.py::retry_task`**：**手动 retry 重置 `bounce_count=0`**——
手动重试 = 全新尝试，清空 never_started bounce 累积（否则 retry 后首个 never_started
立即再触发 FAILED，retry 失去意义）。

**测试**：`test_retry_resets_bounce_count` / `test_retry_from_failed_reopens`。

**`mio_taskhub/api/task_stages.py::_commit_task_and_events`**：任务进入终态
（completed/failed/cancelled）时**关闭其活跃 run**（`_close_active_runs_if_terminal`）——
保证「task 终态 ⇒ 其 run 关闭」，杜绝 run 悬挂。

**测试**：`test_terminal_closes_run.py`（done / cancelled 各一）。

**`mio_taskhub/api/runs.py::heartbeat`**：**run 心跳 = agent 存活证据 → 同步刷新 agent
心跳并置 ONLINE**（P4）。否则 agent 因未单独上报 agent 心跳被置 OFFLINE，其 run 走
120s 紧窗口被提前回收。

**测试**：`test_run_heartbeat_refreshes_agent.py`。

---

## 4. 遗留

1. **`never_started` 语义细化**（P2）：区分「进程未起」vs「起了未报进度」（需 agent 侧启动埋点，暂缺）。
2. **agent 侧心跳纪律**（P4 已由 run 心跳同步刷新缓解）：agent 仍应周期性 `taskhub_agent_heartbeat`。
