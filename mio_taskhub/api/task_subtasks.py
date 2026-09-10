import json as _json
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task, Subtask, SubtaskStatus, GitRef, RefType, HistoryEvent
from mio_taskhub.events import emit_event
from mio_taskhub.api.task_helpers import parse_enum

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/{task_id}/subtasks")
def add_subtask(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    st = Subtask(task_id=task_id, order=body.get("order", 0),
                 title=body.get("title", ""), status=parse_enum(SubtaskStatus, body.get("status"), "pending"))
    event = emit_event(db, type="task_subtask_added", entity="task", entity_id=task_id,
                       payload={"subtask_id": st.id, "title": st.title})
    db.add(st); db.commit(); db.refresh(st)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}


@router.patch("/{task_id}/subtasks/{sid}")
def update_subtask(task_id: str, sid: str, body: dict, db: Session = Depends(get_session)):
    st = db.get(Subtask, sid)
    if not st or st.task_id != task_id:
        raise HTTPException(404, "subtask not found")
    if "title" in body: st.title = body["title"]
    if "order" in body: st.order = body["order"]
    if "status" in body: st.status = parse_enum(SubtaskStatus, body["status"])
    event = emit_event(db, type="task_subtask_updated", entity="task", entity_id=task_id,
                       payload={"subtask_id": st.id, "status": st.status.value})
    db.add(st); db.commit(); db.refresh(st)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}


@router.post("/{task_id}/gitrefs")
def add_gitref(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    g = GitRef(task_id=task_id, ref_type=parse_enum(RefType, body.get("ref_type"), "branch"),
               value=body.get("value", ""), note=body.get("note", ""))
    event = emit_event(db, type="task_gitref_added", entity="task", entity_id=task_id,
                       payload={"gitref_id": g.id, "ref_type": g.ref_type.value})
    db.add(g); db.commit(); db.refresh(g)
    return {"id": g.id, "task_id": g.task_id, "ref_type": g.ref_type.value,
            "value": g.value, "note": g.note}


@router.post("/{task_id}/history")
def add_history(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    h = HistoryEvent(task_id=task_id, type=body.get("type", ""),
                     payload=_json.dumps(body.get("payload")) if body.get("payload") is not None else None)
    event = emit_event(db, type="task_history_added", entity="task", entity_id=task_id,
                       payload={"type": h.type})
    db.add(h); db.commit(); db.refresh(h)
    return {"id": h.id, "task_id": h.task_id, "type": h.type,
            "payload": h.payload, "at": h.at.isoformat()}
