from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.notifications import ws_manager
from mio_taskhub.models import Discussion, DiscussionMessage, Idea, Task
from mio_taskhub.utils import _now

router = APIRouter(prefix="/discussions", tags=["discussions"])


def _broadcast(discussion_id: str):
    import asyncio
    try:
        asyncio.run(ws_manager.broadcast({"type": "discussion_update", "discussion_id": discussion_id}))
    except Exception:
        pass


def _msg_json(m: DiscussionMessage) -> dict:
    return {"author": m.author, "role": m.role, "content": m.content, "at": m.at.isoformat()}


def _disc_full(d: Discussion, db: Session) -> dict:
    msgs = db.exec(select(DiscussionMessage).where(DiscussionMessage.discussion_id == d.id).order_by(DiscussionMessage.at)).all()
    return {
        "id": d.id, "task_id": d.task_id, "idea_id": d.idea_id, "topic": d.topic,
        "agent": d.agent, "status": d.status, "summary": d.summary, "conclusions": d.conclusions,
        "stage": d.stage, "started_at": d.started_at.isoformat(),
        "ended_at": d.ended_at.isoformat() if d.ended_at else None,
        "messages": [_msg_json(m) for m in msgs],
    }


@router.post("", response_model=dict)
def create_discussion(body: dict, db: Session = Depends(get_session)):
    idea_id = body.get("idea_id", "") or ""
    task_id = body.get("task_id", "") or ""
    if not (idea_id or task_id):
        raise HTTPException(422, "idea_id or task_id is required")
    if idea_id and not db.get(Idea, idea_id):
        raise HTTPException(404, "idea not found")
    if task_id and not db.get(Task, task_id):
        raise HTTPException(404, "task not found")
    conclusions = body.get("conclusions", "")
    status = "closed" if conclusions else "open"
    d = Discussion(
        task_id=task_id, idea_id=idea_id,
        topic=body.get("topic", ""), agent=body.get("agent", ""),
        status=status, summary=body.get("summary", ""), conclusions=conclusions,
        stage=body.get("stage", "brainstorming"),
        ended_at=_now() if status == "closed" else None,
    )
    db.add(d); db.commit(); db.refresh(d)
    for m in body.get("messages", []):
        db.add(DiscussionMessage(discussion_id=d.id, author=m.get("author", ""),
                                 role=m.get("role", "user"), content=m.get("content", "")))
    db.commit()
    if task_id:
        try:
            from mio_taskhub.api.tasks import _broadcast_task_update
            _broadcast_task_update(task_id)
        except Exception:
            pass
    _broadcast(d.id)
    return _disc_full(d, db)


@router.get("")
def list_discussions(ref_type: str = None, ref_id: str = "", db: Session = Depends(get_session)):
    if ref_type == "idea":
        q = select(Discussion).where(Discussion.idea_id == ref_id)
    elif ref_type == "task":
        q = select(Discussion).where(Discussion.task_id == ref_id)
    else:
        q = select(Discussion)
    rows = db.exec(q.order_by(Discussion.started_at.desc())).all()
    return {"count": len(rows), "discussions": [_disc_full(d, db) for d in rows]}


@router.get("/{discussion_id}")
def get_discussion(discussion_id: str, db: Session = Depends(get_session)):
    d = db.get(Discussion, discussion_id)
    if not d:
        raise HTTPException(404, "discussion not found")
    return _disc_full(d, db)


@router.post("/{discussion_id}/messages")
def add_message(discussion_id: str, body: dict, db: Session = Depends(get_session)):
    d = db.get(Discussion, discussion_id)
    if not d:
        raise HTTPException(404, "discussion not found")
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(422, "content is required")
    m = DiscussionMessage(discussion_id=discussion_id,
                          author=body.get("author", ""),
                          role=body.get("role", "user"),
                          content=content)
    db.add(m); db.commit(); db.refresh(m)
    if d.task_id:
        try:
            from mio_taskhub.api.tasks import _broadcast_task_update
            _broadcast_task_update(d.task_id)
        except Exception:
            pass
    _broadcast(discussion_id)
    return _msg_json(m)


@router.post("/{discussion_id}/close")
def close_discussion(discussion_id: str, body: dict, db: Session = Depends(get_session)):
    d = db.get(Discussion, discussion_id)
    if not d:
        raise HTTPException(404, "discussion not found")
    d.summary = body.get("summary", d.summary)
    d.conclusions = body.get("conclusions", d.conclusions)
    d.status = "closed"
    d.ended_at = _now()
    db.add(d); db.commit(); db.refresh(d)
    if d.task_id:
        try:
            from mio_taskhub.api.tasks import _broadcast_task_update
            _broadcast_task_update(d.task_id)
        except Exception:
            pass
    _broadcast(d.id)
    return _disc_full(d, db)