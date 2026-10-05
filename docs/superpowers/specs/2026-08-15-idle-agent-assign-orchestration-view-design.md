# 空闲 agent 自动捞取 + 编排视图增强

日期：2026-08-15
状态：已确认（brainstorming 结论）

## 背景

编排引擎（DAG 依赖放行/环检测）、事件订阅、Idea 拆解、绿色版 widget 均已交付。本 spec 落地两个增强方向：

1. **空闲 agent 自动捞取**：目前任务是拉模型——agent 必须自己调 `taskhub_claim` 才领活。目标：hub 主动把任务分配给空闲 agent。
2. **编排视图增强**：FlowView 目前只有阶段列 + 推进按钮，无依赖连线/拖拽/拓扑视图。

二者独立，共用现有 `depends_on`/`stage`/`Agent` 机制。

## A. 空闲 agent 自动捞取（后端）

### 机制：hub 主动分配 + agent 后台轮询领取

```
调度器 tick（每 30s，现有 _release_dependencies 之后）:
  ┌─────────────────────────────────────────┐
  │ ① 找空闲 agent：status=online 且无       │
  │    claimed/running 的 run                │
  │ ② 对每个空闲 agent，按优先级+FIFO 找     │
  │    匹配其 agent_type 的 ready 待领任务    │
  │ ③ 分配：state→claimed, stage→implementing│
  │    Agent→RUNNING(占位 run), 写事件+广播   │
  │    （复用 claim 逻辑，抽成纯函数）        │
  └─────────────────────────────────────────┘

agent 侧（MCP taskhub_claim 增强）:
  若 agent 有"被分配"的 run → 返回该 run（幂等，现已有）
  否则 → 正常排队领取
```

### 分配逻辑（抽纯函数 + 原子领取）

现有 `api/tasks.py::claim_task` 的核心逻辑抽为 `_claim_for(agent, db)`（**不传 agent_type**——`agent.agent_type` 从 Agent 对象取，避免参数与属性不一致），返回 `Run` 或 `None`：

- 该 agent 已有 claimed/running run → 返回它（幂等）
- 无匹配任务 → 返回 `None`
- 成功 → 建 run、任务 state→claimed / stage→implementing、返回 run

调度器与 claim API 都调它。

**原子领取（SQLite 适配）**：SQLite 不支持 `SELECT ... FOR UPDATE`（`with_for_update()` 被静默忽略），依赖数据库级单写者锁 + 条件更新保证一个 task 只能被创建一个 run：

1. 查该 agent 已有 run（幂等检查）。
2. 查候选任务（ready + run_at 到期 + agent_type 匹配）。
3. 用**条件更新**抢占：`UPDATE task SET state='claimed' WHERE id=:id AND state='queued'`，`rowcount==1` 才算抢到（否则跳过，说明已被并发领取）。
4. 抢占成功 → 同事务内建 Run、改 stage/attempt、emit `task_claimed`（含 run_id）→ commit。

scheduler tick、claim API、未来 WS 触发三个入口全部走 `_claim_for`，共享同一条原子路径。

### 调度器新增 `wiring._assign_to_idle_agents(db)`

在 `_release_dependencies()` 之后调用，采用 **Task-first 调度**（避免 Agent-first 饿死后注册的 agent）：

1. 取所有 `ready` 待领任务，按 `priority desc, created_at asc` 排序（与 claim API 同序）。
2. 逐任务找匹配的空闲 agent：`status=online` 且无 claimed/running run，且 `agent.agent_type` 匹配任务 `target_agent_type`（`target_agent_type` 为空则任意在线 agent）。
3. 对匹配的空闲 agent 调 `_claim_for(agent, db)` 抢占（内部条件更新保证原子）。
4. 分配到任务 → 写事件 `task_assigned`（entity=task, **payload={agent, run_id, reason:"idle_assign"}**）+ 广播。
5. 一个 tick 内每个 agent 最多分一个任务（分完即忙）；任务分配完或无可匹配 agent 即停。

**不做的事（YAGNI）**：
- 不引入 Agent 心跳线程（register 即 online，本 spec 不改 Agent 离线判定）。
- 不做抢占/负载均衡（一 agent 一 run 约束已保证不会超配）。
- 不分配已非 ready 的任务（只从 ready 队列取）。

### Agent 表改动

无。`Agent.status` 已是 online/offline，`register` 已更新 last_heartbeat。分配只针对 online 且无 run 的 agent。

### MCP 增强

`taskhub_claim` 描述补充："hub 可能已为你分配任务，调用后优先返回已分配 run（幂等）"。工具签名不变。

### 测试

`tests/test_assign.py`：
- 空闲 agent 被分配（register online + ready 任务 → 调度分配后 task claimed、agent 有 run）
- 忙 agent 不分配（已有 running run → 跳过）
- agent_type 不匹配跳过
- 分配写事件 `task_assigned`（含 run_id）
- claim 幂等：agent 调 claim 返回已分配的 run
- 无 ready 任务时无分配
- **并发原子性**（本方案最重要）：
  - scheduler 与 claim API 并发抢同一 task → 只有一个 run 被创建（条件更新 rowcount 保证）
  - 两个 agent 同时竞争一个 task → 只有一个抢到，另一个跳过
  - 一个 task 在所有并发路径下永远只能创建一个 run（assert run 表无重复 task_id 且非唯一约束冲突）

## B. 编排视图增强（前端）

### B1. 依赖连线（FlowView）

- **范围**：只在展开的单个 stage 面板内画线（该 stage 内任务间依赖）；跨 stage 依赖用现有角标提示（不做跨列连线，避免杂乱）。
- **实现**：面板内加 SVG overlay，计算面板内卡片位置与依赖关系，绘制箭头线。
- **重绘时机**：不在 hover/mousemove 内算坐标。用 `ResizeObserver`（面板尺寸变化）+ `MutationObserver`（任务增删/卡片增减）+ `requestAnimationFrame` 节流，在下列时机重算 `getBoundingClientRect()` → 生成 `edges[]` → 重绘 SVG：卡片展开、窗口缩放、任务新增/删除、拖拽完成。
- **交互**：hover 卡片 → 高亮其上下游连线（加 class）；hover 结束恢复。
- **数据**：复用 `tasks`（list 含 `depends_on`）。

### B2. 拖拽换阶段

**新增后端 `move_to_stage`（与 `advance_stage` 分开）**：

按钮推进与拖拽是两种业务语义——`advance_stage` 走 `TaskStage.can_advance`（仅相邻推进 + 回溯表），而拖拽是**任意跳转**。新增：

```
POST /api/v1/tasks/{id}/stage/move
body: { "target_stage": "review" }
```

- 允许任意跳转（前推/跳过/回溯），**不校验相邻性**。
- 保留终态保护：`done`/`cancelled` 不可再 move（返回 400）。
- 保留产出物校验：move 到 `design` 需 `spec_path`、`planning` 需 `plan_path`、`done` 需 `review_result`（与 advance_stage 同规则）。
- move 时若目标 `stage` 使任务脱离活跃看板（`done`/`cancelled`），同步调整 `state`。
- 成功 → emit `task_moved`（entity=task, payload={from, to}）+ 广播。
- 返回更新后详情。

前端 FlowView 卡片可拖拽到任意 stage 列，drop 时调 `moveToStage(id, target)`；非法目标 → 后端 400/422 → alert 提示 + 卡片弹回原位。产出物缺失时提示补填。

- **实现**：HTML5 drag&drop（draggable 卡片 + 每列 drop 区域），复用 BoardView 既有拖拽样式模式。

### B3. 独立拓扑视图（新组件 TopoView）

- **新视图**：整条依赖链的拓扑布局（DAG 分层）。
- **后端**：复用 `/api/v1/tasks`（list 已含 depends_on），前端做布局。
- **布局**：Kahn 拓扑分层（前端实现，O(V+E) 足够 20~200 任务）；每层一行，节点 = 任务卡片（状态色：done 绿 / blocked 红 / ready 中性），边 = 依赖箭头。
- **节点元数据**：每个 TopoNode 附带 `depth`（拓扑层）、`indegree`、`outdegree`，支撑后续关键路径（CPM）分析、叶子/阻塞/并行度展示，无需重构。
- **交互**：点击节点打开详情抽屉；hover 高亮上下游。
- **入口**：Rail 加「拓扑」项（`view === 'topo'`）。

### 测试与验证

- 后端：`test_assign.py`（分配 + 并发原子性）+ `test_stage_move.py`（move_to_stage 任意跳转/终态保护/产出物校验/事件）+ 现有 190 全绿。
- 前端：`npm run build` 通过；浏览器验证三种交互（连线、拖拽、拓扑视图）。
- 打包：不重打（功能改动，widget/hub EXE 已交付）。

## 错误处理

| 场景 | 行为 |
|---|---|
| 拖拽到非法阶段（done/cancelled 之后） | 后端 400 → alert 提示 + 卡片弹回原位 |
| 拖拽到 design/planning/done 缺产出物 | 后端 422 → 提示填产出物 |
| 调度分配时任务已被并发领取 | 条件更新 rowcount=0 → 跳过该任务，下次 tick 再试 |
| 调度分配时 agent 已忙 | 跳过该 agent，任务留队列 |
| agent 离线 | 不分配（只分配 online） |

## 不做的事（YAGNI）

- 不做 Agent 心跳/离线自动标记。
- 不做跨列依赖连线。
- 不做前端拓扑计算后端化（前端直接算，数据量小）。
- 不做多 agent 抢占负载均衡（一 agent 一 run 已保证）。
- 不改 `advance_stage` 语义（按钮推进保持相邻+回溯）；`move_to_stage` 是新增独立接口，服务拖拽。
- 不做 SQLite 行级锁（SQLite 不支持 FOR UPDATE，用条件更新替代）。
