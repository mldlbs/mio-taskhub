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

### 分配逻辑（抽纯函数）

现有 `api/tasks.py::claim_task` 的核心逻辑（查 agent 已有 run → 查匹配任务 → 建 run → 改状态）抽为 `_claim_for(agent, agent_type, db)`，返回 `Run` 或 `None`：

- 该 agent 已有 claimed/running run → 返回它（幂等）
- 无匹配任务 → 返回 `None`
- 成功 → 建 run、任务 state→claimed / stage→implementing、返回 run

调度器与 claim API 都调它。

### 调度器新增 `wiring._assign_to_idle_agents(db)`

在 `_release_dependencies()` 之后调用：

1. 查所有 `status=online` 的 Agent。
2. 对每个 agent：若无 claimed/running run（即空闲）→ 调 `_claim_for(agent, agent_type, db)` 找任务。
3. 分配到任务 → 写事件 `task_assigned`（entity=task, payload={agent, reason:"idle_assign"}}）+ 广播。

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
- 分配写事件 `task_assigned`
- claim 幂等：agent 调 claim 返回已分配的 run
- 无 ready 任务时无分配

## B. 编排视图增强（前端）

### B1. 依赖连线（FlowView）

- **范围**：只在展开的单个 stage 面板内画线（该 stage 内任务间依赖）；跨 stage 依赖用现有角标提示（不做跨列连线，避免杂乱）。
- **实现**：面板内加 SVG overlay，计算面板内卡片位置与依赖关系，绘制箭头线。
- **交互**：hover 卡片 → 高亮其上下游连线（加 class）；hover 结束恢复。
- **数据**：复用 `tasks`（list 含 `depends_on`）。

### B2. 拖拽换阶段

- FlowView 卡片可拖拽到任意 stage 列。
- **drop 语义**：调用现有 `advance_stage`（`api.advanceStage(id, {target_stage})`），保留产出物校验（design 需 spec_path 等）与回溯限制（TaskStage.can_advance）。
- 非法目标 → 后端 400/422，前端 alert 提示，卡片弹回原位。
- **实现**：HTML5 drag&drop（draggable 卡片 + 每列 drop 区域），复用 BoardView 既有拖拽样式模式。

### B3. 独立拓扑视图（新组件 TopoView）

- **新视图**：整条依赖链的拓扑布局（DAG 分层）。
- **后端**：复用 `/api/v1/tasks`（list 已含 depends_on），前端做布局。
- **布局**：Kahn 拓扑分层（前端实现，或复用后端 planner 概念）；每层一行，节点 = 任务卡片（状态色：done 绿 / blocked 红 / ready 中性），边 = 依赖箭头。
- **交互**：点击节点打开详情抽屉；hover 高亮上下游。
- **入口**：Rail 加「拓扑」项（`view === 'topo'`）。

### 测试与验证

- 前端：`npm run build` 通过；浏览器验证三种交互（连线、拖拽、拓扑视图）。
- 后端：190 全绿（无后端改动，仅前端）。
- 打包：不重打（功能改动，widget/hub EXE 已交付）。

## 错误处理

| 场景 | 行为 |
|---|---|
| 拖拽到非法阶段 | 后端 422 → alert 提示 + 卡片弹回原位 |
| 拖拽到 design/planning 缺产出物 | 后端 422 → 提示填产出物 |
| 调度分配时任务已被领取 | 不分配（跳过该任务，下次 tick 再试） |
| agent 离线 | 不分配（只分配 online） |

## 不做的事（YAGNI）

- 不做 Agent 心跳/离线自动标记。
- 不做跨列依赖连线。
- 不做前端拓扑计算后端化（前端直接算，数据量小）。
- 不做多 agent 抢占负载均衡（一 agent 一 run 已保证）。
