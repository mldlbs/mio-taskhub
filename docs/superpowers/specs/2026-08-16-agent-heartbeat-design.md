# Agent 心跳与超时离线

日期：2026-08-16
状态：已确认（brainstorming 结论）

## 背景

`Agent` 表已有 `status`/`last_heartbeat` 字段，但**无心跳机制**——register 即 ONLINE，永不回落。这对已交付的「空闲 agent 自动捞取」是致命缺陷：agent 退出后仍显示 ONLINE，调度器 `_assign_to_idle_agents` 会持续给它分配任务，任务卡死无人执行。

本 spec 引入：agent 主动心跳 + hub 超时自动离线。

## 机制

```
agent 侧                                    hub 侧
─────────────────                          ────────────────────────────
taskhub_register         ──►  status=ONLINE, last_heartbeat=now
(启动时)

taskhub_agent_heartbeat  ──►  POST /agents/heartbeat
(空闲时周期调，默认60s)       last_heartbeat=now, status=ONLINE

调度器 tick（每 30s）:
  ① _mark_stale_agents():
     last_heartbeat 超过 180s → status=OFFLINE
     （只改 status，不动 run）
  ② 现有 _release_dependencies / _assign_to_idle_agents
     （只分配 status=ONLINE 的 agent）
```

## 后端改动

### 1. `POST /api/v1/agents/heartbeat`（agents.py）

```
body: { "name": "opencode" }
```

- **upsert 自动注册**：name 不存在则自动创建（agent 崩溃重启后直接 heartbeat，无需重新 register）；存在则刷新。
- 幂等：刷新 `last_heartbeat=now`、`status=ONLINE`。
- **不写 `agent_heartbeat` 事件**（避免心跳事件风暴淹没事件表——事件表只保留 `agent_registered`/`agent_offline`/`task_assigned` 等业务事件）。心跳只更新数据库。
- 返回 `{name, status: "online", last_heartbeat}`。

### 2. 调度器超时离线（wiring.py）

新增 `_mark_stale_agents()` 与统一的 `_scheduler_tick()` 外层封装：

```python
def _scheduler_tick():
    _mark_stale_agents()          # ① agent 生命周期
    _release_dependencies()       # ② 依赖放行
    _assign_to_idle_agents()      # ③ 空闲分配

def _get_due_tasks():
    _scheduler_tick()             # 调度器 tick 统一入口
    now = datetime.now(timezone.utc)
    # ...（run_at 到期检查原逻辑不变）...
```

`AGENT_TIMEOUT_SECONDS = 180`。

`_mark_stale_agents()` **数据库过滤扫描**（不拉全表 Python 遍历）：

```
cutoff = now - timedelta(seconds=AGENT_TIMEOUT_SECONDS)
stale = select(Agent).where(
    Agent.status != AgentStatus.OFFLINE,
    Agent.last_heartbeat < cutoff,
)
```

- 对每个 stale agent：`status=OFFLINE`，写 `agent_offline` 事件（entity="agent", entity_id=name, payload={reason:"heartbeat_timeout"}}）+ 广播。
- **只改 agent status，不动任何 Run**——但见下方「run 僵尸防护」：`_on_timeout` 保证 agent 离线的 run 也被及时回收。

### 2b. run 僵尸防护（关键）

`AGENT_TIMEOUT_SECONDS(180)` 默认 > `DEFAULT_TIMEOUT_SECONDS(120)`，默认配置下 run 先超时。但 `task.timeout_min` 可被设成更大值，导致 agent 已 OFFLINE 而 run 仍锁死。

**加固 `_on_timeout`**：当 run 的 agent 已 OFFLINE（或 run 自身心跳超时）时，直接结束该 run 并重领：

```
_on_timeout(run_id, task_id):
  run = get(run_id)
  agent = get_agent(run.agent_name)
  if run 在 claimed/running:
      if agent.status == OFFLINE 或 run 心跳超时:
          run.state = FINISHED (result="heartbeat timeout")
          task → queued/ready 或 failed（按 attempt/max_retries）
```

即：**agent OFFLINE 是 run 超时的充分条件**——不依赖 run timeout 配置，行为上保证 agent 离线必回收其 run。

## MCP / wrapper / 引导

### 3. MCP `taskhub_agent_heartbeat`（mcp_server.py）

```
参数: name: str（agent 名称）
```

- 调 `POST /agents/heartbeat`。
- instructions 补一句：「agent 空闲时周期性调用 taskhub_agent_heartbeat 保持在线，否则超时会被标记离线；未注册时调用会自动注册」。

### 4. agent_wrapper.py

加动作 `heartbeat-agent`：
```python
elif action == "heartbeat-agent":
    r = req("POST", "/agents/heartbeat", {"name": agent})
    print(f"Heartbeat: status={r['status']} last_heartbeat={r['last_heartbeat']}")
```

### 5. 引导（setup-agent.ps1）

任务执行规范里补：
```
- 空闲时每隔约 1 分钟调用 taskhub_agent_heartbeat 保持在线；长期离线会被自动标记离线
```

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| agent 心跳间隔 | 60s | agent 侧建议频率 |
| AGENT_TIMEOUT_SECONDS | 180s | 超 180s 未心跳 → OFFLINE |
| DEFAULT_TIMEOUT_SECONDS | 120s | run 心跳超时（现有） |

**时序约束**：`AGENT_TIMEOUT_SECONDS(180) > DEFAULT_TIMEOUT_SECONDS(120)`，默认下 run 先超时。即使 `task.timeout_min` 被设大，`_on_timeout` 的「agent OFFLINE 即回收 run」保证 agent 离线必回收其 run，不依赖配置。

## 测试

`tests/test_agent_heartbeat.py`：
- heartbeat 幂等刷新 last_heartbeat + ONLINE
- heartbeat 自动注册（未注册 name → 创建 agent）
- 超时未心跳 → OFFLINE（把 last_heartbeat 改为过去时间 → `_mark_stale_agents`）
- 未超时 → 保持 ONLINE
- 离线 agent 不再被 `_assign_to_idle_agents` 分配
- **agent OFFLINE 后其 run 被 `_on_timeout` 回收**（即使 task.timeout_min 很大）
- `agent_offline` 事件写入；**heartbeat 不产生事件**（事件表无 agent_heartbeat）
- `_scheduler_tick` 串起三步（stale → release → assign）

## 不做的事（YAGNI）

- 不做 agent BUSY/IDLE 细分（ONLINE/OFFLINE 已够）。
- 不做 agent 主动取消注册接口（离线由超时自动完成）。
- 不做 `agent_heartbeat` 事件（心跳只更新 DB，防事件风暴）。
- 不做 STALE 中间态（评审建议的优化项，暂不实现，保留 ONLINE→OFFLINE 直接跳转）。
