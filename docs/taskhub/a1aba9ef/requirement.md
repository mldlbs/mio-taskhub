# P1-2 需求规格：删除 self_review 通道，禁空 reviewer，禁止无评审证据进入 done

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-10-21 09:48 |
| 状态 | approved |
| 负责人 | hermes |
| 适用范围 | 任务评审 approve 判据 + 进入 done 的评审证据门控 |
| 任务 ID | `a1aba9ef` |
| 决策来源 | `docs/taskhub/product-decision-20261020.md` §4 P1-2 |

## 背景与问题

mio-taskhub 的核心价值主张是「强约束的任务管理机制提高 AI 编程任务的交付可靠性」。
其中「独立验收」是关键一环 —— 任务完成（`approved` / `done`）应意味着
**有人独立检查过产出物**，而不是执行者自己宣布完成。

2026-10-10 审计 P0-4 已在 `POST /api/v1/tasks/{id}/reviews` 这一条路径上加了
「评审人 ≠ 执行者」检查，但**其余入口未被覆盖**，且该路径自身存在两处逻辑漏洞。

2026-10-21 在生产代码上用隔离库实测确认 **6 条绕过路径**（详见 `spec.md` §2），
其中 4 条可让「approved」在数据库中**完全无痕** —— 既无 TaskReview 记录，
`review_result` 也是一句自说自话的文本。事后无法区分「真审过」与「自己写了句话」。

具体痛点（真实场景，非假设）：

- 执行 agent 提交 `POST /stage` 带 `review_result: "ok"` → 直接进 `done`，
  任务看板显示「已完成」，但审阅历史为空；
- 审阅人字段传空 → 2026-10-10 的自审检查因 `if reviewer and ...` 短路被完全跳过；
- `self_review=true` 是公开的绕过开关。

**为什么现在做**：这是 P1-1 之后的前置止血项。口径修好后如果「approved」仍可自审，
那 P1-1 修出来的成功率数字依然不可信 —— 门禁形同虚设。

## 目标与非目标

### 目标

1. 同一执行者（agent）**恒不能** approve 自己执行的任务，无任何绕过开关。
2. 审阅人**不可为空** —— 无法证明验收独立于执行者的评审，不构成评审。
3. 进入 `done` 阶段**必须**有真实评审证据（approve 记录 或 实际落盘的 review 文档）。
4. 所有入口走**同一套判据**（单一事实源），避免两处判据漂移。
5. 合法流程**不被误伤**：他人验收、纯人工任务、reject/comment、force 紧急通道。

### 非目标

- 不引入新的评审角色/权限体系（超出 P1-2 范围）。
- 不改造 `taskhub_submit_review`（那是**想法**评审工具，与任务评审无关）。
- 不清理历史自审记录 —— 生产库 `taskreview` 表实测 **0 条**，无历史数据可清。
- 不改变 `reject` / `comment` 的语义（自审不构成「验收通过」主张，理应允许）。
- 不做评审文档质量的系统性改造（超出本任务范围）。
- 不做冒名顶替防护（用他人名字提交需要真正的身份体系，超出范围）。

## 用户故事

| # | 角色 | 故事 | 期望 |
|---|---|---|---|
| US-1 | 执行 agent | 我完成了任务，想标记完成 | 系统要求先有他人 approve，我不能自己批 |
| US-2 | 人工评审者 | 我审完了，想 approve | 填写我的名字 → 200，落一条 TaskReview |
| US-3 | 人类用户 | 我手工建的任务，没人跑过 | 我能正常 approve 并进 done（不因「无 agent」被卡） |
| US-4 | 值班运维 | 线上卡住了，需要紧急放行 | 带 `force=true` 可跳过，事件留痕 |
| US-5 | 事后审计者 | 想知道这个任务到底谁审的 | 能从 TaskReview 查到审阅人，且审阅人 ≠ 执行者 |

## 功能需求

| 编号 | 优先级 | 需求描述 | 验收标准 |
|---|---|---|---|
| FR-1 | P0 | `POST /reviews` 在 `decision='approve'` 时，`reviewer` 为空字符串或**完全缺省** → 一律拒绝 | 409 `review.reviewer_required` |
| FR-2 | P0 | `decision='approve'` 且 reviewer == 本任务最近的执行者 → 恒拒绝，**忽略** `self_review` 字段 | 409 `review.self_approval` |
| FR-3 | P0 | `POST /stage`（advance）与 `POST /stage/move`（拖拽）在目标阶段为 `done` 时，必须存在：① `decision='approve'` 的 TaskReview 记录且其 reviewer **不是**执行者，或 ② **实际落盘**（文件存在且非空）的 review 文档 | 否则 422 `review.evidence_missing` |
| FR-4 | P0 | 上述判据集中在 `mio_taskhub/review_guard.py`，由 `reviews.py` 与 `task_stages.py` **共用** | 判据只有一处实现；新增入口必须走共享守卫 |
| FR-5 | P1 | fail-open 例外：① 任务无任何执行者记录（纯人工任务）→ 放行；② `decision` 为 `reject`/`comment` → 放行；③ 请求带 `force=true` → 放行并留痕 | 三种情形均返回 200（见 `test.md` 用例清单） |
| FR-6 | P1 | `failed`/`retrying` 任务移动到 `done` → 返回 **409**（状态机无完成路径），**不是** 422 `review.evidence_missing` | 409，且 detail 说明「无完成路径」 |

> FR-1 根因：原实现 `if reviewer and not body.get("self_review")` 在 `reviewer` 为空时
> **短路**，整个自审检查被跳过。
>
> FR-2 根因：原实现 `not body.get("self_review")` 提供了显式放行开关。
>
> FR-3 根因：原实现 `review_result` 纯文本即可解锁；只登记 review 文档路径
> （文件根本没写）会把 review_result 自动填成「见审查文档 xxx.md」——
> 一条**看起来像审查结论的假记录**。

## 非功能需求

### 性能

- 判据只查最近一次 run + 该任务的 approve 记录 → 单次请求内 2 条索引查询，
  P99 延迟增量 < 5ms（相对既有 `/reviews` 处理时间 < 10%）。
- 不得在请求路径上做全表扫描或文件递归查找（review 文档检查只解析单条路径）。

### 安全与信任链

- 拦截路径**不得**产生任何副作用：不写 TaskReview、不写 `review_result`、
  不推进任务状态/阶段（不产生假证据）。
- 审阅人身份必须可审计：`TaskReview.reviewer` 记录真实提交者，
  不接受静默兜底（如原先前端兜底 `'用户'`）。
- 错误响应须含 `code` + `message` + `hint`，前端能直接展示说明而非 JSON 串。

### 一致性与幂等

- 重复提交同一评审不改变判据结果（判据为纯函数，无状态）。
- 判据集中实现，`reviews.py` 与 `task_stages.py` 不允许各自复制判据逻辑。

### 兼容性与可回退

- `force=true` 紧急通道保留，与既有门控语义一致（写
  `task_stage_gate_forced` 事件留痕）。
- 门控只在**进入** done 时生效 → 已进 done 的历史任务不受影响，无需数据迁移。
- 生产库 `taskreview` 实测 0 条 → 无历史自审记录需清理，不做数据迁移。

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 真实流程中大量任务缺独立评审人，可能卡住 | 交付流程变慢 | `force=true` 紧急通道保留；纯人工任务 fail-open |
| 绕过路径分布在多处，修一处漏一处 | 门控形同虚设 | 判据集中到 `review_guard`；专项测试覆盖 6 条路径 |
| 前端静默兜底 reviewer='用户' 掩盖问题 | 「谁审的」永远查不到 | 前端改为必填校验 + 展示后端业务错误码 |
| 状态机拒绝被伪装成证据缺失 | 排查方向被误导 | FR-6：状态机判据前置 |

## 追溯矩阵

| 需求 | 实现 | 测试 |
|---|---|---|
| FR-1 | `review_guard.assert_reviewable` + `reviews.py` | `test_empty_reviewer_rejected`、`test_missing_reviewer_field_rejected`、`test_review_gates.py::test_empty_reviewer_rejected` |
| FR-2 | `review_guard.assert_reviewable` | `test_self_review_bypass_channel_removed`（+ review_gates 同名用例） |
| FR-3 | `task_stages._check_review_evidence` | `test_advance_to_done_requires_review_evidence`、`test_move_to_done_requires_review_evidence`、`test_done_requires_review_doc_actually_written` |
| FR-4 | `mio_taskhub/review_guard.py` | `test_review_guard_last_executor_none_for_no_run`、`test_review_guard_is_self_approval` |
| FR-5 | `review_guard` / `_check_review_evidence` | `test_pure_human_task_allowed`、`test_reject_and_comment_not_blocked`、`test_force_still_skips_gate` |
| FR-6 | `task_stages.move_to_stage` 前置状态判定 | `test_move_done_from_failed_409_not_500` |
