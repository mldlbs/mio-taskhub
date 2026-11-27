# 需求规格：研发评审会（文档评审点提取与逐条裁决）

## 文档信息

| 项 | 值 |
|---|---|
| 任务 ID | a20f3f9f |
| 文档类型 | requirement |
| 版本 | v1.0 |
| 状态 | approved |
| 作者 | 产品管理专家团（小探 / 阿稳）+ 老周汇编 |
| 日期 | 2026-11-26 |
| 上游 | 会话需求："写完 spec/plan/api/adr 后从文档提取可评审点，再逐条评审" |
| 范围 | 评审点提取 + 逐条裁决 + 关会汇总；复用现有 Discussion/TaskReview/Decision，首版四类文档 |
| 明确不含 | 多角色并行会签（P1）、plan 及剩余 17 类文档（P1）、`pre_review` 字段、自动合并 |

## 1. 背景与问题

- 现状：文档写完靠质量门校验结构与分数，但"这份 spec 里哪些点值得开会讨论"没有机制——评审凭注意力，容易漏。
- 痛点一：评论散落在聊天、Discussion、evidence 三处，对不上原文位置；事后查"当时为什么这么定"只能翻。
- 痛点二：分歧在评论里蒸发，没有结构化载体（选项 / 建议 / 裁决理由 / 拍板人）。
- 痛点三："评审过"没有可机读证据，design gate 只认讨论记录条数，不认逐条结论。
- 2026-11-26 已确认的设计基线：不新建评审系统，在现有文档 `review` 阶段内嵌一套逐条裁决流程。

## 2. 目标与非目标

- 目标：
  - 机器按文档结构自动铺点，召回优先、零边际成本，候选全量永久保留。
  - 人做减法：勾 5-10 个待评点，逐条裁决，每条落到唯一终态。
  - 所有实质产出落回既有实体（Discussion / DiscussionMessage / TaskReview / Decision），只新增 `review_point` 一张表。
  - 关会产出可机读证据（五段 review + TaskReview），喂给现有 design gate。
- 非目标（明确不做）：
  - 不改 `doc_lifecycle` 9 状态、不改 DOC_KINDS、不改 QUALITY_SPEC、不重写 Markdown 解析。
  - 不建 `review_meeting` 表——会议就是一条 `Discussion(mode=review)`。
  - 不做多角色并行会签（降 P1）——单一主审裁决，多角色只做意见标注。
  - 不加 `pre_review` 字段——agent 预审用 `origin` + `why_it_matters` 承载。

## 3. 用户故事 / 使用场景

- 唯一用户：开发者本人（主审）；次级使用者：agent（经 MCP 触提提取、查询评审点、登记由评审产生的决策）。
- 场景 A（提取）：文档从 draft 推进到 review 且质量门通过 → 系统自动提取候选评审点 → 主审看到按风险与簇分组的清单。
- 场景 B（裁剪）：主审勾选 5-10 个必评点（强制纳入 high 风险 / 跨文档冲突 / premise 无样本量的点），其余留 candidate 但永久保留。
- 场景 C（逐条裁决）：开会后一次聚焦一条，执行采纳 / 修改 / 驳回 / 转 Decision / 转 TC / 暂缓六选一；驳回与暂缓必填理由。
- 场景 D（关会）：所有点终态且门控满足（decisions≥2、action_items≥1）→ 关会写五段 review + 一条不可变 TaskReview + action_item 转 Subtask。
- 场景 E（回溯）：三个月后查某条评审结论 → 由 RP-id 定位到 quote / anchor / 文档版本 sha256 / 裁决结论 / 关联 Decision。

## 4. 功能需求（FR）

### 4.1 需求概览

| FR | 名称 | 类型 | 优先级 | 前置 |
|---|---|---|---|---|
| FR-1 | 进入 review 时自动触发提取 | 功能代码 | P0 | 无 |
| FR-2 | 复用质量门解析器按四类文档规则提取 | 功能代码 | P0 | FR-1 |
| FR-3 | LLM 仅产出结构化建议、无权删点、失败降级 | 功能代码 | P0 | FR-2 |
| FR-4 | 15 个 P0 字段齐全、id 可复现、版本 sha256 口径 | 功能代码 | P0 | FR-2 |
| FR-5 | 三层去重（父子归并 P1 / dedup_key 聚类 / LLM 语义建议） | 功能代码 | P0 | FR-4 |
| FR-6 | 强制入会 + 排序分 + 每 kind≥1 + 总数 5-10 | 功能代码 | P0 | FR-5 |
| FR-7 | candidate 全量永久保留、重跑幂等 | 功能代码 | P0 | FR-5 |
| FR-8 | 创建/复用 Discussion(mode=review) 作会议容器 | 功能代码 | P0 | FR-6 |
| FR-9 | 每条 shortlist 点写一条带 RP-id 的 DiscussionMessage | 功能代码 | P0 | FR-8 |
| FR-10 | 六动作各自更新 status 并产生副作用 | 功能代码 | P0 | FR-9 |
| FR-11 | 转 Decision 契约（claim→question，source=RP-*，resolved 回写） | 功能代码 | P0 | FR-10 |
| FR-12 | 关会门控（点全终态 + decisions≥2 + action_items≥1） | 功能代码 | P0 | FR-10 |
| FR-13 | 关会追加一条不可变 TaskReview | 功能代码 | P0 | FR-12 |
| FR-14 | action_item 生成 Subtask | 功能代码 | P0 | FR-13 |
| FR-15 | 关会后由人走现有 stage gate 推进文档状态 | 功能代码 | P0 | FR-13 |
| FR-16 | 全链回溯（RP-id → 原文 → 版本 → 裁决 → Decision/TC） | 功能代码 | P1 | FR-13 |
| FR-17 | 多角色意见标注（非会签） | 功能代码 | P1 | FR-9 |
| FR-18 | 会议级 agent_summary | 功能代码 | P1 | FR-8 |

### FR-1 提取触发

- 用户故事：作为主审，我希望文档质量门通过后自动铺出评审点，不必手工逐段找问题。
- 验收标准：
  - 文档由 draft 推进 review 且 `quality.errors=0`（requirement/spec/api 另需分数≥80）时触发提取。
  - errors>0 时推进被门控拒绝，且不产生任何评审点。
  - 提取幂等：同 task 同版本重跑不新增重复点。
- 预估：0.3 人日。

### FR-2 规则提取

- 用户故事：作为主审，我希望候选点按文档结构铺全，宁可多提不漏。
- 验收标准：
  - 复用 `doc_quality._sections_of / _split_by_heading / _table_data_rows`，不重写 Markdown 解析。
  - 四类文档（requirement / spec / api / decision）各按专属规则提取：requirement 对每个 FR-n、api 对错误码表每行、spec 对每个 H3 明细单元、decision 对每个选项与理由至少产出 1 点。
  - 每点 `anchor` 含标题链 + 行号 + 表格行键；`quote` 逐字且 ≤200 字；`claim` ≤120 字可证伪。
- 预估：0.8 人日。

### FR-3 LLM 增强与降级

- 用户故事：作为主审，我希望 LLM 帮我写清"这点为什么重要"并聚类，但不能替我删点。
- 验收标准：
  - LLM 只产出结构化 JSON：`why_it_matters` 初稿 / `cluster_id` 建议 / `risk_level` 建议；逐条附 quote + anchor。
  - mock LLM 返回"删除某候选"指令 → 忽略，点数不减。
  - LLM 超时或返回空 → 全部 rule 点保留，LLM 字段为空，`origin=rule`。
- 预估：0.5 人日。

### FR-4 字段与版本口径

- 用户故事：作为回溯者，我希望每个点都能定位到产生它的文档版本。
- 验收标准：
  - 15 个 P0 字段齐全：`id / task_id / source_doc{kind,path,version,status} / kind / section / anchor / claim / quote / why_it_matters / risk_level / suggested_action / status / origin / dedup_key / cluster_id`。
  - id 格式 `RP-{task8}-{seq}`；同 task 同版本跑两次 id 集合相同。
  - `source_doc.version` 为 `sha256:` 前缀，与 `ReadEvidence.document_version` 同口径；抽 1 点比对完全相等。
- 预估：0.3 人日。

### FR-5 三层去重

- 用户故事：作为主审，我希望重复点归簇但不丢原始点。
- 验收标准：
  - 父子归并（parent_id）列 P1；`dedup_key` 跨文档聚类为 P0；LLM 语义相似度 ≥0.85 仅给建议，人工确认才合并。
  - 两份文档同 dedup_key → 同 cluster_id；LLM 建议合并被人工拒绝 → 两点都留。
  - 去重不物理删除任何原始候选点。
- 预估：0.4 人日。

### FR-6 强制入会与排序

- 用户故事：作为主审，我希望高风险点一定进会，避免被裁掉。
- 验收标准：
  - 强制入会条件：`risk_level=high` / 跨文档冲突 / premise 无样本量 → 自动 shortlisted。
  - 排序分公式：按 risk_level 降序 + cluster_id 聚合 + section 文档序。
  - 每个出现的 kind 至少 1 点入会；总数 5-10（候选不足则全入）。
- 预估：0.3 人日。

### FR-7 候选永久保留

- 用户故事：作为回溯者，我希望被驳回的点仍可查，证明"评审过且判过不采纳"。
- 验收标准：
  - 驳回 / 剔除不物理删除，点仍可查且 `status=rejected`。
  - 同版本重跑按 dedup_key 幂等，不产生重复。
- 预估：0.2 人日。

### FR-8 会议容器

- 用户故事：作为主审，我希望会议复用现有讨论容器，不另起一套。
- 验收标准：
  - shortlist 确定后创建一条 `Discussion(mode=review, roles, prompt_snapshot=RolePrompt 版本)`。
  - 同一 task 同时仅一条 `status=open` 的 review Discussion。
  - `review_id` / `decision_id` 关联字段为 P0（2026-11-26 拍板提级）。
- 预估：0.3 人日。

### FR-9 逐条讨论落点

- 用户故事：作为主审，我希望每条评审点的结论都能在讨论记录里找到。
- 验收标准：
  - 每条 shortlist 点进入 discussing 时写一条 `DiscussionMessage`，`content` 含 RP-id，`author` 与 `role` 非空且 role ∈ 会议 roles。
  - 6 个点 → ≥6 条消息，可按 RP-id 过滤。
- 预估：0.3 人日。

### FR-10 六动作裁决

- 用户故事：作为主审，我希望每一点都能落到唯一终态，不能"评了等于没评"。
- 验收标准：
  - 动作六种：采纳 confirm / 修改 amend / 驳回 reject / 转 Decision / 转 TC / 暂缓 defer。
  - 驳回与暂缓理由必填，缺失 → 422 人话化提示。
  - 转 Decision 生成 Decision 且 `decision_id` 回填；转 TC 生成 action_item。
  - 点终态 ∈ {resolved, rejected, deferred}；deferred 可由后续 Decision resolved 异步回写为 resolved。
- 预估：0.6 人日。

### FR-11 转 Decision 契约

- 用户故事：作为主审，我希望分歧当场转成可拍板的决策，而不是沉在评论里。
- 验收标准：
  - `POST /api/v1/decisions`：`question=claim` 原文、`options=[{label,note}]` 来自 suggested_action 变体、`context=why_it_matters + quote`、`source=RP-{id}`、`task_id` 同源。
  - `GET /decisions/{id}` 可查；resolve 后点异步回写 `choice/rationale` 并转 resolved。
- 预估：0.3 人日。

### FR-12 关会门控

- 用户故事：作为主审，我希望没评完的会关不掉。
- 验收标准：
  - 所有 shortlist 点终态 + `decisions≥2` + `action_items≥1`。
  - `decisions` 计数用宽口径：当场采纳 / 修改 + 转 Decision 登记都计入（2026-11-26 拍板）。
  - 存在 discussing 点关会 → 409 并列出缺口；满足后 → `Discussion.status=closed`、`ended_at` 非空、review 五段齐全。
- 预估：0.3 人日。

### FR-13 不可变 TaskReview

- 用户故事：作为回溯者，我希望关会结论只追加不改写。
- 验收标准：
  - 关会追加 1 条 `TaskReview`：`checklist={RP-id: bool}`、`artifacts=doc paths`、`decision ∈ {approve, reject, comment}`。
  - `Discussion.review` 五段：risks / divergences / suggestions / decisions / action_items。
  - 再次关会被拒且不新增第二行。
- 预估：0.3 人日。

### FR-14 action_item 转 Subtask

- 用户故事：作为主审，我希望会上决定的行动项自动进任务清单。
- 验收标准：每条 action_item 生成 1 条 `Subtask`（task_id 同源），标题含 RP-id。
- 预估：0.2 人日。

### FR-15 文档状态由人推进

- 用户故事：作为主审，我希望关会不擅自改文档状态，由我按现有流程推进。
- 验收标准：
  - 关会只产出 TaskReview + 五段，不改文档状态（2026-11-26 拍板）。
  - 后续由人走现有 stage gate：spec/api approved + ≥1 讨论记录 + 分数≥80 才进 design；缺讨论记录 → 422 阻断。
- 预估：0.2 人日。

### FR-16 全链回溯（P1）

- 验收标准：给定 RP-id 一次查询返回 quote / anchor / 版本 sha256 / 裁决结论 / 关联 Decision/TC；文档改版后旧点仍可回溯旧 sha256。
- 预估：0.4 人日。

### FR-17 多角色意见标注（P1）

- 验收标准：会议 roles=[产品,技术,红队] → 三条不同 role 消息；同一 RP-id 只有一个终态裁决（非会签）。
- 预估：0.3 人日。

### FR-18 会议级 agent 摘要（P1）

- 验收标准：开关打开后 `Discussion.review` 追加摘要段，点级字段不被改写。
- 预估：0.2 人日。

## 5. 非功能需求

- 性能：单文档提取 P95 < 30s（含 LLM 超时降级路径）；100 点评审页首屏 < 2s。
- 可靠性：LLM 不可用 / 超时不丢候选点，仅 LLM 字段为空；关会门控不因此失效。
- 不可变：候选全量 + TaskReview 均只追加不修改；每条裁决留 reviewer + 时间戳；每条状态迁移写审计日志（from/to/actor/timestamp）。
- 兼容性：不改 9 状态生命周期、DOC_KINDS、QUALITY_SPEC、stage gate；不重写 Markdown 解析。
- 可追溯：版本统一 `sha256:` 口径（与 ReadEvidence）；每条 DiscussionMessage 带 RP-id。
- 幂等：同版本重跑提取不重复入库；关会重复调用 409。
- 安全：本机信任域 127.0.0.1 沿用现有鉴权边界；写操作严格 404→所有权→状态 校验顺序；SQLite 参数化。

## 6. 验收与 DoD

- pytest 新增用例覆盖 FR-1~15 全绿，受影响回归套件零失败（2026-11-27 实跑 85 个评审相关用例全绿：FR-3/5/6/11 补齐后新增 14 测）。
- 实弹验证：以 `docs/taskhub/decision-entity/api.md` 回放 → 提取出 ≥1 个 RP 点 → Web 逐条裁决 6 点 → 关会生成五段 + TaskReview → `GET trace` 回溯。
- H5 pilot（2026-11-26 实跑）：5 份历史已评审文档盲回放，最终 Recall=22/49=44%（≥70% 通过 / 50-70% 半通过 / 30-50% 观察 / <30% 降级）；落 30-50% 观察区，价值叙事固定为"防遗漏 + 结构化留痕 + 逐条裁决"，不写"提前发现风险"，代码不回滚。
- 打包进 exe（PYZ 含新模块）并重启验证；MCP 新增工具在发版说明标注"重启宿主才可见"。

## 7. 已拍板决策（2026-11-26）

| # | 决策项 | 结论 |
|---|---|---|
| 1 | H5 门槛 | 主判定 Recall≥70% 通过 / 50-70% 半通过；<30% 降级为"结构化留痕"叙事 |
| 2 | review_id / decision_id | 提到 P0，不依赖单会议假设 |
| 3 | decisions≥2 计数 | 宽口径：采纳 / 修改 + 转 Decision 登记都计入 |
| 4 | 关会是否自动推进文档状态 | 不自动，由人走现有 stage gate |
| 5 | H5 样例集 | 历史已评审文档盲回放（如 2f0be5e7/review.md） |
| 6 | H5 实测结果 | 2026-11-26 实跑 5 样本，剔除 3 个弱命中后 22/49=44%；落 30-50% 观察区，价值叙事固定为"防遗漏 + 结构化留痕 + 逐条裁决" |

## 8. 风险与开放问题

- 历史评审记录不全（checklist 常空）→ pilot 真值标注成本高，人工补全 + 缩小样本。
- 历史缺文档版本 → 取评审日期前最近版本近似，标注误差。
- anchor 改版漂移 → 锚定章节 ID 而非行号，quote 兜底。
- LLM 成本 / 延迟 → 异步 + 缓存，超时降级不阻断。
- 双写入口冲突（会议 vs 手工 POST reviews）→ review 状态下会议为唯一推荐写入口，单 active 会议。
- "逐条"疲劳 → pilot 验证裁剪命中率，允许跳过后补评。
- 44% 命中率说明真问题多来自实现 / 评审阶段才浮现的工程判断（悲观锁、`@JsonProperty`、色值唯一来源等），源文档无文字线索；规则提取只命中"已写明的点"，不能替代人工判断。
