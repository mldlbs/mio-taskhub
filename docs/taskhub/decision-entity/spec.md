> 软件项目文档链 · 7 件套  
> 上游：[架构设计](docs/taskhub/89a90af6/architecture.md) | 下游：[状态模型](docs/taskhub/89a90af6/data-model.md)  
> 任务：89a90af6（Decision 实体）

# 模块 Spec（Module Specification）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-11-04 13:36 |
| 状态 | approved |
| 负责人 | agent:workbuddy |
| 适用范围 | mio_taskhub/api/decisions.py、mio_taskhub/models.py（Decision）、inbox 集成、MCP 工具、Web DecisionInbox |

## 1. 模块职责与边界

- 做什么：
  - 把「需要用户拍板的问题」持久化为一等公民 `Decision` 实体（open → resolved / superseded）
  - 提供决策的登记、列表、详情、裁决、作废五个 REST 端点（prefix `/api/v1/decisions`）
  - 把 open 决策并入 `GET /tasks/inbox` 的 decide 队列（与依赖阻塞同列，最老的在前）
  - 提供 MCP 工具 `taskhub_add_decision` / `taskhub_resolve_decision`，约束 agent 只登记不裁决
  - Web 端「待我处理」面板呈现 open 决策 + 裁决对话框（选项单选 / 自由作答 / 理由留档）
- 不做什么：
  - 不改任务状态机：决策裁决**不**联动 Task.state / stage（requirement FR-02 验收标准）
  - 不做决策与任务的双向绑定校验（task_id 仅作弱关联，任务删除不级联）
  - 不做决策提醒/推送（deadline 只是展示字段，不触发通知——推送属后续任务）
  - 不做多用户/权限模型（127.0.0.1 信任域内，decider 为自由字符串）

## 2. 输入 / 输出

- 输入：
  - REST JSON body（question 必填；options `[{label, note}]`；deadline ISO 8601）
  - MCP 工具参数（options 以 JSON 字符串传入，工具层解析后转发）
- 输出：
  - `_serialize()` 统一 15 字段 JSON（见 data-model.md 实体表）
  - inbox decide 队列条目（kind=decision，含 question/options/action/age_days）
  - 数据库 `decision` 表（SQLModel create_all 自动建，零迁移）

## 3. 依赖

- 上游模块：
  - `mio_taskhub.db.get_session`（会话注入）
  - `mio_taskhub.models.Decision`（SQLModel 表模型）
- 下游消费方：
  - `api/inbox.py`（decide 队列集成）
  - `mcp_server.py`（两个工具）
  - `web/src/components/DecisionInbox.jsx` + `web/src/api.js`（前端三方法）
- 外部库：无新增（FastAPI / SQLModel / httpx 均已有）

## 4. 详细设计

### 4.1 数据模型（models.py 尾部追加）

- `Decision(SQLModel, table=True)`，15 字段：id/question/context/options(JSON)/recommendation/status/choice/rationale/decider/deadline/task_id/successor_id/source/created_at/resolved_at
- 索引：question、status、task_id、created_at
- 状态机：`open` →（resolve）`resolved`；`open` →（supersede）`superseded`；两终态不可逆（终态再操作 409）

### 4.2 REST 端点（api/decisions.py）

- 校验顺序（黄金顺序沿用）：404 → 状态校验（409）→ 参数校验（422）
- `_validate_options`：接受 `[{label, note}]` 与纯字符串数组（自动转 `{label, note:""}`）；缺 label / 非 list → 422 人话化
- `_parse_deadline`：`fromisoformat` + Z 后缀兼容 + naive 补 UTC；解析失败 422
- 裁决审计只增不改：resolve 只写 choice/rationale/decider/status/resolved_at，question/options 原文永不覆盖
- supersede：successor_id 可选；不存在 → 422；作废后 resolved_at 也落时间

### 4.3 inbox 集成（api/inbox.py）

- decide 队列追加 kind=decision 条目（含 question/title/question 双字段，title=question 供旧消费方兼容）
- 排序：decide 队列整体按 age_days 降序（最老的在前，与 review 同向）
- summary 新增分解：`decide = decide_tasks + decide_decisions`（旧字段 decide/review/failed 语义不变）

### 4.4 MCP 工具（mcp_server.py）

- `taskhub_add_decision(question, options, recommendation, task_id, context, deadline, source)`：options 为 JSON 字符串，解析失败返回人话错误（不抛 500）
- `taskhub_resolve_decision(decision_id, choice, rationale, decider)`：直接转发 resolve 端点
- 工具描述硬约束：「登记后呈现给用户，不要自己替用户裁决」；instructions 首屏规范同步更新
- 风险门控：POST /decisions 归类 write（非 destructive），默认放行——登记是 agent 职责，裁决须用户明示

### 4.5 Web 前端（web/src/）

- `api.js` 新增 listDecisions/resolveDecision/supersideDecision 三方法
- `DecisionInbox.jsx`：30s 轮询 open 决策；决策行点击弹裁决对话框（radio 选项 + 自由作答 input + 理由 textarea + 作废次按钮）；提交后即时刷新队列
- `ledger.css` 新增 inbox-dlg__* 样式（紧凑单列弹窗，深浅主题适配）

## 5. 异常与边界情况

- 决策不存在：404「decision {id} 不存在」
- 终态再 resolve/supersede：409 + 提示查历史或新开决策
- options 传字符串/缺 label：422 + 「你要做的是」人话指引
- deadline 非 ISO：422 + ISO 8601 格式示例
- resolve 缺 choice：422 + 列出可选 label（无预设选项时显示「无预设选项」）
- 拉取失败（Web 轮询）：静默 catch，不阻塞主界面
