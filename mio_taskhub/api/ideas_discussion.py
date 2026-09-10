from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import Session, select
from sqlalchemy import func
from mio_taskhub.db import get_session
from mio_taskhub.models import (Idea, Discussion, DiscussionMessage, Task,
                                IdeaChange, IdeaHistory)
from mio_taskhub.api.ideas import _idea_json


router = APIRouter(prefix="/ideas", tags=["ideas"])


@router.get("/{idea_id}/history")
def get_idea_history(idea_id: str, page: int = 1, page_size: int = 50, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    total = db.exec(select(func.count(IdeaHistory.id)).where(IdeaHistory.idea_id == idea_id)).one()
    items = db.exec(
        select(IdeaHistory)
        .where(IdeaHistory.idea_id == idea_id)
        .order_by(IdeaHistory.at.desc(), IdeaHistory.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "items": [{
            "id": h.id,
            "kind": h.kind,
            "actor": h.actor,
            "content": h.content,
            "reasoning": h.reasoning,
            "extra": h.extra,
            "at": h.at.isoformat(),
        } for h in items]
    }


@router.get("/{idea_id}")
def get_idea(idea_id: str, include_changes: bool = Query(True),
             before_id: int = Query(None), limit: int = Query(20, ge=1, le=100),
             db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    out = _idea_json(i)
    rows = db.exec(select(Discussion).where(Discussion.idea_id == idea_id).order_by(Discussion.started_at)).all()
    ds = []
    for d in rows:
        msgs = db.exec(select(DiscussionMessage).where(DiscussionMessage.discussion_id == d.id).order_by(DiscussionMessage.at)).all()
        ds.append({
            "id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
            "summary": d.summary, "conclusions": d.conclusions, "stage": d.stage,
            "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None,
            "messages": [{"author": m.author, "role": m.role, "content": m.content,
                          "at": m.at.isoformat()} for m in msgs],
        })
    out["discussions"] = ds
    tasks = db.exec(select(Task).where(Task.idea_id == idea_id).order_by(Task.created_at)).all()
    out["tasks"] = [{"id": t.id, "title": t.title, "stage": t.stage.value,
                       "state": t.state.value} for t in tasks]
    if include_changes:
        q = select(IdeaChange).where(IdeaChange.idea_id == idea_id)
        if before_id is not None:
            q = q.where(IdeaChange.id < before_id)
        changes = db.exec(q.order_by(IdeaChange.id.desc()).limit(limit)).all()
        out["changes"] = [{
            "id": c.id, "version": c.version, "created_at": c.created_at.isoformat(),
            "diff": c.diff, "reason": c.reason,
        } for c in changes]
    return out