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

- 404 未注册的 name。
- 幂等：刷新 `last_heartbeat=now`、`status=ONLINE`。
- emit `agent_heartbeat` 事件（entity="agent", entity_id=name）+ 广播。
- 返回 `{name, status: "online", last_heartbeat}`。

### 2. 调度器超时离线（wiring.py）

新增 `_mark_stale_agents()`，在 `_get_due_tasks` 开头（`_release_dependencies` 之前）调用：

```
AGENT_TIMEOUT_SECONDS = 180
```

- 扫描所有 `status != OFFLINE` 的 Agent。
- `last_heartbeat` 超过 `AGENT_TIMEOUT_SECONDS`（或为 None 且注册超时）→ `status=OFFLINE`。
- 写 `agent_offline` 事件（entity="agent", entity_id=name, payload={reason:"heartbeat_timeout"}}）+ 广播。
- **只改 agent status，不动任何 Run**——agent 存活与 run 存活解耦（run 由现有 heartbeat timeout 时钟处理）。

## MCP / wrapper / 引导

### 3. MCP `taskhub_agent_heartbeat`（mcp_server.py）

```
参数: name: str（agent 名称）
```

- 调 `POST /agents/heartbeat`。
- instructions 补一句：「agent 空闲时周期性调用 taskhub_agent_heartbeat 保持在线，否则超时会被标记离线」。

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

## 测试

`tests/test_agent_heartbeat.py`：
- heartbeat 幂等刷新 last_heartbeat + ONLINE
- 超时未心跳 → OFFLINE（把 last_heartbeat 改为过去时间 → `_mark_stale_agents`）
- 未超时 → 保持 ONLINE
- 离线 agent 不再被 `_assign_to_idle_agents` 分配
- 离线不误伤正在跑的 run（run 状态不变）
- 事件 `agent_heartbeat` / `agent_offline` 写入
- heartbeat 404（未注册 name）

## 不做的事（YAGNI）

- 不做 agent BUSY/IDLE 细分（ONLINE/OFFLINE 已够）。
- 不做 agent 主动取消注册接口（离线由超时自动完成）。
- 不把 run 超时逻辑并入 agent 超时（职责分离，run 由现有 HeartbeatSweep 处理）。
