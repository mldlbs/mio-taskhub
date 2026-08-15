# mio_taskhub/api/events.py
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Event
from mio_taskhub.events import event_to_dict

router = APIRouter(prefix="/events", tags=["events"])

DEFAULT_LIMIT = 200


@router.get("")
def list_events(after_seq: int = Query(None), limit: int = Query(DEFAULT_LIMIT, ge=1, le=1000),
                db: Session = Depends(get_session)):
    """事件订阅：after_seq 不传返回最近 limit 条；=0 返回全部；=N 返回 seq>N 的增量。"""
    q = select(Event)
    if after_seq is not None and after_seq > 0:
        q = q.where(Event.id > after_seq)
    rows = db.exec(q.order_by(Event.id.asc()).limit(limit)).all()
    events = [event_to_dict(e) for e in rows]
    return {
        "events": events,
        "next_seq": events[-1]["seq"] if events else 0,
    }
