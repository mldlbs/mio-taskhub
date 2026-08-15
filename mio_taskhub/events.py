# mio_taskhub/events.py
"""统一事件日志：emit_event 写 Event 表（与业务同事务），broadcast_for_event 统一 WS 广播。

约定：写操作端点先 emit_event(db, ...) 拿到 Event 对象，随业务 db.commit()，
提交后调用 broadcast_for_event(event) 触发 WS 推送。
"""
from __future__ import annotations
import asyncio
import json
from typing import Optional
from sqlmodel import Session
from mio_taskhub.models import Event
from mio_taskhub.notifications import ws_manager

# entity → WS 消息 type
MESSAGE_TYPES = {
    "task": "task_update",
    "idea": "idea_update",
    "discussion": "discussion_update",
}


def emit_event(db: Session, type: str, entity: str = "", entity_id: str = "",
               run_id: str = "", payload: Optional[dict] = None) -> Event:
    """构造并 db.add 一条事件（不 commit，与业务同事务提交）。返回 Event 对象（含自增 seq）。"""
    e = Event(
        type=type,
        entity=entity,
        entity_id=entity_id,
        run_id=run_id,
        payload=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
    )
    db.add(e)
    return e


def event_to_dict(e: Event) -> dict:
    return {
        "seq": e.id,
        "type": e.type,
        "entity": e.entity,
        "entity_id": e.entity_id,
        "run_id": e.run_id or "",
        "payload": json.loads(e.payload) if e.payload else None,
        "at": e.at.isoformat(),
    }


def broadcast_for_event(event: Event):
    """按 entity 映射 WS 消息类型并广播。异步隔离，异常静默。"""
    msg_type = MESSAGE_TYPES.get(event.entity, "event_update")
    try:
        asyncio.run(ws_manager.broadcast({"type": msg_type, "event": event_to_dict(event)}))
    except Exception:
        pass
