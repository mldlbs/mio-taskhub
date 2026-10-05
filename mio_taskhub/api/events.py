# mio_taskhub/api/events.py
from datetime import timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Event, Task, TaskEvent
from mio_taskhub.events import event_to_dict

router = APIRouter(prefix="/events", tags=["events"])
task_events_router = APIRouter(prefix="/tasks", tags=["tasks"])

DEFAULT_LIMIT = 200


@router.get("")
def list_events(after_seq: int = Query(None, ge=0), limit: int = Query(DEFAULT_LIMIT, ge=1, le=1000),
                db: Session = Depends(get_session)):
    """事件订阅：after_seq 不传返回最近 limit 条；=0 从 seq 1 起按 limit 分页；=N 返回 seq>N 的增量（按 limit 分页）。"""
    q = select(Event)
    if after_seq is not None and after_seq > 0:
        q = q.where(Event.id > after_seq)
    if after_seq is None:
        rows = db.exec(q.order_by(Event.id.desc()).limit(limit)).all()
        rows.reverse()
    else:
        rows = db.exec(q.order_by(Event.id.asc()).limit(limit)).all()
    events = [event_to_dict(e) for e in rows]
    return {
        "events": events,
        "next_seq": events[-1]["seq"] if events else 0,
    }


@task_events_router.get("/{task_id}/events")
def get_task_events(task_id: str, limit: int = 50, db: Session = Depends(get_session)):
    """返回任务的 M1 TaskEvent 时间线（状态变更记录）。"""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    events = db.exec(
        select(TaskEvent).where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.created_at.desc())
        .limit(limit)
    ).all()
    def _fmt(dt):
        if dt is None: return None
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return {
        "task_id": task_id,
        "events": [{
            "id": e.id,
            "event_type": e.event_type,
            "from_state": e.from_state,
            "from_stage": e.from_stage,
            "to_state": e.to_state,
            "to_stage": e.to_stage,
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "reason": e.reason,
            "metadata": e.event_metadata,
            "created_at": _fmt(e.created_at),
        } for e in events],
    }
