# 架构设计：研发评审会

## 文档信息

| 项 | 值 |
|---|---|
| 任务 ID | a20f3f9f |
| 文档类型 | architecture |
| 版本 | v1.0 |
| 状态 | draft |
| 负责人 | 产品管理专家团 |
| 日期 | 2026-11-26 |
| 上游 | requirement.md |
| 下游 | spec.md / data-model.md / api.md |

## 1. 系统上下文

- 本模块是 mio-taskhub（FastAPI + SQLite + MCP + React）内的一个新能力域，不是独立服务。
- 上游输入：任务文档（`docs/taskhub/**/*.md`）、质量门报告（`doc_quality`）、角色提示词（RolePrompt）。
- 下游消费：Web 评审页、agent（MCP 工具）、design gate（读 TaskReview + Discussion 判定）。
- 外部依赖：可选 LLM（走现有代理，超时降级）。

| 参与方 | 交互方式 | 依赖本模块的什么 |
|---|---|---|
| 主审（用户） | Web UI | 评审点清单、逐条裁决、关会 |
| agent | MCP / REST | 提取、查询、登记由评审产生的决策 |
| design gate | 读既有实体 | TaskReview + Discussion 记录 |
| 质量门 | 只读复用 | 文档解析骨架，不修改 |

## 2. 总体架构

- 分层：
  - 接入层：`api/reviews.py`（新增 8 端点）+ MCP 工具（7 个）+ Web 组件（6 个）。
  - 编排层：`spec.md` 定义的提取管线、裁决编排、关会编排。
  - 领域层：`review_point` 新表 + 复用既有实体（Discussion/TaskReview/Decision/Subtask）。
  - 基础设施：SQLite（create_all 零迁移）、path_guard（文档读取）、doc_quality（解析复用）、LLM 代理（可选）。
- 关键取舍：会议本体不建表（复用 Discussion），新表只有 review_point 一张；文档生命周期不变。

| 层 | 组件 | 职责 | 不改既有 |
|---|---|---|---|
| 接入 | api/reviews.py | 8 REST 端点，404→所有权→状态 校验 | 复用 get_session |
| 接入 | mcp_server.py | 7 MCP 工具 | 复用 add/resolve decision |
| 接入 | web/src/Review*.jsx | 三栏评审页 + 进度条 + 底栏门控 | 复用表格/文档预览/Decision 弹窗 |
| 编排 | review 提取/裁决/关会 | 状态机 + 副作用编排 | — |
| 领域 | review_point | 评审点原子单位 | — |
| 基础设施 | SQLite / LLM | 存储与可选增强 | create_all / 超时降级 |

## 3. 关键数据流

### 3.1 提取流

- 1. 主审触发 `POST /reviews/extract`（或前端在进入 review 后提示）。
- 2. 读文档正文 → path_guard 校验路径 → doc_quality 解析骨架（H2/H3/表格行）。
- 3. 按 kind 分派规则生成 rule 候选点 → 填 15 P0 字段 → version 取 sha256。
- 4. LLM 增强（可选，超时 8s 降级）：why/risk/suggested_action/cluster 建议。
- 5. dedup_key 聚类 → 幂等写库 → 返回 `{points, skipped}`。

### 3.2 逐条裁决流

- 1. shortlist 确定 → `POST /meetings/open` 创建 `Discussion(mode=review)` → 每点写一条带 RP-id 的 DiscussionMessage。
- 2. 主审逐条 `POST /points/{id}/decide` → 更新点 status + 写消息 + 副作用（Decision/Subtask/action_item）。
- 3. 转 Decision 走 `POST /decisions` → Decision.resolved 后异步回写点。

### 3.3 关会流

- 1. `POST /meetings/{id}/close` → 门控校验（点全终态 + decisions≥2 + action_items≥1）。
- 2. 通过 → 写五段 review + 追加 TaskReview + 生成 Subtask → Discussion 关闭。
- 3. 不推文档状态；由人走现有 stage gate 决定是否进 design。
- 4. 失败 → 409 + gaps 清单，列出未终态点与缺口计数。

### 3.4 回溯流

- `GET /meetings/{id}/trace?point_id=` → RP-id → quote/anchor/version sha256 → 裁决结论 → 关联 Decision/Subtask。
- 文档改版后旧点仍可回溯旧版本 sha256（版本快照永久保留）。

## 4. 部署与影响面

- 新增文件：`api/reviews.py` 扩展、`mcp_server.py` 扩展、`web/src/Review*.jsx`、`models.py` 增 review_point。
- create_all 自动建表，对存量库零迁移；既有 API 签名不变。
- MCP 新增工具需重启宿主才可见——发版说明必须标注。
- 打包进 exe：PYZ 含新模块；前端构建后拷 dist；无需改 Tauri 壳。
