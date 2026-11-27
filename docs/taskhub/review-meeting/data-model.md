# 状态模型与数据模型：研发评审会

## 文档信息

| 项 | 值 |
|---|---|
| 任务 ID | a20f3f9f |
| 文档类型 | data-model |
| 版本 | v1.0 |
| 状态 | draft |
| 负责人 | 产品管理专家团 |
| 日期 | 2026-11-26 |
| 上游 | requirement.md / spec.md |
| 范围 | 新增 review_point 表 + 复用实体映射 + 三层状态机；不改既有 schema |

## 1. 实体与字段

### 1.1 新增表 review_point（唯一新表）

| 字段 | 类型 | 必填 | 默认 | 约束 | 说明 |
|------|------|------|------|------|------|
| id | string | 是 | — | PK，格式 `RP-{task8}-{seq}` | 评审点 ID |
| task_id | string | 是 | — | FK→Task，索引 (task_id,status) | 所属任务 |
| meeting_id | string | 否 | null | FK→Discussion（P0 提级） | 关联会议容器 |
| decision_id | string | 否 | null | FK→Decision（P0 提级） | 转决策时回填 |
| source_doc.kind | string | 是 | — | requirement/spec/api/decision | 来源文档类型 |
| source_doc.path | string | 是 | — | workspace 相对路径 | 来源文档路径 |
| source_doc.version | string | 是 | — | `sha256:` 前缀 | 与 ReadEvidence 同口径 |
| source_doc.status | string | 是 | — | 文档生命周期状态 | 提取时快照 |
| kind | string | 是 | — | section_gap/fr_contract/api_contract/data_rule/risk_assumption/decision_point/premise | 评审点类型（区别于 source_doc.kind） |
| section | string | 是 | — | H2/H3 标题链 | 所属章节 |
| anchor | string | 是 | — | 标题链+行号+表格行键 | 定位锚点 |
| claim | string | 是 | — | ≤120 字 | 可证伪原子命题 |
| quote | string | 是 | — | ≤200 字 | 原文逐字引用 |
| why_it_matters | string | 否 | "" | — | 重要性与风险说明（LLM 初稿） |
| risk_level | string | 是 | medium | high/medium/low | 风险分级 |
| suggested_action | string | 否 | "" | confirm/clarify/decide/test/out_of_scope | 建议动作 |
| status | string | 是 | candidate | 见 §2.1 | 评审点生命周期状态 |
| origin | string | 是 | rule | rule/llm | 候选来源 |
| dedup_key | string | 是 | — | 索引 | 去重键 |
| cluster_id | string | 否 | null | 索引 | 跨文档簇 |
| reject_reason | string | 否 | null | — | 驳回/暂缓理由（必填时校验） |
| annotations | JSON | 否 | [] | [{role,stance,comment}] | 多角色意见标注（P1） |
| reviewer | string | 否 | null | — | 裁决人 |
| decided_at | datetime | 否 | null | — | 裁决时间 |
| created_at | datetime | 是 | now() | — | 创建时间 |
| updated_at | datetime | 是 | now() | — | 更新时间（并发判定） |
| parent_id | string | 否 | null | FK→review_point（P1） | 父子归并 |
| fr_refs | array | 否 | [] | P1 | 关联 FR-n |
| tc_refs | array | 否 | [] | P1 | 关联 TC-n |
| rule_version | string | 否 | "" | — | 提取规则版本 |

- 索引：`(task_id, status)`、`dedup_key`、`cluster_id`；唯一约束 `(task_id, seq)`。
- 通过 SQLModel `create_all` 自动建表，对存量库零迁移（沿用既有约定）。

### 1.2 复用实体映射（均不改 schema）

| 实体 | 复用方式 | 关联键 |
|------|---------|--------|
| Task | 会议归属、评审点归属 | review_point.task_id |
| Discussion | 会议容器，`mode=review` | review_point.meeting_id → Discussion.id |
| DiscussionMessage | 逐条讨论，`content` 含 RP-id | 按 RP-id 过滤 |
| Decision | 分歧升级载体，`source=RP-{id}` | review_point.decision_id ↔ Decision.source |
| TaskReview | 关会不可变总账，`checklist={RP-id:bool}` | 由 meeting 关联 |
| Subtask | action_item 落地 | task_id 同源 |
| ReadEvidence | 文档版本口径 | source_doc.version ≡ document_version |
| RolePrompt | 角色提示词快照 | Discussion.prompt_snapshot |

- 会议本体不新建表：一条 `Discussion(mode=review)` 即一次会议。
- `review_id` / `decision_id` 为 P0 字段（2026-11-26 拍板提级），避免依赖"单会议"假设。

## 2. 状态机

### 2.1 review_point 状态机（点级）

| 状态 | 含义 | 进入条件 | 可迁移到 |
|------|------|---------|---------|
| candidate | 候选，全量永久保留 | 提取入库 | shortlisted / rejected |
| shortlisted | 已勾选待评 | 主审勾选 | discussing / candidate |
| discussing | 开会逐条中 | 会议创建 | resolved / rejected / deferred |
| resolved | 采纳/修改/转 TC | 主审裁决 | 终态（不可回退） |
| rejected | 驳回（理由必填） | 主审裁决 | 终态 |
| deferred | 暂缓/转 Decision | 主审裁决 | resolved（Decision resolved 后异步回写） |

- 终态不可回退；会议内 undo 单条裁决写审计日志后回到裁决前状态（例外需留痕）。
- 驳回不物理删除：点仍可查 `status=rejected`。

### 2.2 会议状态机（文档 review 阶段内）

| 状态 | 触发 | 产物 | 出口 |
|------|------|------|------|
| S0 文档就绪 draft | 质量门 errors=0（requirement/spec/api ≥80 分） | 质量报告 | 推进 review |
| S1 候选已生成 candidate | 触发提取 | 全量 review_point | 提取完成 |
| S2 已筛选 shortlist | 主审勾选 5-10 点 | shortlisted 标记 | 每 kind≥1 且总数合规 |
| S3 开会逐条 discussing | 创建 Discussion(mode=review) | 每条点一条 DiscussionMessage | 全部 shortlist 点终态 |
| S4 待关闭 ready_to_close | 门控校验 | decisions≥2 + action_items≥1 | 门控全绿 |
| S5 已关闭 closed | 主审确认 | 五段 review + TaskReview + Subtask | 终态，不自动推文档状态 |
| SX 中止 aborted | 主审中止 / 文档退回 | Discussion 关闭但不写 TaskReview | 终态 |

- 会议状态由 review_point 状态聚合推导 + Discussion.status 判定，不单独建表。
- 同一 review 阶段仅一条 active 会议（单 active 约束）。

### 2.3 文档状态机（既有，不改）

- 沿用 doc_lifecycle 9 状态严格向前；本模块只在 S5 后由人走现有 stage gate。
- 进 design 条件（既有规则）：spec/api 均 approved + ≥1 条讨论记录 + 质量分≥80。
- 关会**不自动**改文档状态（2026-11-26 拍板）。

### 2.4 Decision 状态机（既有，复用）

- open → resolved / superseded；评审转出的 Decision 由 `source=RP-{id}` 溯源。
- resolved 后异步回写 review_point：`status` 转 resolved、`choice/rationale` 留痕。

## 3. 数据完整性约束

- 一条评审点必须恰好属于一个 task；`source_doc.version` 非空。
- `claim` ≤120 字、`quote` ≤200 字，超长在提取时截断并记 warn。
- 驳回与暂缓必须有 `reject_reason`（≥8 字），否则 422。
- 关会前所有 shortlist 点必须终态；`decisions≥2`（宽口径，含当场采纳/修改）、`action_items≥1`。
- TaskReview 只追加不修改：重复关会 409，不新增第二行。
- 审计日志只增不改，每次状态迁移含 from/to/actor/timestamp。
