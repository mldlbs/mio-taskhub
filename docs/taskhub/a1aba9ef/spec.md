# P1-2 技术设计：禁自审通道

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-10-21 09:51 |
| 状态 | approved |
| 负责人 | hermes |
| 适用范围 | 任务评审（approve）判据与进入 done 的评审证据门控 |


- 任务 ID：`a1aba9ef`
- 决策来源：`docs/taskhub/product-decision-20261020.md` §4 P1-2
- 日期：2026-10-21

## 1. 设计目标

用一个**共享判据模块**封住全部 6 条绕过路径，且不误伤合法流程。

核心约束（来自需求的 FR-4）：**判据必须是单一事实源**。此前 P0-4 只在
`reviews.py` 内联判据，`task_stages.py` 完全不知道「独立验收」的存在 ——
这正是绕过路径 4/5/6 长期存在的原因。

## 2. 现状：6 条绕过路径（2026-10-21 实测确认）

| # | 路径 | 修复前行为 | 根因位置 |
|---|---|---|---|
| 1 | `POST /reviews` `reviewer=""` | **200 放行** | `reviews.py`：`if reviewer and not self_review` 短路 |
| 2 | `POST /reviews` 不传 `reviewer` | **200 放行** | 同上 |
| 3 | `POST /reviews` `self_review=true` | **200 放行** | `reviews.py`：`not body.get("self_review")` |
| 4 | `POST /stage` + `review_result` 文本 | **200 进 done，TaskReview=0** | `task_stages.py` 不检查评审证据 |
| 5 | `POST /stage/move` + `review_result` 文本 | **200 进 done，TaskReview=0** | 同上（拖拽路径） |
| 6 | `POST /stage/move` + 只登记 `doc_paths.review`（文件不存在） | **200 进 done，review_result="见审查文档 xxx.md"** | `_apply_stage_requirements` 自动填充 |

路径 4/5/6 的危害最大：它们**根本不经过 `reviews.py`**，因此 P0-4 的自审检查
**完全不参与**。结果是「approved」在 DB 里无痕 —— 审阅历史为空，
`review_result` 只是一句自说自话的文本。事后无法区分「真审过」与「自己写了句话」。

## 3. 模块职责与边界

### 做什么

- 判定一次 `approve` 是否构成**独立验收**：审阅人非空，且审阅人 ≠ 本任务执行者。
- 判定进入 `done` 是否具备**真实评审证据**：存在他人 approve 记录，或存在实际落盘的 review 文档。
- 提供**单一事实源**判据，供提交入口（`reviews.py`）与阶段门控（`task_stages.py`）共用。

### 不做什么

- 不做评审角色/权限体系（谁能评审、几级评审）—— 超出 P1-2 范围。
- 不做评审**内容质量**判定（结论写得对不对）—— 只判「有没有人独立审过」。
- 不做历史数据清理 —— `taskreview` 实测 0 条，无数据可清。
- 不做状态机改造 —— 阶段流转规则本身不变，只在流转前加一道证据检查。
- 不改 `taskhub_submit_review`（想法评审工具，与任务评审无关）。

### 边界模糊处的取舍

| 争议点 | 取舍 | 理由 |
|---|---|---|
| 纯文本 `review_result` 算不算评审证据？ | **不算** | 它是执行者自己写的一句话，事后无法区分「真审过」与「自己写了句话」 |
| 只登记 review 路径、文件没写，算不算？ | **不算** | 同上，且会产出「见审查文档 xxx.md」这种假结论 |
| `reject` / `comment` 自审算不算违规？ | **不算** | 自审不构成「验收通过」主张；否则会出现「无法拒绝自己写的垃圾」这种荒谬约束 |
| 任务没人跑过（纯人工）怎么办？ | **放行** | 无自审概念，fail-open 避免误伤人工流程 |
| 需要紧急放行怎么办？ | `force=true` | 门控防误操作，不防蓄意绕过；且 force 写事件留痕 |

## 4. 架构

```
                    ┌─────────────────────────────┐
                    │   mio_taskhub/review_guard  │  ← 单一事实源
                    │   (新，110 行)               │
                    │                             │
                    │   last_executor(db, task_id)│
                    │   is_self_approval(...)      │
                    │   assert_reviewable(...)     │
                    └──────────┬──────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
   ┌──────▼───────┐   ┌────────▼────────┐   ┌───────▼────────┐
   │ api/reviews.py│   │api/task_stages  │   │  前端          │
   │ 提交评审      │   │  .py            │   │ ReviewPanel    │
   │ (approve 入口)│   │ _check_review_  │   │ .jsx           │
   │              │   │   evidence()    │   │                │
   │              │   │  (进 done 门控) │   │                │
   └──────────────┘   └─────────────────┘   └────────────────┘
```

### 4.1 `mio_taskhub/review_guard.py`（新增）

三个纯函数，无副作用，便于单测：

| 函数 | 职责 |
|---|---|
| `last_executor(db, task_id)` | 取该任务**最近一次** run 的 `agent_name`；无 run 返回 `None` |
| `is_self_approval(db, task_id, reviewer)` | reviewer 与最后执行者相同 → True；reviewer 为空 → False |
| `assert_reviewable(db, task_id, reviewer, decision)` | approve 时的总判据，返回 `(allowed: bool, detail: dict)` |

`assert_reviewable` 判据：

```
if decision != "approve":  → allowed（reject/comment 不构成验收主张）
if not reviewer.strip():   → denied(review.reviewer_required)
executor = last_executor(...)
if executor and reviewer == executor:
                            → denied(review.self_approval)
otherwise:                 → allowed
```

**不比较历史所有 run，只取最近一次** —— 与 P0-4 原实现一致，避免「换 agent
接力」时误判。同时 `last_executor` 为 `None`（纯人工任务）时一律放行，
实现 fail-open。

### 4.2 `api/reviews.py`

- 删掉内联的自审判据，改为调用 `assert_reviewable`。
- `decision == "approve"` 时校验通过后，再走原有的 `summary_too_short` 规则
  （未登记 review 文档时 summary 至少 8 字符）。
- **不受理** `self_review` 字段（不再读取它）。

被拒时**不写** `review_result`、**不建** TaskReview 记录 —— 不产生假证据。

### 4.3 `api/task_stages.py` — `_check_review_evidence`

新函数，在 `advance_stage` 与 `move_to_stage` 中于 `dst == DONE` 时调用。

判据顺序：

```
if dst != DONE:              → return（不拦）
if body.get("force"):        → return（紧急通道，事件留痕）

approve_records = SELECT * FROM taskreview
                   WHERE task_id=? AND decision='approve'
executor = last_executor(...)

if approve_records:
    if not executor:                    → return（纯人工任务，fail-open）
    if 存在 reviewer != executor 的记录: → return（他人验收）
    raise 409 review.self_approval     （全是自审）

review_path = doc_paths["review"]
if review_path 且文件实际存在且非空:     → return（真实审查文档）

raise 422 review.evidence_missing
```

**为什么 review 文档必须真的落盘**：原实现允许「只登记路径」，
`review_result` 被自动填成「见审查文档 xxx.md」—— 这是一条看起来像审查结论的
假记录，事后无法从数据上区分真假。要求文件存在（`read_evidence.resolve_doc_path`
+ `is_file()` + `st_size > 0`）后，「登记了路径但没写文件」不再是有效证据。

**为什么 force 保留**：门控的目的是防误操作，不是防蓄意绕过；紧急场景
（如线上卡死）需要出口，且 `force` 会写 `task_stage_gate_forced` 事件留痕。

### 4.4 状态机判据优先（FR-6）

`move_to_stage` 中的判据顺序调整：

```
_apply_stage_requirements(...)         # 原有文档门槛
if dst == DONE and state not in (QUEUED, CLAIMED, COMPLETED):
    raise 409「无完成路径」             # ← 前置到评审判据之前
_check_review_evidence(...)            # P1-2 新增
```

理由：`failed` / `retrying` 任务没有完成路径，属**状态机层面**的拒绝（409）。
若先撞评审门控会返回 422 `review.evidence_missing`，把「状态机拒绝」伪装成
「缺评审证据」，掩盖真实原因、误导排查。

## 5. 前端改动

### 5.1 `web/src/api.js`

| 问题 | 修复 |
|---|---|
| `ApiError` 只读 `detail.error`，读不到 `detail.code` | 两处都读，`error_code` 优先 `error` 后 `code` |
| 错误文案取值链缺 `detail.message` 一档 | 补上：后端写的「请由他人独立验收后重试」这类说明不再被 JSON.stringify 成一行 |

取值优先级：`detail.message` → `detail.detail` → `detail.error` → `JSON.stringify(detail)`。

### 5.2 `web/src/components/ReviewPanel.jsx`

| 问题 | 修复 |
|---|---|
| `reviewer.trim() \|\| '用户'` 静默兜底 | approve 前**校验非空**，为空则提示不提交（静默兜底等于替用户伪造身份，且「谁审的」永远查不到） |
| `alert('提交失败: ' + e.message)` 丢弃 hint | 改为内联错误条，展示 `message` + `hint` |

### 5.3 `web/src/index.css`

新增 `.rp__error` / `.rp__error__icon` / `.rp__error__msg`，
沿用既有 CSS 变量（`--danger` / `--bg-2` / `--line-strong`），不引入新色值。

## 6. 数据迁移

**无**。生产库 `taskreview` 表实测 0 条，无历史自审记录需清理。
本任务只堵未来入口，不追溯历史。

## 7. 影响面

| 文件 | 改动 |
|---|---|
| `mio_taskhub/review_guard.py` | **新增** 110 行 |
| `mio_taskhub/api/reviews.py` | 内联判据 → 调用共享守卫；删 self_review 分支 |
| `mio_taskhub/api/task_stages.py` | 新增 `_check_review_evidence`；两处调用；判据顺序调整 |
| `web/src/api.js` | ApiError 错误码/文案提取 |
| `web/src/components/ReviewPanel.jsx` | 审阅人必填 + 内联错误条 |
| `web/src/index.css` | `.rp__error` 样式 |
| `tests/test_p1_2_no_self_review.py` | **新增** 14 用例 |
| `tests/test_review_gates.py` | 自审用例语义反转 + 新增空 reviewer 用例 |
| `tests/test_stage.py` | done 门控用例改为需真实评审证据 |
| `tests/test_stage_move.py` | 同上 |

## 8. 兼容性风险

| 风险 | 处置 |
|---|---|
| 既有测试断言旧行为（self_review 放行、纯文本进 done） | **有意变更** —— 旧行为正是被消灭的缺陷。已逐条改写并注明原语义 |
| 线上任务在 done 阶段被卡 | 任务状态不受影响（门控只在**进入** done 时生效），已进 done 的历史任务不受影响 |
| MCP 工具 | 任务评审**没有** MCP 工具（`taskhub_submit_review` 是想法评审），后端 HTTP API 是唯一入口 → 后端守卫即主防线，前端禁用只是辅助 |

## 9. 验证方式

见 `test.md`。核心：6 条绕过路径专项用例 + 既有评审/阶段测试回归 + 全量测试。

## 10. 详细设计

### 10.1 涉及文件清单

| 文件 | 状态 | 说明 |
|---|---|---|
| `mio_taskhub/review_guard.py` | **新增** | 共享判据（109 行） |
| `mio_taskhub/api/reviews.py` | 改 | 删内联判据 + self_review 分支；改调 `assert_reviewable` |
| `mio_taskhub/api/task_stages.py` | 改 | 新增 `_check_review_evidence`；advance/move 两处调用；判据顺序前置 |
| `web/src/api.js` | 改 | `ApiError` 读 `detail.code`；文案取值链补 `detail.message` |
| `web/src/components/ReviewPanel.jsx` | 改 | 审阅人必填；`alert` → 内联错误条 |
| `web/src/index.css` | 改 | 新增 `.rp__error` 系列 |
| `tests/test_p1_2_no_self_review.py` | **新增** | 14 用例 |
| `tests/test_review_gates.py` | 改 | 自审用例语义反转 + 新增空 reviewer 用例 |
| `tests/test_stage.py` | 改 | done 门控用例改为需真实评审证据 |
| `tests/test_stage_move.py` | 改 | 同上 |

### 10.2 `review_guard.py` 函数签名与行为

```python
def last_executor(db: Session, task_id: str) -> Optional[str]:
    """取该任务最近一次 run 的 agent_name；无 run 返回 None。"""
    r = db.exec(
        select(Run).where(Run.task_id == task_id)
        .order_by(Run.started_at.desc())
    ).first()
    return r.agent_name if r is not None else None


def is_self_approval(db: Session, task_id: str, reviewer: str) -> bool:
    reviewer = (reviewer or "").strip()
    if not reviewer:
        return False
    executor = last_executor(db, task_id)
    if not executor:
        return False
    return executor.strip() == reviewer


def assert_reviewable(db: Session, task_id: str, reviewer: str,
                      decision: str = "approve") -> Tuple[bool, Optional[dict]]:
    if decision != "approve":
        return True, None
    reviewer = (reviewer or "").strip()
    if not reviewer:
        return False, {"code": EMPTY_REVIEWER_CODE, "message": ..., "hint": ...}
    executor = last_executor(db, task_id)
    if executor and executor.strip() == reviewer:
        return False, {"code": SELF_APPROVAL_CODE, "message": ..., "reviewer": ..., "executor": ..., "hint": ...}
    return True, None
```

**为何只取最近一次 run**：与 P0-4 原实现一致。任务常由多个 agent 接力，
「这个任务最后是谁做的」最贴近「自审」语义；比较全部历史 run 会把
接力链条上的合法协作者误判为自审。

### 10.3 `_check_review_evidence` 时序

```
advance_stage / move_to_stage
  │
  ├─ 1. _apply_stage_requirements(t, dst, body[, strict])
  │      └─ done 阶段：填 review_result（文本或「见审查文档 xxx.md」）
  │
  ├─ 2. [move 专属] dst==DONE 且 state not in (QUEUED, CLAIMED, COMPLETED)
  │      └─ raise 409「无完成路径」          ← FR-6，必须早于第 3 步
  │
  ├─ 3. _check_review_evidence(db, t, dst, body)
  │      ├─ dst != DONE                → return
  │      ├─ body.get("force")          → return（留痕）
  │      ├─ approve_records 非空
  │      │    ├─ executor 为 None      → return（纯人工任务）
  │      │    ├─ 存在他人 approve      → return
  │      │    └─ 全是自审              → raise 409 review.self_approval
  │      ├─ review 文档实际落盘        → return
  │      └─ raise 422 review.evidence_missing
  │
  └─ 4. _check_lifecycle_gate(...) → 状态机流转 → 提交
```

### 10.4 状态机转换表（无变化）

| 源 | 目标 | 说明 |
|---|---|---|
| 任意 | `done` | **新增前置**：评审证据门控 |
| `failed`/`retrying` | `done` | 409（无完成路径）—— FR-6 |
| `queued`/`claimed` | `done` | 走 T5 → T6 两段转换 |
| `review` | `done` | T6 收尾 |

### 10.5 错误响应结构

```json
{
  "detail": {
    "code": "review.evidence_missing",
    "message": "进入 done 需要真实评审证据：既无 approve 评审记录，也无实际落盘的 review 文档；review_result 纯文本不足以构成独立验收",
    "has_approve_record": false,
    "has_review_doc": false,
    "hint": "先 POST /api/v1/tasks/{id}/reviews 提交 decision=approve 的评审记录（审阅人不得是本任务的执行者），或写入并登记 review 文档（doc_paths['review']，文件须实际存在）；紧急情况可带 force=true 跳过并留痕"
  }
}
```

前端读取：`detail.code`（业务码）或 `detail.error`（policy 码）；
文案优先 `detail.message`，其次 `detail.hint`。
