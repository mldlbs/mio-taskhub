# 模块详细设计：研发评审会

## 文档信息

| 项 | 值 |
|---|---|
| 任务 ID | a20f3f9f |
| 文档类型 | spec |
| 版本 | v1.1 |
| 状态 | approved |
| 负责人 | 产品管理专家团 |
| 日期 | 2026-11-26 |
| 上游 | requirement.md（FR-1~18） |
| 下游 | api.md / data-model.md / test.md |

## 1. 模块职责与边界

- 本模块负责三件事：评审点提取、逐条裁决编排、关会汇总写入。
- 不负责：文档质量判定（复用 `doc_quality`）、文档状态推进（复用 stage gate）、决策生命周期（复用 `decisions`）、用户鉴权（复用现有信任域）。
- 一句话定位：在现有 `doc_lifecycle` 的 `review` 状态内嵌一套逐条裁决流程；会议本体 = 一条 `Discussion(mode=review)`，实质产出落回既有实体。

| 边界 | 本模块 | 复用不改 |
|---|---|---|
| 会议容器 | 确定 roles、prompt_snapshot 版本、单 active 约束 | Discussion schema |
| 评审点 | 新建 `review_point` 表 + 提取 / 去重 / 排序 / 裁决逻辑 | — |
| 讨论留痕 | 写带 RP-id 的消息 | DiscussionMessage schema |
| 决策升级 | 组装 question/options/context/source | Decision schema 与 resolve 端点 |
| 关会产物 | 编排五段 + TaskReview 追加 + Subtask | TaskReview / Subtask schema |
| 文档状态 | 不推进 | stage gate 现有规则 |

## 2. 详细设计

### 2.1 评审点提取管线（FR-1~5）

- 触发点：`POST /reviews/extract` 由主审显式调用；进入 review 且 quality.errors=0（requirement/spec/api 另需≥80 分）时前端提示可提取。
- 规则实现落 `mio_taskhub/review/extract.py`：H2 → H3 → H4 → 表格行 + 列表项递归下钻；元信息 / 空标题过滤；内容指纹去重；版本 `sha256:` 16 位摘要。H5 判定口径落 `mio_taskhub/review/h5.py`（词边界 / 数字上下文 / scope 归属 / 弱命中剔除）。
- 步骤：
  - 1. 读取文档正文，复用 `doc_quality._sections_of` 得到 H2/H3 骨架；复用 `_split_by_heading` 切明细单元；复用 `_table_data_rows` 取表格行。
  - 2. 按 `source_doc.kind` 分派规则：requirement 取每个 FR-n 行；api 取错误码表每行 + 每个接口的每个参数组；spec 取每个 H3 明细单元；decision 取每个选项与理由段。
  - 3. 逐点填 P0 字段：id=`RP-{task8}-{seq}`、`anchor`=标题链+行号+表格行键、`quote` 逐字 ≤200 字、`claim` ≤120 字可证伪、`origin=rule`、`status=candidate`。
  - 4. `source_doc.version` = `sha256:` + 正文摘要，与 `ReadEvidence.document_version` 同口径。
  - 5. 去重三层：`dedup_key` 相同归同 `cluster_id`（P0）；LLM 语义 ≥0.85 仅写建议，人工确认才合并；parent_id 父子归并列 P1。
  - 6. LLM 增强（可失败）：产出 `why_it_matters` 初稿 / `risk_level` 建议 / `suggested_action` 建议；成功则 `origin=llm` 或保留 rule 来源标记；失败则字段留空、`origin=rule`，候选点数不变。
- 幂等：按 `(task_id, version, dedup_key)` 唯一判定，重跑命中则跳过，不新增行。

### 2.2 强制入会与排序（FR-6）

- 强制入会条件（满足任一即 shortlisted）：`risk_level=high`；跨文档同 cluster 且 claim 冲突；kind=premise 且无样本量说明。
- 排序分 = risk 权重（high=3/medium=2/low=1）×10 + 同簇聚合加成 + 文档序；展示按 risk 降序、簇内按 section 文档序。
- 覆盖约束：每个出现的 kind 至少 1 点；总数 5-10，候选不足 5 则全入；超出 10 的按排序分截断，但被强制入会的点不受截断影响。
- 未入选点保留 `status=candidate`，可在后续会议再加入。

### 2.3 会议状态机（FR-8~9）

- 会议状态沿用文档级 9 状态；在 `review` 内的一次会议 = 一条 `Discussion(mode=review)`。
- 会议子状态由 `review_point.status` 聚合推导，不单独建表：
  - 全部 candidate → S1 候选已生成
  - 存在 shortlisted → S2 已筛选
  - 存在 discussing 且有消息 → S3 开会逐条
  - 全部 shortlist 点终态 → S4 待关闭
  - Discussion.status=closed 且 TaskReview 已追加 → S5 已关闭
  - 主审中止且未写 TaskReview → SX 已中止
- 单 active 约束：同一 task 同时只一条 `status=open` 的 review Discussion；创建第二条返回 409。
- `prompt_snapshot` 记 RolePrompt 版本快照，保证复盘时能还原当时的角色提示词。

### 2.4 逐条裁决（FR-10~11）

- 动作与落点：

| 动作 | review_point.status | DiscussionMessage | 五段落点 | 其他 |
|---|---|---|---|---|
| 采纳 confirm | resolved | 带 RP-id 的结论消息 | suggestions / decisions | checklist[RP]=true |
| 修改 amend | resolved | 结论 + 修改意见原文 | suggestions + action_items | 生成 Subtask |
| 驳回 reject | rejected | 理由必填 | divergences | checklist[RP]=false |
| 转 Decision | deferred | 结论 + Decision 引用 | decisions | 建 Decision，decision_id 回填 |
| 转 TC | resolved | 结论 + TC 引用 | action_items + suggestions | 生成 Subtask |
| 暂缓 defer | deferred | 理由必填 + revisit_trigger | risks | 不生成 Subtask |

- 每条裁决即时写库，刷新不丢；会议内可 undo 单条裁决并写审计日志。
- 转 Decision 由 `POST /decisions` 完成：`question=claim` 原文、`options` 来自 suggested_action 变体、`context=why_it_matters + quote`、`source=RP-{id}`；Decision resolved 后异步回写点，`status` 转 resolved。

### 2.5 关会编排（FR-12~15）

- 门控检查：所有 shortlist 点 ∈ {resolved, rejected, deferred}；`decisions≥2`（宽口径）；`action_items≥1`。不满足 → 409 并列出缺口清单。
- 通过时按顺序原子写入：
  - 1. `Discussion.review` 写五段（risks / divergences / suggestions / decisions / action_items），`status=closed`、`ended_at` 落时间戳。
  - 2. 追加 1 条 `TaskReview`：`checklist={RP-id: bool}`、`artifacts=doc paths`、`decision` 由是否残留 question/escalated 判定。
  - 3. 每条 action_item 生成 1 条 `Subtask`（task_id 同源，标题含 RP-id）。
  - 4. 不推进文档状态；由人走现有 stage gate。
- 重复关会 → 409，不新增第二条 TaskReview。

### 2.6 数据读写与并发

- 新增 `review_point` 表，索引 `(task_id, status)`、`dedup_key`、`cluster_id`，`(task_id, seq)` 唯一约束。
- `review_id` / `decision_id` 为 P0 字段（2026-11-26 拍板提级），不依赖"单会议"假设。
- 写操作校验顺序固定：404 → 所有权 → 状态；每步返回对应业务码与人话提示。
- 审计日志：每次状态迁移写 from/to/actor/timestamp，只增不改。

### 2.7 失败降级路径

- LLM 超时 / 返回空 / 返回删点指令 → 忽略，保留全部 rule 点，LLM 字段留空。
- 文档无 H2/H3/表格 → 提取返回 0 点并给出人话提示，不报错。
- H5 pilot 实测 Recall=22/49=44%（2026-11-26，5 样本，剔除 3 个弱命中）→ 落 30-50% 观察区，功能照常落地，价值叙事固定为"防遗漏 + 结构化留痕 + 逐条裁决"，不改 FR 实现、不回滚代码。
