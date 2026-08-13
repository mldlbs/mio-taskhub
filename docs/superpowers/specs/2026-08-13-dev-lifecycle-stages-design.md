# 研发流程生命周期（superpowers 工作流可视化）

日期：2026-08-13
状态：已确认

## 背景

mio-taskhub 当前只覆盖了 superpowers 研发流程的「执行段」（queued→claimed→running→completed）。任务创建后立即进入可执行队列，跳过了需求理解、设计、计划、审查等阶段。用户希望在任务生命周期中可视化 superpowers 完整工作流：需求理解 → 设计 → 计划 → 可执行 → 执行 → 审查 → 完成。

经澄清：
- 生命周期采用**完整串行**模型，各阶段严格推进
- 需求理解/讨论发酵：由 **agent 领走做需求理解**（可拉独立会话讨论），**创建者确认**后进入设计
- 产出物**强制关联**：理解→讨论记录，设计→spec 文档，计划→plan 文档，审查→审查结论；状态推进必须有对应产出物
- Web UI **新增「研发流程」视图**，保留现有执行看板
- 状态模型：**stage 与 state 分离**（方案 B）——stage 管研发阶段推进轴，state 管执行子状态

## 方案

采用**方案 B（stage 分离）**：新增 `stage` 字段承载研发阶段推进，保留现有 `state` 承载执行子流程。现有 claim/heartbeat/result 执行逻辑不改，只在外层叠加 stage 流转。

## 数据模型（models.py）

### 新增 `TaskStage` 枚举

```python
class TaskStage(str, enum.Enum):
    BRAINSTORMING = "brainstorming"   # 需求理解/讨论发酵
    DESIGN        = "design"          # 写 spec 文档
    PLANNING      = "planning"        # 写 plan 文档
    READY         = "ready"           # 待执行（可被领取）
    IMPLEMENTING  = "implementing"    # 执行中（对应现有执行状态）
    REVIEW        = "review"          # 审查/验收
    DONE          = "done"            # 完成
    CANCELLED     = "cancelled"       # 取消
```

### Task 新增字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `stage` | TaskStage | 研发阶段，**默认 READY**（向后兼容旧任务）；create_task 显式设为 BRAINSTORMING |
| `spec_path` | str | 设计文档路径（强制产出物） |
| `plan_path` | str | 计划文档路径（强制产出物） |
| `review_result` | str | 审查结论（强制产出物） |

`Task.state` 保留现状，仅 IMPLEMENTING 阶段使用。

### stage 迁移规则（单向串行）

```
brainstorming → design （需有 discussion 记录）
design → planning       （需有 spec_path）
planning → ready        （需有 plan_path）
ready → implementing    （由 claim 触发，同时 state→claimed）
implementing → review   （由 submit_result 成功触发）
review → done           （需有 review_result）
任意阶段 → cancelled    （取消）
```

## API 扩展（api/tasks.py）

### 1. `POST /tasks/{id}/stage` — 推进研发阶段（核心新增）

Body: `{"target_stage": "design", "spec_path": "docs/superpowers/specs/xxx.md"}`

- 校验当前 stage → target_stage 是否合法迁移（对照迁移表）
- 校验产出物：
  - target=design 需传 spec_path
  - target=planning 需传 plan_path
  - target=done 需传 review_result
- 合法则更新 stage + 产出物字段，返回新 stage
- 非法迁移返回 400，缺产出物返回 422

### 2. 现有接口联动 stage

- `create_task`：新任务 stage 默认=BRAINSTORMING（不再直接可执行）；提供可选 body 参数 `stage`，创建者可显式指定（如 `"stage": "ready"` 跳过理解/设计阶段直接可执行，用于测试或明确任务）
- `claim_task`：只领取 `stage==READY` 的任务（在现有 run_at 过滤基础上加 stage 条件）
- `submit_result` 成功：任务 stage→REVIEW（state→completed 逻辑保留供执行视图）
- `get_task`/`_task_detail`：返回 `stage`/`spec_path`/`plan_path`/`review_result`

### 3. 辅助

- `GET /tasks?stage=xxx`：按研发阶段过滤（现有 state 过滤保留）
- 现有 `POST /tasks/{id}/discussions` 不变（承载理解/讨论记录，作为 brainstorming 产出物）

### 一致性约束

- `stage==IMPLEMENTING` 时 `state` 反映执行子状态（claimed/running/retrying）
- `stage==READY` 时 `state` 应为 queued
- 其他阶段 state 无意义（queued 占位）

## MCP 扩展（mcp_server.py）

- 新增 `taskhub_advance_stage`：推进阶段 + 传产出物（target_stage/spec_path/plan_path/review_result）
- `taskhub_create_task` / `taskhub_claim` / `taskhub_get_task` / `taskhub_submit_result`：响应或逻辑带上 stage 字段

## Web UI（新增「流程」视图）

- 导航新增「流程」入口，与看板/列表/夜间计划并列
- **流程泳道**：需求理解 / 设计 / 计划 / 待执行 / 执行中 / 审查 / 完成，每列一个 stage
- 卡片显示：标题/优先级/目标 agent/产出物标记（📄 spec、📝 plan 徽标）
- **拖拽推进**：卡片可拖到下一阶段，弹窗要求填产出物路径
- 详情抽屉增强：显示当前阶段 + 进度条、「推进阶段」按钮（选目标阶段+填产出物）、spec/plan 链接、review_result、讨论记录强化为「理解/讨论」
- 产出物标记：缺产出物的阶段列显示「待产出」提示
- **现有执行看板保留**：聚焦 IMPLEMENTING/READY 阶段任务

## 测试

- models：TaskStage 枚举、迁移表、新字段 CRUD
- API：stage 推进接口（合法/非法迁移/缺产出物 400/422）、claim 只领 READY、create 默认 brainstorming、get 返回 stage
- MCP：advance_stage 工具、claim 带 stage 过滤
- 集成：完整生命周期 e2e（create→brainstorm→design→planning→ready→claim→implement→review→done）

## 不做的事（YAGNI）

- 不做阶段并行/回退到任意阶段（严格单向串行，仅 cancelled 例外）
- 不做 spec/plan 文档内容渲染（只存路径）
- **现有任务的迁移策略**：SQLite 旧表在 `init_db`（create_all）时不会为已有 task 表加列，因此需一次性迁移：检查 task 表是否存在 stage 列，缺则 `ALTER TABLE task ADD COLUMN stage VARCHAR` 并把已有行 stage 置为 `ready`（旧任务可直接执行），新任务由 create_task 显式设为 `brainstorming`。模型字段默认值用 READY 而非 BRAINSTORMING，避免未显式设置的行卡在需求理解阶段。
