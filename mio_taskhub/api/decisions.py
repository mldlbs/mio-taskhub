# -*- coding: utf-8 -*-
"""Decision 实体 REST 端点（FR-02，任务 89a90af6）。

POST   /api/v1/decisions               创建（question 必填；options 为 [{label, note}]）
GET    /api/v1/decisions?status=&limit= 列表（默认 open，created_at 降序）
GET    /api/v1/decisions/{id}           详情
POST   /api/v1/decisions/{id}/resolve   裁决（choice 必填；终态再裁决 → 409）
POST   /api/v1/decisions/{id}/supersede 作废并指向后继决策

设计要点：
  - 只写 decisions 表，不触碰任务状态机（requirement FR-02 验收标准）。
  - 422 人话化（E7/P0-2 同口径）：参数错误带「你要做的是」。
  - 裁决不覆盖 question/options 原文——审计只增不改（requirement 非功能需求）。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Decision

router = APIRouter(prefix="/decisions", tags=["decisions"])

VALID_STATUS = ("open", "resolved", "superseded")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_deadline(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        raise HTTPException(422, "deadline 必须是 ISO 8601 时间串（如 2026-11-14T12:00）")


def _validate_options(options) -> list:
    """options 必须是 [{label, note}] 形态（note 可缺省）；其余形态 422。"""
    if options is None:
        return []
    if not isinstance(options, list):
        raise HTTPException(422, "options 必须是数组 [{label, note}]。"
                                 "你要做的是：传 JSON 数组，每项至少含 label 字符串")
    out = []
    for i, o in enumerate(options):
        if isinstance(o, str):
            out.append({"label": o, "note": ""})
            continue
        if not isinstance(o, dict) or not str(o.get("label") or "").strip():
            raise HTTPException(422, f"options[{i}] 缺 label。"
                                     "你要做的是：每项写成 {\"label\": \"方案A\", \"note\": \"理由\"}")
        out.append({"label": str(o["label"]).strip(), "note": str(o.get("note") or "")})
    return out


def _serialize(d: Decision) -> dict:
    return {
        "id": d.id, "question": d.question, "context": d.context,
        "options": d.options or [], "recommendation": d.recommendation,
        "status": d.status, "choice": d.choice, "rationale": d.rationale,
        "decider": d.decider, "deadline": d.deadline.isoformat() if d.deadline else None,
        "task_id": d.task_id, "successor_id": d.successor_id, "source": d.source,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
    }


@router.post("")
def create_decision(body: dict, db: Session = Depends(get_session)):
    question = str(body.get("question") or "").strip()
    if not question:
        raise HTTPException(422, "question is required. "
                                 "你要做的是：传「需要决策的问题」文本，如 "
                                 "{\"question\": \"前端走 A 还是 B\"}")
    options = _validate_options(body.get("options"))
    d = Decision(
        question=question,
        context=str(body.get("context") or ""),
        options=options or None,
        recommendation=str(body.get("recommendation") or ""),
        deadline=_parse_deadline(body.get("deadline")),
        task_id=str(body.get("task_id") or ""),
        source=str(body.get("source") or ""),
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return _serialize(d)


@router.get("")
def list_decisions(status: str = Query("open"), limit: int = Query(50, ge=1, le=200),
                   db: Session = Depends(get_session)):
    if status not in VALID_STATUS:
        raise HTTPException(422, f"status 必须是 {'/'.join(VALID_STATUS)}，收到 '{status}'")
    q = select(Decision).where(Decision.status == status)
    rows = db.exec(q.order_by(Decision.created_at.desc())).all()[:limit]
    return {"count": len(rows), "status": status, "items": [_serialize(r) for r in rows]}


@router.get("/{decision_id}")
def get_decision(decision_id: str, db: Session = Depends(get_session)):
    d = db.get(Decision, decision_id)
    if not d:
        raise HTTPException(404, f"decision {decision_id} 不存在")
    return _serialize(d)


@router.post("/{decision_id}/resolve")
def resolve_decision(decision_id: str, body: dict, db: Session = Depends(get_session)):
    d = db.get(Decision, decision_id)
    if not d:
        raise HTTPException(404, f"decision {decision_id} 不存在")
    if d.status != "open":
        raise HTTPException(409, f"decision 已是终态 '{d.status}'（choice={d.choice or '无'}），"
                                 f"不能重复裁决。你要做的是：查 GET /api/v1/decisions/{d.id} "
                                 "确认历史，或新开一条决策")
    choice = str(body.get("choice") or "").strip()
    if not choice:
        opts = [o["label"] for o in (d.options or [])]
        raise HTTPException(422, "choice is required. "
                                 f"你要做的是：从选项里挑一个或自由作答（可选：{ '、'.join(opts) or '无预设选项' }）")
    d.choice = choice
    d.rationale = str(body.get("rationale") or "")
    d.decider = str(body.get("decider") or "user")
    d.status = "resolved"
    d.resolved_at = _now()
    db.add(d)
    db.commit()
    db.refresh(d)
    return _serialize(d)


@router.post("/{decision_id}/supersede")
def supersede_decision(decision_id: str, body: dict, db: Session = Depends(get_session)):
    d = db.get(Decision, decision_id)
    if not d:
        raise HTTPException(404, f"decision {decision_id} 不存在")
    if d.status != "open":
        raise HTTPException(409, f"decision 已是终态 '{d.status}'，不能作废")
    successor = str(body.get("successor_id") or "")
    if successor and not db.get(Decision, successor):
        raise HTTPException(422, f"successor_id '{successor}' 不存在。"
                                 "你要做的是：先创建替代决策，或省略 successor_id 直接作废")
    d.status = "superseded"
    d.successor_id = successor
    d.resolved_at = _now()
    db.add(d)
    db.commit()
    db.refresh(d)
    return _serialize(d)
