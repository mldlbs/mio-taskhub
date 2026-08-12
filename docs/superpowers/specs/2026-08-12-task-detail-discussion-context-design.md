# 任务细节、讨论与上下文关联设计

日期：2026-08-12
状态：已确认

## 背景

mio-taskhub 当前 Task 模型仅含基础字段（title/description/priority/est_duration/depends_on 等），无法承载真实研发任务的完整信息：验收标准、子任务分解、Git 引用、产出物、执行历史、独立讨论，以及执行上下文（项目/工作区/文件路径）。

需求来源：跨 agent 研发任务需要更多细节，可拉出独立讨论会话，并关联项目/工作区/文件路径数据。经澄清：
- 上下文（项目/工作区/文件路径）由 **agent claim 时传入**
- 讨论形式为 **agent 侧拉回**（agent 拉到自己的会话讨论，结束后将摘要+结论 POST 回任务）
- 文件粒度：**文件路径列表 + 工作区根路径**
- 细节字段：验收标准、子任务/计划、自定义状态标签、Git 引用、截止时间、产出物、执行历史（全选）

## 方案

采用**规范化子表**（方案 B）：标量细节直接加列，重复性/增长性数据（子任务、Git 引用、产出物、历史、讨论）用独立子表，便于查询、展示与扩展。

## 数据模型扩展（models.py）

### Task 表新增标量列

| 字段 | 类型 | 说明 |
|---|---|---|
| `acceptance_criteria` | str | 验收标准 / 完成定义（DoD），默认空 |
| `due_at` | datetime | 截止时间，可空 |
| `labels` | JSON(str) | 自定义状态标签，如 `["blocked","waiting-review"]` |
| `project` | str | 关联项目名（claim 时传入） |
| `workspace` | str | 工作区根路径（claim 时传入） |
| `files` | JSON(str) | 文件路径列表（相对工作区，claim 时传入） |
| `deliverables` | JSON(str) | 预期产出物路径列表 |

### 新子表（1-N）

- **Subtask**：`id / task_id / order(int) / title(str) / status`（pending|in_progress|done|blocked）
- **GitRef**：`id / task_id / ref_type`（branch|commit|pr|tag）`/ value(str) / note(str)`
- **HistoryEvent**：`id / task_id / type(str) / payload(JSON) / at(datetime)` — 执行历史时间线
- **Discussion**：`id / task_id / topic(str) / agent(str) / status`（open|closed）`/ summary(str) / conclusions(str) / started_at / ended_at`
- **DiscussionMessage**：`id / discussion_id / author(str) / role(str) / content(str) / at(datetime)`

> 现有 `Event` 表挂 run_id 且未被使用，保留不动；新增 `HistoryEvent` 挂 task_id 更贴合任务维度。

## API 扩展

```
GET    /tasks/{id}                     # 详情含子任务/gitrefs/历史/讨论
PATCH  /tasks/{id}                     # 更新标量字段（criteria/due_at/labels/deliverables）
POST   /tasks/{id}/subtasks            # 添加子任务
PATCH  /tasks/{id}/subtasks/{sid}      # 更新子任务状态
POST   /tasks/{id}/gitrefs             # 关联 Git 引用
POST   /tasks/{id}/history             # 追加历史事件
POST   /tasks/{id}/discussions         # agent 拉回讨论后回写摘要+结论
GET    /tasks/{id}/discussions         # 列讨论
POST   /tasks/claim                    # 增加可选参数 project / workspace / files
```

约束：
- 所有子资源接口校验 task 存在，404 返回 `task not found`
- 子资源写入后统一通过 WS 广播任务变更
- claim 的 project/workspace/files 仅在任务尚无值时写入（首次领取携带），不覆盖既有值
- `PATCH /tasks/{id}` 可更新全部标量字段（acceptance_criteria/due_at/labels/deliverables/project/workspace/files），其中 project/workspace/files 由人工或 agent 显式补充时使用

## MCP 工具扩展

- `taskhub_claim` 增加可选参数 `project / workspace / files`
- `taskhub_get_task` 返回完整详情（含子任务、git refs、讨论、历史）
- 新增：`taskhub_update_task`、`taskhub_add_subtask`、`taskhub_update_subtask`、`taskhub_add_gitref`、`taskhub_add_history`、`taskhub_add_discussion`

工具沿用扁平参数签名 + `Field` 描述，返回 JSON 字符串。

## Web UI 扩展

- 创建任务表单：新增验收标准、截止时间、标签、产出物、项目、工作区字段
- 任务详情抽屉：子任务勾选、讨论记录、历史时间线、Git 引用、上下文（项目/工作区/文件）
- 列表/看板卡片显示自定义标签与截止时间徽标

## 测试

- models：新表 CRUD 单测
- API：子任务/讨论/gitref/历史/详情接口测试、claim 带 context 集成测试
- MCP：新增工具测试（沿用 ASGITransport + call_tool 模式）

## 不做的事（YAGNI）

- 不做实时 IM 式讨论流（拉回式摘要即可）
- 不做任务依赖图可视化
- 不做文件内容快照
- 不迁移现有 `Event` 表
