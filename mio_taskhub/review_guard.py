# -*- coding: utf-8 -*-
"""P1-2：自审通道守卫 —— 同一执行者不得 approve 自己的任务。

背景（决策文档 `product-decision-20261020.md` §4 P1-2）：
> P1-2 | 删 self_review 通道 / 禁空 reviewer
> - 为什么先做：自审缺口让 "approved" 不可信，门禁形同虚设
> - 验收：同一 agent 自审恒 409，无绕过

实测（修复前，隔离库探测）确认 **6 条绕过路径**：

| # | 路径 | 修复前行为 |
|---|------|-----------|
| 1 | `POST /tasks/{id}/reviews` 传 `reviewer=""` | **200 放行**（`if reviewer and ...` 短路，空 reviewer 完全跳过自审检查） |
| 2 | `POST /tasks/{id}/reviews` 不传 `reviewer` 字段 | **200 放行**（同上） |
| 3 | `POST /tasks/{id}/reviews` 传 `self_review=true` | **200 放行**（显式绕过通道） |
| 4 | `POST /tasks/{id}/stage`（advance）传 `review_result` 文本进 done | **200 放行，且 TaskReview 记录数 = 0** —— 完全不经过 `reviews.py` |
| 5 | `POST /tasks/{id}/stage/move`（拖拽）传 `review_result` 文本进 done | **200 放行，且 TaskReview 记录数 = 0** |
| 6 | `POST /tasks/{id}/stage/move` 只登记 `doc_paths.review`（文件根本没写） | **200 放行**，review_result 被自动填成「见审查文档 xxx.md」—— 一条**看起来像审查结论的假记录** |

第 4/5 条最严重：它们连 `reviews.py` 都不经过，自审检查**根本不参与**，
且不产生任何 TaskReview 记录 —— 「approved」在 DB 里完全无痕。
第 6 条同样产出假审查记录（2026-10-10 审计 P0-2 只堵了文本路径，没堵文档路径）。

本模块提供**单一事实源**的判据，供 `reviews.py` 与 `task_stages.py` 共用：
- `last_executor(db, task_id)` → 最近一次执行者（无 run 返回 None）
- `is_self_approval(db, task_id, reviewer)` → 是否为自审
- `assert_reviewable(db, task_id, reviewer, decision)` → 总判据，返回 (allowed, detail)

设计原则（对齐决策 §0「宁可门是哑的，不能门是堵的」）：
- **只拦「明确的假完成」**：自审是明确的信任链自破 → 拦
- **fail-open 于无关情形**：任务没有执行者记录（纯人工任务）→ 放行
- **不拦 reject / comment**：只有 `approve` 才构成「独立验收」的信任主张
- **不拦他人评审**：reviewer 与执行者不同即放行
"""
from __future__ import annotations

from typing import Optional, Tuple

from sqlmodel import Session, select

from mio_taskhub.models import Run


# 自审错误码（前端按码渲染专用文案）
SELF_APPROVAL_CODE = "review.self_approval"
# 空 reviewer 错误码
EMPTY_REVIEWER_CODE = "review.reviewer_required"


def last_executor(db: Session, task_id: str) -> Optional[str]:
    """取该任务最近一次的执行者（run 的 agent_name）。

    没有 run 记录 → 返回 None（纯人工任务，非自审场景）。
    只看**最近一次**：与现有实现一致 —— 「这个任务最后是谁做的」是最贴近
    「自审」语义的判据。
    """
    r = db.exec(
        select(Run).where(Run.task_id == task_id)
        .order_by(Run.started_at.desc())
    ).first()
    return r.agent_name if r is not None else None


def is_self_approval(db: Session, task_id: str, reviewer: str) -> bool:
    """reviewer 是否就是本任务最近的执行者。

    - reviewer 为空 → False（由 `assert_reviewable` 单独拒空）
    - 任务无执行者记录 → False（纯人工任务，无自审概念）
    - reviewer == 最后执行者 → True
    """
    reviewer = (reviewer or "").strip()
    if not reviewer:
        return False
    executor = last_executor(db, task_id)
    if not executor:
        return False
    return executor.strip() == reviewer


def assert_reviewable(db: Session, task_id: str, reviewer: str,
                      decision: str = "approve") -> Tuple[bool, Optional[dict]]:
    """approve 前的自审/空 reviewer 校验。返回 (allowed, detail)。

    只对 `decision == "approve"` 生效：
    - reject / comment 不构成「独立验收」主张，不拦（否则会出现「无法拒绝
      自己写的垃圾」这种荒谬约束）。
    """
    if decision != "approve":
        return True, None

    reviewer = (reviewer or "").strip()
    if not reviewer:
        return False, {
            "code": EMPTY_REVIEWER_CODE,
            "message": ("approve 必须指定审阅人（reviewer）；"
                        "空审阅人无法证明验收是独立于执行者的"),
            "hint": "填入审阅人标识（人工审阅填你的名字/ID，Agent 审阅填 agent 名）",
        }

    executor = last_executor(db, task_id)
    if executor and executor.strip() == reviewer:
        return False, {
            "code": SELF_APPROVAL_CODE,
            "message": (f"审阅人 '{reviewer}' 是本任务最近的执行者，"
                        f"不能自我 approve；请由他人独立验收"),
            "reviewer": reviewer,
            "executor": executor,
            "hint": ("「独立验收」要求审阅人与执行者不同。"
                     "若确需自审，请改为 comment 记录，或由他人复核后再 approve"),
        }

    return True, None
