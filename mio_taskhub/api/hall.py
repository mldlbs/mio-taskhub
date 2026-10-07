"""Agent 任务大厅（只读）。

需求 FR-2：为「无人在线 / dispatcher 未运行」场景提供 Agent 主动发现任务的入口。

严格边界（2026-10-07 裁剪裁定）：
- **只读**：不写库、不留痕、不加价、不发事件——零副作用。
- **单次 SQL**：一条select 查出全部字段，不做 N+1。
- 只返回 `grab_mode=true` 且 `state=queued` + `stage=ready` 的任务。

不在此实现（需求证据不足，见 requirement.md §7 Future）：悬赏、匹配评分、
竞争人数、拒单、失约冷却。派单让位谓词在 background.py。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskStage, TaskState

router = APIRouter(prefix="/tasks", tags=["tasks"])

# 大厅默认/上限返回条数（FR-2：默认 ≤50 条，limit 上限 200）
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@router.get("/hall", response_model=dict)
def list_hall(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    target_agent_type: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    db: Session = Depends(get_session),
):
    """列出所有标记为主动领取（`grab_mode=true`）且待领的任务。

    只读接口：不产生任何副作用。Agent 选定后走**既有**
    `POST /tasks/claim?agent=<name>&task_id=<id>` 完成领取（FR-3），
    本接口不提供领取能力。
    """
    q = select(Task).where(
        Task.grab_mode == True,  # noqa: E712 —— SQLAlchemy 需用 == True 而非 is True
        Task.state == TaskState.QUEUED,
        Task.stage == TaskStage.READY,
    )
    # 过滤条件均为可选：空值表示不过滤。逗号分隔多值按 OR 语义。
    if target_agent_type:
        wanted = [x.strip() for x in target_agent_type.split(",") if x.strip()]
        if wanted:
            q = q.where(Task.target_agent_type.in_(wanted))
    if project:
        wanted = [x.strip() for x in project.split(",") if x.strip()]
        if wanted:
            q = q.where(Task.project.in_(wanted))

    # 单次 SQL 取回全部行，再在内存里排序（避免多次往返）。
    # 排序：priority desc → created_at asc（沿用既有 claim 的FIFO 语义，不引入新机制）。
    rows = db.exec(q).all()
    items = sorted(rows, key=lambda t: (-(t.priority or 0), t.created_at or t.id))

    total = len(items)
    limited = items[:limit]
    return {
        "total": total,
        "limit": limit,
        "items": [_hall_item(t) for t in limited],
    }


def _hall_item(t: Task) -> dict:
    """大厅条目。字段刻意保持最小集——诊断信息为主，不做加权评分。"""
    return {
        "id": t.id,
        "title": t.title,
        "description": (t.description or "")[:200],
        "target_agent_type": t.target_agent_type,
        "priority": t.priority or 0,
        "project": t.project or "",
        "labels": list(t.labels or []),
        "est_duration_min": t.est_duration_min or 0,
        "depends_on": list(t.depends_on or []),
        "acceptance_criteria": (t.acceptance_criteria or "")[:200],
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
