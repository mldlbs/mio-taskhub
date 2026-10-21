# P1-2 实施计划

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-10-21 09:48 |
| 状态 | approved |
| 负责人 | hermes |
| 适用范围 | 自审封堵 + 进入 done 证据门控的实施步骤 |

- 任务 ID：`a1aba9ef`
- Run ID：`4d977436`
- 分支：`task-a1aba9ef`
- 决策来源：`docs/taskhub/product-decision-20261020.md` §4 P1-2

## 1. 阶段划分

| 阶段 | 内容 | 完成条件 | 状态 |
|---|---|---|---|
| S0 | 审计绕过路径 | 在**生产代码**上实测确认全部绕过路径，记录 HTTP 码与 DB 副作用 | ✅ 完成 |
| S1 | 建共享判据模块 | `mio_taskhub/review_guard.py` 提供 `last_executor` / `is_self_approval` / `assert_reviewable` | ✅ 完成 |
| S2 | 接入提交入口 | `reviews.py` 删 self_review 分支、改用共享判据、禁空 reviewer | ✅ 完成 |
| S3 | 接入阶段门控 | `task_stages.py` 新增 `_check_review_evidence`，advance + move 两处调用 | ✅ 完成 |
| S4 | 前端配套 | `api.js` 错误码/文案提取；`ReviewPanel` 审阅人必填 + 内联错误条 | ✅ 完成 |
| S5 | 专项测试 | `tests/test_p1_2_no_self_review.py` 14 用例覆盖 6 绕过 + 4 合法路径 | ✅ 完成 |
| S6 | 回归修复 | 改写断言旧行为的既有测试（8 处） | ✅ 完成 |
| S7 | 全量验证 | 全量 pytest 绿 + 前端构建通过 | ✅ 完成 |
| S8 | 提交推送 | commit + push + 文档门禁 | 🔄 进行中 |

## 2. S0：审计绕过路径（已确认）

方法：在**当前生产代码**上用 TestClient 构造真实 `COMPLETED/REVIEW` 任务
（`submit_result` 成功后的真实状态），逐条尝试绕过。

> 前两次探针用错了初始任务状态（`QUEUED`），被状态机正常挡住 ——
> 说明状态机本身没漏，漏的是评审证据这一层。改用真实状态后 5 类绕过全部复现，
> 另在 S3 设计阶段发现第 6 类（只登记 review 路径）。

| # | 绕过路径 | 修复前实测 | 危害 |
|---|---|---|---|
| 1 | `POST /reviews` `reviewer=""` | **200** | 自审检查被短路，完全跳过 |
| 2 | `POST /reviews` 不传 `reviewer` | **200** | 同上 |
| 3 | `POST /reviews` `self_review=true` | **200** | 显式绕过开关 |
| 4 | `POST /stage` + `review_result` 文本 | **200 进 done，TaskReview=0** | 不经 reviews.py，approved 无痕 |
| 5 | `POST /stage/move` + `review_result` 文本 | **200 进 done，TaskReview=0** | 同上（拖拽路径） |
| 6 | `POST /stage/move` + 只登记 `doc_paths.review`（文件不存在） | **200 进 done，review_result="见审查文档 xxx.md"** | 假审查记录 |

## 3. S1：共享判据模块

新建 `mio_taskhub/review_guard.py`（109 行），三个纯函数：

```python
def last_executor(db, task_id) -> str | None
def is_self_approval(db, task_id, reviewer) -> bool
def assert_reviewable(db, task_id, reviewer, decision) -> tuple[bool, dict]
```

设计要点：
- **无副作用**（不写库）→ 可单测，可在任意入口复用。
- 只取**最近一次** run 的 agent（与 P0-4 原实现一致，避免接力任务误判）。
- `last_executor` 为 `None`（无 run）→ 判定为无自审概念，fail-open。

## 4. S2：接入提交入口（`api/reviews.py`）

1. 删掉内联自审判据（原 `if reviewer and not body.get("self_review")` 整块）。
2. 改调 `assert_reviewable(db, task_id, reviewer, decision)`。
3. `self_review` 字段不再被读取。
4. 被拒时不写 `review_result`、不建 TaskReview。

## 5. S3：接入阶段门控（`api/task_stages.py`）

1. 新增 `_check_review_evidence(db, t, dst, body)`。
2. `advance_stage` 与 `move_to_stage` 中在 `dst == DONE` 时调用。
3. `move_to_stage` 中把「无完成路径」的 409 判定**前置**到评审判据之前
   （否则 failed 任务会误报 422 evidence_missing，掩盖真实原因）。

## 6. S4：前端配套

| 文件 | 改动 |
|---|---|
| `web/src/api.js` | `ApiError` 读 `detail.code`（原先只读 `detail.error`）；文案取值链补 `detail.message` |
| `web/src/components/ReviewPanel.jsx` | approve 前校验审阅人非空（原先静默兜底 `'用户'`）；`alert` → 内联错误条（展示 message + hint） |
| `web/src/index.css` | 新增 `.rp__error` 系列样式，沿用既有 CSS 变量 |

## 7. S5：专项测试

`tests/test_p1_2_no_self_review.py` — 14 个用例：

| 用例 | 对应验收项 |
|---|---|
| `test_empty_reviewer_rejected` | AC-1 |
| `test_missing_reviewer_field_rejected` | AC-2 |
| `test_self_review_bypass_channel_removed` | AC-3 |
| `test_advance_to_done_requires_review_evidence` | AC-4 |
| `test_move_to_done_requires_review_evidence` | AC-5 |
| `test_done_requires_review_doc_actually_written` | AC-6 |
| `test_other_reviewer_approve_allowed` | AC-7 |
| `test_pure_human_task_allowed` | AC-8 |
| `test_reject_and_comment_not_blocked` | AC-9（参数化 ×2） |
| `test_force_still_skips_gate` | AC-10 |
| `test_blocked_submit_leaves_no_review_record` | AC-11 |
| `test_review_guard_last_executor_none_for_no_run` | FR-4 |
| `test_review_guard_is_self_approval` | FR-4 |

## 8. S6：回归修复（8 处既有测试）

既有测试断言的是**被本任务消灭的旧行为**，逐条改写并注明原语义：

| 文件 | 用例 | 改动 |
|---|---|---|
| `test_review_gates.py` | `test_self_review_allowed_with_explicit_flag` | 语义反转 → `test_self_review_bypass_channel_removed`（409） |
| `test_review_gates.py` | （新增）`test_empty_reviewer_rejected` | 覆盖空 reviewer |
| `test_stage.py` | `test_advance_to_done_with_review` | 先提交 approve 记录再进 done |
| `test_stage.py` | （新增）`test_advance_to_done_requires_review_evidence` | 纯文本 → 422 evidence_missing |
| `test_stage_move.py` | `test_move_to_done_with_review_result` | 先 `_approve()` 再进 done |
| `test_stage_move.py` | （新增）`test_move_to_done_requires_review_evidence` | 纯文本 → 422 |
| `test_stage_move.py` | `test_move_done_from_ready_via_review` | 加 `_approve()` |
| `test_stage_move.py` | `test_move_done_from_implementing` | 加 `_approve()` |
| `test_stage_move.py` | `test_move_done_from_completed_implementing` | 加 `_approve()` |

## 9. S7：全量验证

```bash
MIO_TASKHUB_DB="E:/hermes/cache/scratch/p12_full2.db" .venv/Scripts/python.exe -m pytest tests/ -q --tb=line
cd web && npm run build
```

**注意**：`tests/test_readme_stats.py::test_check_passes` 校验 README 统计值与
实际代码一致。新增 14 个测试函数 + 1 个新模块会使统计漂移 → 需重新生成 README：

```bash
.venv/Scripts/python.exe scripts/gen_readme_stats.py
```

## 10. S8：提交与推送

1. `git add` 相关文件（**不**包含 `.workbuddy/`、`AGENTS.md`、`CLAUDE.md`
   等未跟踪的无关文件）。
2. commit message 遵循仓库现有规范：`fix(P1-2): ...`。
3. 推进文档状态到 approved（`requirement` / `spec` / `api` / `test` / `plan`），
   否则 pre-push 门禁会拒绝。
4. `git push -u origin task-a1aba9ef`。

## 11. 风险与回退

| 风险 | 回退方式 |
|---|---|
| 门控误伤正常流程 | `force=true` 通道保留；纯人工任务 fail-open；已进 done 的历史任务不受影响 |
| 判据有遗漏 | `review_guard` 单一事实源 + 6 条路径专项用例；新增入口时必须走共享守卫 |
| 改坏既有流程 | 全量 pytest + 前端构建双重验证；分步 commit 便于回退 |

## 12. 停止条件

本任务完成即停。不在本任务内做：

- 评审角色/权限体系改造（超出范围）
- MCP 侧任务评审工具建设（当前任务评审无 MCP 入口，HTTP 是唯一路径）
- 历史数据清理（`taskreview` 实测 0 条，无数据可清）
- 冒名顶替防护（需要身份体系，超出范围）
