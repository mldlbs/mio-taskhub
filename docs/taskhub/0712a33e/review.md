# P1-2 心跳失败根因定位报告

| 项 | 值 |
|---|---|
| 任务 | 0712a33e（branch task-0712a33e，run c7c794bc） |
| 来源 | docs/taskhub/system-assessment-20260930.md P1-2 |
| 日期 | 2026-09-30 |
| 数据 | 生产库 taskhub.db（8/12–9/30） |
| 方法 | 先定位（只读数据 + 代码审计），产出真/假判定，再决定修复 |

## 1. 现象（事实）

- 119 次 run 中 52 次 exit_code≠0，其中 **24 次 heartbeat_timeout**。
- 24 次超时的 `finished_at - started_at` 高度集中于 **120.1–129.7s**（含 1748s / 3873s 两个长尾）。
- 受影响任务 `est_duration_min` = 30/90/120/180，但回收窗口未按 est 自适应。

## 2. 代码审计：超时窗口如何决定（事实）

`mio_taskhub/heartbeat.py`：

```python
DEFAULT_TIMEOUT_SECONDS = 300            # 任务未配 timeout_min 时基线
AGENT_OFFLINE_TIMEOUT_SECONDS = 120      # agent OFFLINE 时的回收上限

def effective_timeout(self, run):
    timeout = run.timeout_seconds or self.timeout   # 300s
    if run.agent_offline:
        timeout = min(timeout, self.agent_offline_timeout)  # ← 收窄到 120s
    return timeout
```

`mio_taskhub/background.py::_get_runs`：

```python
timeout_sec = (task.timeout_min * 60) if task and task.timeout_min else DEFAULT_TIMEOUT_SECONDS
agent_offline = agent is None or agent.status == AgentStatus.OFFLINE
```

`_mark_stale_agents`：agent 超过 `AGENT_TIMEOUT_SECONDS=180` 无心跳 → 标 OFFLINE。

**推断链**：agent OFFLINE → `agent_offline=True` → `effective_timeout` 被收窄到 **120s** → 扫描周期 10s → 观测到 **120.1s 硬簇**。与数据完全吻合。

## 3. 关键判别依据：`progress`

| progress | 次数 | 时长 | 含义 |
|---|---|---|---|
| **0** | 24 | 120–130s（+1748s/3873s 长尾） | **从未上报心跳**（process 未启动/已死） |
| 100 | 4 | — | 非失败：这些 run 的 task 最终 COMPLETED（是 `LIKE '%heartbeat%'` 的统计噪声） |

24 次超时**全部 progress=0** —— 即 `last_heartbeat == started_at`，**agent 一次心跳都没发**。

## 4. 真 / 假失败判定（附证据）

| 类别 | 数量 | 证据 | 判定 |
|---|---|---|---|
| **从未启动**（120s 簇） | ~20 | progress=0；`last_heartbeat==started_at`；agent OFFLINE 或不存在；无 heartbeat 事件 | **归类为"真失败"**：agent claim 后从未工作。**但不是"误判"**——agent 确实没在干活。缺陷在于**未被干净 requeue**，而是消耗 attempt 直到超限 FAILED |
| **长尾**（1748s/3873s） | 2 | progress=0；时长超 120s 说明当时 agent online（窗口 300s） | **真失败**：agent 真死，窗口也无误 |
| **工作后死亡** | 0（在 24 内） | — | 无样本 |

**结论**：
- **不存在"agent 正在工作却被误判超时"的证据** —— 前一轮"心跳误判"推断**被数据否定**（正确地说：不是误判，是"从未启动"被当成失败）。
- **真缺陷有二**：
  1. **窗口机制缺陷**：`agent_offline` 时无条件收窄到 120s，**忽略 `est_duration_min`**。一个 `est=90min` 的长任务，若 agent 短暂掉线，会在 120s 被杀——这是 P1 风险（当前生产未观察到该组合，但机制成立）。
  2. **生命周期缺陷**：claim 后从未启动（progress=0）与真实失败**未加区分**，一律走"记失败 + attempt+1"，导致本可 requeue 的任务被消耗到 FAILED。

## 5. 修复方向（按结论，非预设）

- **4a 窗口自适应（保留）**：`effective_timeout` 不再无条件收窄到 120s；改为 `max(基线, est_duration_min*60 的合理倍数)`，并对 agent_offline 用"该任务自己的 est 窗口"而非全局 120s。
- **4b 生命周期区分（新增，优先级更高）**：超时时若 `progress==0`（从未心跳）→ 判为 `never_started`，**直接干净 requeue 且不消耗 attempt**（或消耗极少），与"工作后失败"区分。
- **5 Verify 闭链**：requeue 后校验 run 真的重新进 CLAIMED/RUNNING，形成 Detect→Classify→Recover→Verify。
- **6 测试**：长任务不被 120s 误杀；never_started 干净 requeue；真超时仍准时回收；verify 逻辑。

## 6. 证据档位声明

- **事实**：24 次超时 progress=0；120.1s 硬簇；`effective_timeout` 收窄逻辑；agent 全 OFFLINE。
- **推断**：120s 簇由 `agent_offline→120s` 收窄 + 10s 扫描产生（机制与数据吻合，无逐条日志，故为推断）。
- **不成立（被数据否定）**："agent 在工作却被误判超时"——无 progress>0 的超时样本。
