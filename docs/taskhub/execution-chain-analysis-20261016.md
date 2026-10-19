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

### P7 · HighHttpErrorRate 把 4xx 计入错误（F10 空转告警）

- `alerts.py::_check_http` 用 `sum(error_count.values())`（**4xx + 5xx**）算错误率
- 实测：**14006 请求中 11139 全是 4xx、0 个 5xx** → 告警报 **79.5%**（纯误报）
- 4xx（404/422/409）是正常客户端操作，不应触发「服务端错误率」告警

### P8 · night_runner spawn 失败 / 子进程 URL 缺 /api/v1（F9）

- `night_runner.json` 的 `idle-worker-opencode.cwd` 指向**不存在的** `dist\mio-taskhub`
  （实际 `dist\mio-taskhub-v3`）→ 每天 00:00 spawn precheck 失败「脚本不存在」
- `night_runner._spawn` 把 `MIO_TASKHUB_URL` 设为**裸 hub base**（无 `/api/v1`）传给子进程
  → idle_worker claim 到 `/tasks/claim` → **405**

### P9 · 停滞检测误伤「有心跳的长任务」

- `remediation.py::evaluate_stalled_tasks` 用 `last_transition_at` 判停滞（>30min）
- 但长任务（>30min）**不迁移状态属正常**，其 run 在正常心跳 → 被误判停滞并 `requeue_task`
- `requeue_task` 只把 task 置 QUEUED，**不关活跃 run**（同 P6 类问题）

### P10 · 终态时间戳被阶段迁移反复覆盖

- `apply_transition` **每次** `to_state==COMPLETED` 都重写 `task.completed_at`
- `COMPLETED→REVIEW→DONE` 的阶段迁移会覆盖完成时间 → 投影回放与实测差 1s（`test_projection` CI flaky）
- 同 `failed_at`/`cancelled_at`（应只在**首次**进入终态时设）

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

**`mio_taskhub/background.py::_on_timeout`**（P2 观测细化）：`reaper_decision` 记录**真实
`run_state_before`**（claimed=从未心跳 vs running=曾心跳）+ `run_heartbeated` 标志——
供事后区分「进程未起」与「起了未报进度」。**语义仍以 `progress==0` 为兼容基准**（不改行为）。

**测试**：`test_reaper_state_before.py`。

**`mio_taskhub/observability/alerts.py::_check_http` + `report.py::_collect_http`**（F10）：
错误率**只计服务端错误（5xx）**——4xx 是正常客户端错误，不计入。消除 HighHttpErrorRate 空转告警。

**测试**：`test_http_error_alert.py`（4xx 不触发 / 5xx 触发 / 无错误不触发）。

**`mio_taskhub/scheduling/night_runner.py::_spawn`**（F9）：子进程 `MIO_TASKHUB_URL` 补
`/api/v1` 前缀（`{url}` 占位仍用裸 hub base）；`~/.mio_taskhub/night_runner.json` 的
`idle-worker-opencode.cwd` 修正为 `dist\mio-taskhub-v3`。

**测试**：`test_night_runner.py::test_spawn_passes_api_prefixed_url`。

**`mio_taskhub/observability/remediation.py::evaluate_stalled_tasks`**（P9）：停滞判定排除
**有近期 run 心跳**的任务（长任务不迁移状态属正常）——避免误排丢在途工作。

**测试**：`test_remediation.py::test_task_with_fresh_run_heartbeat_not_stalled`。

**`mio_taskhub/workflow/transitions.py::apply_transition`**（P10）：终态时间戳
（`completed_at`/`failed_at`/`cancelled_at`）**只在首次进入终态时设**（同 `claimed_at`），
不再被后续阶段迁移覆盖。

**测试**：`test_projection.py::test_completed_at_not_overwritten_by_stage_change`。

---

## 4. 遗留

1. **`never_started` 语义彻底细化**（P2）：本轮回填观测字段；若要按 `run_state_before`
   改变归桶/requeue 语义，需评估对失败率的影响（暂保守不改）。
2. **agent 侧心跳纪律**（P4 已由 run 心跳同步刷新缓解）。
