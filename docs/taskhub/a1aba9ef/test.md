# P1-2 测试与验收

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 最后更新 | 2026-10-21 09:51 |
| 状态 | passed |
| 负责人 | hermes |
| 适用范围 | 自审封堵与进入 done 证据门控的验证 |

## 1. 验收标准

| # | 验收项 | 期望 | 结果 |
|---|---|---|---|
| AC-1 | reviewer 传空 → 409 review.reviewer_required | 409 | ✅ |
| AC-2 | 不传 reviewer 字段 → 409 | 409 | ✅ |
| AC-3 | self_review=true + reviewer==执行者 → 409 review.self_approval | 409 | ✅ |
| AC-4 | advance + review_result 纯文本 → 422 review.evidence_missing | 422 | ✅ |
| AC-5 | move + review_result 纯文本 → 422 | 422 | ✅ |
| AC-6 | 只登记 review 路径（文件不存在）→ 422 | 422 | ✅ |
| AC-7 | 他人 approve → 200 且可进 done | 200 | ✅ |
| AC-8 | 纯人工任务（无 run）→ 200 且可进 done | 200 | ✅ |
| AC-9 | reject / comment 自审 → 200（不拦） | 200 | ✅ |
| AC-10 | force=true → 200 | 200 | ✅ |
| AC-11 | 被拒的 approve 不留 TaskReview 记录 | 0 条 | ✅ |
| AC-12 | failed 任务 move→done → 409（非 422） | 409 | ✅ |
| AC-13 | 专项测试全绿 + 全量无回归 | — | ✅ |

## 2. 测试范围

### 覆盖

- 后端评审提交入口（`POST /reviews`）的 approve 判据：空 reviewer、缺省 reviewer、
  自审、`self_review` 绕过开关。
- 后端阶段门控（`POST /stage` / `POST /stage/move`）进入 done 的评审证据要求：
  纯文本、假文档路径、他人 approve 记录、实际落盘文档。
- 判据本身的单元行为（`review_guard` 三个纯函数）。
- 既有评审/阶段测试的回归。

### 不覆盖

- **不覆盖**前端交互的自动化测试 —— 本仓库前端无测试框架，前端改动靠
  `npm run build` + 代码走查验证。
- **不覆盖**并发场景下的自审竞态（两人同时 approve）—— 判据是读时判定，
  并发下最坏结果是两条 approve 记录，不影响「approved 可信」这个目标。
- **不覆盖**生产环境端到端 —— 本次不部署生产，仅验证代码与测试。
- **不覆盖**冒名顶替（用他人名字提交）—— 需要身份体系，超出范围。

## 3. 用例清单

> 追溯列直接引用 requirement 的 FR-n 编号，供 `traceability` 交叉校验。

| # | 用例 | 类型 | 断言要点 | FR | 对应 AC |
|---|---|---|---|---|---|
| 1 | `test_empty_reviewer_rejected` | 集成 | 409 + `code=review.reviewer_required` | FR-1 | AC-1 |
| 2 | `test_missing_reviewer_field_rejected` | 集成 | 409 + code 一致 | FR-1 | AC-2 |
| 3 | `test_self_review_bypass_channel_removed` | 集成 | 409 + `code=review.self_approval` + `executor=opencode` | FR-2 | AC-3 |
| 4 | `test_advance_to_done_requires_review_evidence` | 集成 | 422 + code + **TaskReview 数=0** | FR-3 | AC-4 |
| 5 | `test_move_to_done_requires_review_evidence` | 集成 | 422 + code + TaskReview 数=0 | FR-3 | AC-5 |
| 6 | `test_done_requires_review_doc_actually_written` | 集成 | 422 + code + TaskReview 数=0 | FR-3 | AC-6 |
| 7 | `test_other_reviewer_approve_allowed` | 集成 | 200 → move done 200 + `stage=done` | FR-3 | AC-7 |
| 8 | `test_pure_human_task_allowed` | 集成 | 200 → move done 200（无 run） | FR-5 | AC-8 |
| 9 | `test_reject_and_comment_not_blocked[reject]` | 集成 | 200 | FR-5 | AC-9 |
| 10 | `test_reject_and_comment_not_blocked[comment]` | 集成 | 200 | FR-5 | AC-9 |
| 11 | `test_force_still_skips_gate` | 集成 | 200 + stage=done | FR-5 | AC-10 |
| 12 | `test_blocked_submit_leaves_no_review_record` | 集成 | TaskReview 数=0 | FR-4 | AC-11 |
| 13 | `test_review_guard_last_executor_none_for_no_run` | 单元 | `last_executor` → None | FR-4, FR-5 | — |
| 14 | `test_review_guard_is_self_approval` | 单元 | True/False/False 三分支 | FR-4 | — |
| 15 | `test_move_done_from_failed_409_not_500` | 集成（既有） | 409 + detail 含 "done" | FR-6 | AC-12 |
| 16 | `test_move_to_done_requires_review_result` | 集成（既有） | 422 + review_result 保持空 | FR-3 | — |
| 17 | `test_self_review_bypass_channel_removed`（review_gates） | 集成 | 409 + reviews 列表为空 | FR-2 | AC-3 |
| 18 | `test_empty_reviewer_rejected`（review_gates） | 集成 | 409 ×2 变体 | FR-1 | AC-1/2 |
| 19 | `test_advance_to_done_requires_review_evidence`（stage） | 集成 | 422 + code | FR-3 | AC-4 |
| 20 | `test_advance_to_done_with_review`（stage） | 集成 | approve 后 → 200 | FR-3 | AC-7 |
| 21 | `test_move_to_done_requires_review_evidence`（stage_move） | 集成 | 422 + code | FR-3 | AC-5 |
| 22 | `test_move_to_done_with_review_result`（stage_move） | 集成 | approve 后 → 200 | FR-3 | AC-7 |
| 23 | `test_move_done_from_ready_via_review`（stage_move） | 集成 | approve 后 → 200 | FR-3 | AC-7 |
| 24 | `test_move_done_from_implementing`（stage_move） | 集成 | approve 后 → 200 | FR-3 | AC-7 |
| 25 | `test_move_done_from_completed_implementing`（stage_move） | 集成 | approve 后 → 200 | FR-3 | AC-7 |
| 26 | `test_done_gate_still_accepts_review_text`（task_doc_paths） | 集成 | 422 + code + TaskReview=0 | FR-3 | — |
| 27 | `test_done_gate_accepts_real_review_record`（task_doc_paths） | 集成 | approve 后 → 200 | FR-3 | — |
| 28 | `test_move_to_done_closes_active_run`（terminal_closes_run） | 集成 | approve 后 → 200 + run 关闭 | FR-3 | — |

**FR 覆盖对照**：

| FR | 需求 | 覆盖用例 |
|---|---|---|
| FR-1 | 禁空 reviewer | #1, #2, #18 |
| FR-2 | 删 self_review 通道 | #3, #17 |
| FR-3 | 进 done 需真实评审证据 | #4, #5, #6, #7, #16, #19~#28 |
| FR-4 | 判据单一事实源 | #12, #13, #14 |
| FR-5 | fail-open 例外 | #8, #9, #10, #11, #13 |
| FR-6 | 状态机判据优先 | #15 |

### 关键断言（不止状态码，还校验副作用）

```python
# 拦截路径不得留下假证据
assert len(_reviews("tc4")) == 0
assert client.get(f"/api/v1/tasks/{tid}/reviews").json() == []

# 错误码必须明确，不能只给状态码
assert r.json()["detail"]["code"] == "review.reviewer_required"
assert r.json()["detail"]["code"] == "review.self_approval"
assert r.json()["detail"]["code"] == "review.evidence_missing"
```

## 4. 执行结果

### 4.1 专项测试

```bash
MIO_TASKHUB_DB="E:/hermes/cache/scratch/p12_test.db" .venv/Scripts/python.exe \
  -m pytest tests/test_p1_2_no_self_review.py -v
```

**结果：14 passed**（2.13s）。

### 4.2 评审/阶段回归

```bash
MIO_TASKHUB_DB="E:/hermes/cache/scratch/p12_reg2.db" .venv/Scripts/python.exe \
  -m pytest tests/test_stage.py tests/test_stage_move.py tests/test_review_gates.py \
              tests/test_review_mode.py tests/test_p1_2_no_self_review.py -q
```

**结果：67 passed**（159.88s，0 failed）。

### 4.3 全量测试

```bash
MIO_TASKHUB_DB="E:/hermes/cache/scratch/p12_full2.db" .venv/Scripts/python.exe \
  -m pytest tests/ -q --tb=line
```

**结果：见 §5**。

### 4.4 前端构建

```bash
cd web && npm run build
```

**结果：built in 3.81s**（273 modules transformed，0 error）。

## 5. 回归修复说明（11 处）

既有测试断言的是**本任务要消灭的旧行为**。逐条改写并注明原语义 ——
这些不是「为了让测试变绿而改测试」，而是行为变更的显式记录：

| 文件 | 用例 | 改法 |
|---|---|---|
| `test_review_gates.py` | `test_self_review_allowed_with_explicit_flag` | → `test_self_review_bypass_channel_removed`，断言 409 + 0 条 review 记录 |
| `test_review_gates.py` | （新增）`test_empty_reviewer_rejected` | 覆盖 reviewer 空与缺省两种 |
| `test_stage.py` | `test_advance_to_done_with_review` | 先 `POST /reviews` approve，再进 done |
| `test_stage.py` | （新增）`test_advance_to_done_requires_review_evidence` | 纯文本 → 422 evidence_missing |
| `test_stage_move.py` | `test_move_to_done_with_review_result` | 先 `_approve()` 再进 done |
| `test_stage_move.py` | （新增）`test_move_to_done_requires_review_evidence` | 纯文本 → 422 |
| `test_stage_move.py` | `test_move_done_from_ready_via_review` | 加 `_approve()` |
| `test_stage_move.py` | `test_move_done_from_implementing` | 加 `_approve()` |
| `test_stage_move.py` | `test_move_done_from_completed_implementing` | 加 `_approve()` |
| `test_task_doc_paths.py` | `test_done_gate_still_accepts_review_text` | 语义反转 → 422 evidence_missing；新增 `test_done_gate_accepts_real_review_record` |
| `test_terminal_closes_run.py` | `test_move_to_done_closes_active_run` | move→done 前补真实 approve 记录 |

**未改动的**：`test_move_done_from_failed_409_not_500`（期望 409）——
通过把「无完成路径」判定前置到评审判据之前来满足，而非改测试期望。

## 6. 已知未修复项

| 项 | 性质 | 说明 |
|---|---|---|
| `events.py:83` `WSManager.broadcast was never awaited` | 既有的 RuntimeWarning | 非本次改动引入，与 P1-1/P1-2 语义无关 |
| README 统计漂移 | 已处理 | 新增 14 测试 + 1 模块后需 `scripts/gen_readme_stats.py` 重生成 |
