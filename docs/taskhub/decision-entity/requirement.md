# 需求规格：Decision 实体

## 文档信息

| 项 | 值 |
|---|---|
| 任务 ID | 89a90af6 |
| 文档类型 | requirement |
| 版本 | v1.0 |
| 状态 | approved |
| 作者 | 老周（主理人） |
| 日期 | 2026-11-04 |
| 上游 | docs/taskhub/capability-v0.7/evidence/e7-decision-list.md（E7 执行记录） |
| 范围 | Decision 实体 + REST + 收件箱集成 + MCP + Web 呈现；不含多人协作 |

## 1. 背景与问题

- 2026-11-04 E7 实验（决策清单）结论：用户不是不想决策，是没人告诉他有决策在等他。三个便宜解（看板噪声过滤 / 决策面板 / 422 人话化 + MCP 待办工具）已当日上线。
- 但决策本身仍是二等公民：今天的 5 个真实裁决（R2 冻结、大屏线摘依赖、需求23 放弃、28f8fceb 验收、37e540b9 归档）散落在聊天记录、evidence 文档、discussion 三处，**检索「为什么这么定」只能靠翻**。
- Discussion 是对话流，不是决策记录：没有选项、没有建议、没有 deadline、没有裁决结论的结构化字段。
- 现有系统里「只有你能解的事」有两种形态：系统推导的（依赖阻塞、待验收、失败）已在收件箱；**人主动提出的决策问题**没有承载实体。

## 2. 目标与非目标

- 目标：
  - 决策成为一等公民实体：可创建（问题+选项+建议）、可检索（列表/过滤）、可裁决（choice+理由留痕）、可追溯（关联 task、时间线）。
  - 未裁决的决策进入收件箱 decide 队列——与依赖阻塞并列，用户在一个入口看到所有「该你拍板」的事。
  - Agent（MCP）能登记决策并等用户裁决，而不是在聊天里问完就沉底。
- 非目标（本次明确不做）：
  - 多人投票 / RBAC / 会签——单人场景（A-9/A-11 已否决多人线）
  - 决策与文档链生命周期绑定——决策是轻量记录，不进 22-kind 文档状态机
  - 决策树 / 依赖决策图——无实证需求，YAGNI
  - 通知推送（邮件/IM）——无外部通道，收件箱 + MCP 足够

## 3. 用户故事 / 使用场景

- 唯一用户：开发者本人（决策人）；次级使用者：agent（经 MCP 登记决策问题、催裁决）。
- 场景 A（agent 登记）：会话中发现「这里有两个方案，需要你拍板」→ agent 调 MCP 工具登记决策（问题/选项/建议）→ 用户在收件箱看到 → 裁决 → agent 下次会话可查结论。
- 场景 B（用户自记）：自己犹豫的技术选型，登记后过几天裁决，留痕可检索。
- 场景 C（回溯）：三个月后查「当时为什么选了 A 不选 B」→ GET /decisions/{id} 看选项+理由。

## 4. 功能需求（FR）

### 4.1 需求概览

| FR | 名称 | 类型 | 人日 | 前置 |
|---|---|---|---|---|
| FR-01 | Decision 数据模型 | 功能代码 | 0.3 | 无 |
| FR-02 | REST 端点（建/列/查/裁决） | 功能代码 | 0.5 | FR-01 |
| FR-03 | 收件箱 decide 队列集成 | 功能代码 | 0.2 | FR-01 |
| FR-04 | MCP 工具（登记/裁决） | 功能代码 | 0.3 | FR-02 |
| FR-05 | Web 决策面板呈现与裁决交互 | 功能代码 | 0.5 | FR-02 |

### FR-01 Decision 数据模型

- 用户故事：作为唯一用户，我希望每个「需要我拍板的问题」以结构化记录存在（问题/选项/建议/结论/理由/时间），以便检索与回溯。
- 验收标准：
  - 字段：id（8 位十六进制）、question（必填）、context（背景说明）、options（JSON 数组，每项 {label, note}，可空）、recommendation（建议选项 label，可空）、status（open/resolved/superseded）、choice（裁决结果，resolved 时必填）、rationale（裁决理由，resolved 时必填 ≥8 字符）、decider（裁决人）、deadline（可选）、task_id（可选关联）、source（登记来源）、created_at、resolved_at。
  - 新表经 SQLModel create_all 自动建，无需手写迁移。
  - status 状态机严格向前：open → resolved / open → superseded；终态不可回退。
- 预估：0.3 人日。

### FR-02 REST 端点

- 用户故事：作为用户/agent，我希望经 HTTP 创建、检索、裁决决策，以便与现有工具链集成。
- 验收标准：
  - `POST /api/v1/decisions`：body {question, context?, options?, recommendation?, deadline?, task_id?, source?}；question 缺失 → 422；options 非 [{label, note}] 形态 → 422；返回完整记录（含 id）。
  - `GET /api/v1/decisions?status=open&limit=`：默认 open；返回 {count, items}，按 created_at 降序。
  - `GET /api/v1/decisions/{id}`：不存在 → 404。
  - `POST /api/v1/decisions/{id}/resolve`：body {choice, rationale?, decider?}；choice 缺失 → 422；对终态再裁决 → 409；成功置 resolved 并落 resolved_at。
  - `POST /api/v1/decisions/{id}/supersede`：置 superseded 并记 successor_id（可选）。
  - 全部端点只写 decisions 表，不触碰任务状态机。
- 预估：0.5 人日。

### FR-03 收件箱 decide 队列集成

- 用户故事：作为用户，我希望 open 决策与依赖阻塞并列出现在收件箱「待你拍板」段，一个入口看全。
- 验收标准：
  - `GET /api/v1/tasks/inbox` 的 decide 队列包含 open 决策（含 question、age_days、action 指向 resolve 端点）；summary.decide = 依赖阻塞数 + open 决策数。
  - 裁决后该决策从队列消失（一个轮询周期内）。
- 预估：0.2 人日。

### FR-04 MCP 工具

- 用户故事：作为 agent，我发现需要用户拍板的分叉时，希望能登记决策并留待用户裁决，而不是在聊天里问完就沉底。
- 验收标准：
  - `taskhub_add_decision(question, context, options, recommendation, deadline, task_id)` → 创建 open 决策。
  - `taskhub_resolve_decision(decision_id, choice, rationale)` → 裁决留痕。
  - `taskhub_my_inbox` 返回的 decide 队列自动含 open 决策（复用 FR-03，不加新工具）。
  - 工具描述明确：**登记后要把决策呈现给用户，不要自己替用户裁决**。
- 预估：0.3 人日。

### FR-05 Web 决策面板呈现与裁决交互

- 用户故事：作为用户，我希望在决策面板里直接裁决（选选项+填理由），不用切到接口工具。
- 验收标准：
  - DecisionInbox 的 decide 段显示 open 决策（question 为标题，标注 recommendation）。
  - 点决策行 → 弹裁决对话框（选项单选 + 理由输入 + 提交）→ 调 resolve 端点 → 队列即时刷新。
  - 对话框可取消，不留半成品。
- 预估：0.5 人日。

## 5. 非功能需求

- 性能：decisions 表预计单表 < 10⁴ 行；列表/收件箱查询走 status 索引，P95 < 50ms（本地 SQLite）；POST/resolve 全程 < 100ms。
- 安全：本服务仅监听 127.0.0.1（既有约束不变）；decisions 端点与现有 /tasks 同级信任域，不做额外鉴权；写入内容不落敏感凭据。
- 数据：决策记录随 SQLite 主库存储，随既有备份策略；不可变审计要求——resolve 只更新裁决字段，不覆盖 question/options 原文。
- 兼容：新表经 create_all 自动创建，对存量库零迁移；MCP 新增工具不改变既有工具签名。

## 6. 验收与 DoD

- pytest 新增用例全绿（模型校验 / REST 边界 422、409、404 / 收件箱集成 / 排序），受影响回归套件零失败。
- 实弹验证：创建决策 → 收件箱出现 → Web 面板可见 → 裁决 → 队列消失 → GET 回溯含选项与理由。
- 打包进 exe（PYZ 含 decisions 模块）并重启验证。

## 7. 风险与开放问题

- 唯一风险：收件箱语义污染（决策与依赖阻塞混排）——用 action 提示区分，验收时核对可读性。
