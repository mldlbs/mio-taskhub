# 编排引擎 + 事件订阅 + Idea 拆解

日期：2026-08-15
状态：已确认（brainstorming 结论）

## 背景

P0（需求工作台 + 浮动面板 + harness 接入）已交付。本 spec 落地三个关联方向：

1. **P1 编排（DAG）**：`depends_on` 支持任务列表，依赖满足自动放行 READY，调度器扩展，前端 FlowView 呈现依赖。
2. **事件订阅推送**：统一事件日志表（自增 seq），MCP `taskhub_poll_events(seq)` 增量订阅，WS 实时广播保留。
3. **Idea → Task 一键拆解**：想法成形后批量生成任务集并与 idea 关联。

三者以调度器与事件为共同核心，故合并为一个 spec。

## 方案总览

```
写操作(create/claim/heartbeat/result/stage/idea/discussion)
        │  emit_event(type, entity, entity_id, payload)
        ▼
┌──────────────────────────────┐
│ Event 表（自增 seq 日志）      │  ← GET /api/v1/events?after_seq=N
└──────────┬───────────────────┘
           │  WS 广播（现有保留）
           ▼
    MCP taskhub_poll_events(seq)  →  agent 增量轮询
    UI / 浮动面板（WS 实时刷新）

调度器 tick（每 30s）:
  ① run_at 到期检查（现有）
  ② 依赖放行：depends_on 全部完成 → stage 置 READY + emit + 广播
```

## DAG 编排

### 数据模型

`Task.depends_on` 从 `Optional[str]` 升级为 JSON 数组：

```python
depends_on: list = Field(default_factory=list, sa_column=Column(JSON))
```

- 空列表 = 无依赖（等同旧 `None`）。
- 读取侧统一走 helper `_deps(t) -> list`，兼容旧值（字符串 / None / 列表）。
- **DB 迁移**（`db.py::_migrate_stage_column` 扩展）：
  - 已有 `depends_on` 列若存了非空单值字符串，改写为 `["旧值"]`（JSON 文本）。
  - 旧库无 JSON 列的 SQLite 无需改列类型（JSON 存 TEXT）。

### 依赖完成判据

前置任务满足以下任一即视为「已完成」：

- `state == completed`
- `stage == done`

任务自身不在活跃看板（`cancelled` / `done` / `completed`）时，不参与放行判断。

### 自动放行（调度器内）

`wiring._get_due_tasks` 保持原职责；**新增 `wiring._release_dependencies(db)`**，在调度器 `tick` 内调用：

1. 查询所有 `state` 非 `cancelled/done` 且 `stage` 在 `[brainstorming, design, planning]` 且 `depends_on` 非空的任务。
2. 逐任务检查前置任务是否全部「已完成」。
3. 全部满足 → `stage = READY`，写 `emit_event("task", id, {reason: "deps_met"})`，广播 `task_update`。
4. 有依赖但长时间未满足且前置中存在 `cancelled` / `failed`（不可能放行）的任务 → 在 `board.summary.alerts` 里报「依赖阻塞」告警，并建议处理。

放行不校验 `spec_path`/`plan_path`（与手动推进解耦，编排场景允许前置产物由前序任务负责）。

### 循环依赖校验

- `create_task` / `update_task`（改 `depends_on` 时）：对 task 全图做可达性检测，若新依赖形成环 → 400/422 `"cyclic dependency"`。
- 校验逻辑抽为纯函数 `planner.detect_cycle(deps: dict[str, list[str]]) -> list[str]`（返回环路径或空）。
- 调度器放行不阻止已存在的历史环（仅告警），避免破坏既有数据。

### 适配点

- `planner._topological_sort`：`depends_on` 从单值改列表，`children`/`in_degree` 按列表累计；缺失依赖（id 不在任务集）忽略不报错。
- `tasks.create_task` / `tasks.update_task`：`depends_on` 写入归一化为 list；`update_task` 允许改依赖并做环检测。
- `tasks.claim_task`：仅领取 `stage == READY`（放行已由调度器完成，无需 claim 时再判断）。
- `board.board_summary`：`ready_queue` 条件不变（ready + queued），放行后自然入队。
- `tasks.list_tasks`：返回字段新增 `depends_on`（数组）与 `idea_id`，供 FlowView 渲染依赖角标，避免前端逐任务拉详情。

## 事件订阅

### 数据模型

`Event` 表扩展（保留 `run_id`/`type`/`payload`/`at`，新增两个索引字段）：

```python
class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)   # 自增 seq
    type: str                                                    # task/run/idea/discussion/heartbeat/...
    entity: str = Field(default="", index=True)                  # task | run | idea | discussion
    entity_id: str = Field(default="", index=True)
    run_id: str = Field(default="", index=True)                  # 兼容旧字段
    payload: Optional[str] = None                                # JSON 字符串
    at: datetime = Field(default_factory=_now)
```

- `id` 即 seq，单调递增。
- **DB 迁移**：`event` 表新增 `entity`/`entity_id` 列；旧行按 `run_id` 回填 `entity="run", entity_id=run_id`；`run_id` 允许空。

### 统一 emit

新模块 `mio_taskhub/events.py`：

```python
def emit_event(db: Session, type: str, entity: str = "", entity_id: str = "",
               run_id: str = "", payload: dict | None = None) -> int
```

- 写入 Event 行（`payload` 序列化为 JSON 字符串），**与业务写操作同事务提交**（调用方先 `db.add(Event)` 再随业务 `db.commit()`，避免事件与状态不一致）。事务提交后由调用方触发现有 WS 广播（`task_update`/`idea_update`/`discussion_update`，按 entity 映射）。
- 在 `main.py` 里作为 WS 广播的单一入口，替换各 `api/*.py` 里的 `_broadcast_*` 内联实现。
- 调用约定：`emit_event(db, type=..., entity=..., entity_id=..., payload=...)` 只负责 `db.add`，不单独 commit；各 API 在业务 commit 后统一 `broadcast_for_event(event)`。
- **全量埋点**：create / claim / heartbeat / result / stage / subtask / gitref / idea / discussion 等所有写操作统一调用。

### 查询接口

`GET /api/v1/events?after_seq=N&limit=200`

- `after_seq` 缺省返回最近 `limit` 条（默认 200）。
- 返回 `{events: [...], next_seq}`；`events` 按 `seq` 升序；空库 `events=[], next_seq=0`。
- `next_seq` = 返回的最后一条 `seq`，调用方下次以其为 `after_seq`。

### MCP 工具

新增 `taskhub_poll_events`：

```
参数: seq: int = 0    (上次消费的 seq，0 表示从头/最近)
返回: {seq, next_seq, events: [{seq, type, entity, entity_id, payload}]}
```

- 描述强调「增量订阅全局变更：建任务、领取、心跳、完成、阶段推进、想法、讨论都会产生事件；记录 last_seq 后轮询即可拿到增量」。
- 心跳事件量大：agent 调用时按需忽略 `type == "heartbeat"`（在工具返回中保留，由 agent 过滤）。

## Idea → Task 拆解

### 数据模型

`Task` 新增列：

```python
idea_id: str = Field(default="", index=True)
```

**DB 迁移**：`task` 表新增 `idea_id VARCHAR NOT NULL DEFAULT ''`。

### 拆解接口

`POST /api/v1/ideas/{idea_id}/breakdown`

```json
{
  "tasks": [
    {
      "title": "写 spec", "description": "...",
      "target_agent_type": "mio-taskhub", "priority": 2,
      "est_duration_min": 30, "stage": "brainstorming",
      "ref": "t1", "depends_on": []
    },
    {
      "title": "写 plan", "ref": "t2", "depends_on": ["t1"]
    }
  ]
}
```

- 每个子任务可带临时 `ref`（如 `"t1"`），`depends_on` 可引用 ref 或真实 task id。
- 后端流程：
  1. 校验 idea 存在；若 `status == broken_down` → 409 `"already broken down"`。
  2. 先创建全部任务（`stage` 缺省 `brainstorming`，`idea_id` 回填），收集 `ref → real_id` 映射。
  3. 再解析每个任务 `depends_on`：ref 映射成真实 id；引用不存在的 ref/id → 422。
  4. 全图环检测（复用 `planner.detect_cycle`）→ 成环 422。
  5. idea `status = broken_down`，`updated_at` 刷新。
  6. `emit_event("idea", idea_id, {action: "broken_down", task_ids: [...]})` + 广播；每个任务 emit `task` 事件。
- 返回：`{idea: {...}, tasks: [{id, title, ref, depends_on}]}`。

### 查询联动

- `GET /api/v1/ideas/{idea_id}` 返回新增 `tasks: [{id, title, stage, state}]`。
- `GET /api/v1/tasks/{task_id}` 返回新增 `idea_id` 字段。
- `board.summary.counts` 不变。

## 前端

### FlowView 依赖标记

- 卡片 meta 区新增依赖角标：`⛓ N`（N = 依赖数量），`⛓ 0` 不显示。
- 状态色：前置全部已完成 → 绿 `ok`；有前置未完成 → 灰 `dim`；存在前置 `cancelled`/`failed`（阻塞）→ 红 `danger`。
- 卡片标题行 hover 显示依赖任务标题 tooltip（title 属性，简单实现）。
- `TaskDetail`（详情抽屉）新增「依赖」区块：列出前置任务标题 + 状态；无依赖显示「无前置任务」。

### IdeasView 拆解入口

- idea 详情页状态为 `formed` 时显示「拆解为任务」按钮。
- 点击打开拆解表单（复用轻量 modal 或 inline）：每行 `{ref, title, 依赖refs(逗号分隔)}`，可增删行。
- 提交 `POST /ideas/{id}/breakdown`，成功后刷新详情（显示关联任务列表）并置状态 broken_down。

### EmbedView / 浮动面板

不做改动（FlowView 扩展自动生效）。

## 错误处理

| 场景 | 行为 |
|---|---|
| 依赖成环（创建/更新/拆解） | 400/422 `"cyclic dependency: a → b → a"` |
| 拆解引用不存在的 ref/id | 422 `"unknown dependency ref: xxx"` |
| 拆解已 broken_down | 409 `"already broken down"` |
| idea 不存在 | 404 |
| 依赖阻塞（前置 cancelled/failed） | board.summary.alerts 告警 + FlowView 红标 |

## 测试

- `tests/test_dag.py`：
  - 多依赖全满足放行（调度器 tick 后 stage→ready）
  - 部分满足不放行
  - 前置 cancelled → 告警 + 不放行
  - 循环依赖检测（create/update 422；`detect_cycle` 纯函数）
  - 旧单值 `depends_on` 迁移为数组（`_deps` 归一化）
- `tests/test_events.py`：
  - seq 自增、`after_seq` 增量、缺省最近 200 条、空库零值
  - 各写操作产生对应事件（create/claim/heartbeat/result/stage/idea/discussion）
  - WS 广播仍工作
- `tests/test_breakdown.py`：
  - ref 依赖解析成真实 id
  - 幂等 409、未知 ref 422、成环 422
  - idea 状态 broken_down + 详情返回 tasks
  - task 详情返回 idea_id
- `tests/test_mcp_server.py` 扩展：`taskhub_poll_events`、`taskhub_breakdown_idea`（mock HTTP）
- 前端：`cd web && npm run build`；浏览器验证 FlowView 依赖角标 + IdeasView 拆解表单
- 回归：现有 140 测试全绿

## 不做的事（YAGNI）

- 不做 AND+OR 混合依赖表达式（全 AND 已确认）。
- 不做事件表清理策略（单用户本地，表增长可接受；预留后续）。
- 不做跨 agent 并发领取调度（保持「一 agent 一 run」约束）。
- 不做 FlowView 依赖图连线渲染（角标 + 详情列表已覆盖需求）。
- 不做 hermes 专有订阅机制（沿用 MCP 工具）。
