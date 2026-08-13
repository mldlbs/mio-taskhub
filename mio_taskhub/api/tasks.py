import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.notifications import ws_manager
from mio_taskhub.models import (
    Task, TaskState, Run, RunState, Subtask, SubtaskStatus, GitRef, RefType, HistoryEvent,
    Discussion, DiscussionMessage,
)
from mio_taskhub.utils import _now

router = APIRouter(prefix="/tasks", tags=["tasks"])

def _broadcast_task_update(task_id: str):
    import asyncio
    try:
        asyncio.run(ws_manager.broadcast({"type": "task_update", "task_id": task_id}))
    except Exception:
        pass

def _parse_dt(value, name: str):
    if not isinstance(value, str):
        return value
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"invalid {name}: {value}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # naive → treat as UTC
    else:
        dt = dt.astimezone(timezone.utc)       # any offset → normalize to UTC
    return dt

def _parse_enum(enum_cls, value, default=None):
    if value is None and default is not None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        raise HTTPException(400, f"invalid value: {value}, expected one of {[e.value for e in enum_cls]}")

@router.post("", response_model=dict)
def create_task(body: dict, db: Session = Depends(get_session)):
    due_at = _parse_dt(body.get("due_at"), "due_at")
    run_at = _parse_dt(body.get("run_at"), "run_at")
    t = Task(
        id=str(uuid.uuid4())[:8],
        title=body.get("title", ""),
        description=body.get("description", ""),
        target_agent_type=body.get("target_agent_type"),
        priority=body.get("priority", 0),
        schedule_type=body.get("schedule_type", "once"),
        run_at=run_at,
        cron_expr=body.get("cron_expr"),
        est_duration_min=body.get("est_duration_min", 30),
        depends_on=body.get("depends_on"),
        max_retries=body.get("max_retries", 3),
        acceptance_criteria=body.get("acceptance_criteria", ""),
        due_at=due_at,
        labels=body.get("labels", []),
        project=body.get("project", ""),
        workspace=body.get("workspace", ""),
        files=body.get("files", []),
        deliverables=body.get("deliverables", []),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    _broadcast_task_update(t.id)
    return {
        "id": t.id, "title": t.title, "state": t.state.value,
        "priority": t.priority, "created_at": t.created_at.isoformat(),
    }

@router.get("", response_model=list)
def list_tasks(state: str = None, agent_type: str = None, db: Session = Depends(get_session)):
    q = select(Task)
    if state:
        ts = None
        try:
            ts = TaskState[state.upper()]  # 按成员名查找（忽略大小写）
        except KeyError:
            try:
                ts = TaskState(state)      # 回退：按值查找
            except ValueError:
                ts = None
        if ts is not None:
            q = q.where(Task.state == ts)
    if agent_type:
        q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
    rows = db.exec(q).all()
    return [
        {"id": r.id, "title": r.title, "state": r.state.value,
         "priority": r.priority, "target_agent_type": r.target_agent_type}
        for r in rows
    ]

def _task_detail(t: Task, db: Session) -> dict:
    subtasks = db.exec(select(Subtask).where(Subtask.task_id == t.id).order_by(Subtask.order)).all()
    gitrefs = db.exec(select(GitRef).where(GitRef.task_id == t.id)).all()
    history = db.exec(select(HistoryEvent).where(HistoryEvent.task_id == t.id).order_by(HistoryEvent.at)).all()
    discussions = db.exec(select(Discussion).where(Discussion.task_id == t.id)).all()
    def _fmt(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # stored naive → UTC
        return dt.isoformat()
    return {
        "id": t.id, "title": t.title, "description": t.description, "state": t.state.value,
        "priority": t.priority, "target_agent_type": t.target_agent_type,
        "schedule_type": t.schedule_type, "run_at": _fmt(t.run_at),
        "cron_expr": t.cron_expr, "est_duration_min": t.est_duration_min,
        "depends_on": t.depends_on, "max_retries": t.max_retries, "attempt": t.attempt,
        "created_at": t.created_at.isoformat(),
        "acceptance_criteria": t.acceptance_criteria,
        "due_at": _fmt(t.due_at),
        "labels": t.labels, "project": t.project, "workspace": t.workspace,
        "files": t.files, "deliverables": t.deliverables,
        "subtasks": [{"id": s.id, "order": s.order, "title": s.title, "status": s.status.value} for s in subtasks],
        "gitrefs": [{"id": g.id, "ref_type": g.ref_type.value, "value": g.value, "note": g.note} for g in gitrefs],
        "history": [{"id": h.id, "type": h.type, "payload": h.payload, "at": h.at.isoformat()} for h in history],
        "discussions": [{"id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
                         "summary": d.summary, "conclusions": d.conclusions,
                         "started_at": d.started_at.isoformat()} for d in discussions],
    }

@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return _task_detail(t, db)

@router.patch("/{task_id}")
def update_task(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    editable = ["title", "description", "priority", "est_duration_min", "max_retries",
                "acceptance_criteria", "due_at", "labels", "project", "workspace",
                "files", "deliverables", "target_agent_type", "depends_on"]
    for k in editable:
        if k in body:
            v = body[k]
            if k == "due_at":
                v = _parse_dt(v, "due_at")
            setattr(t, k, v)
    db.add(t)
    db.commit()
    db.refresh(t)
    _broadcast_task_update(t.id)
    return _task_detail(t, db)

@router.post("/{task_id}/subtasks")
def add_subtask(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    st = Subtask(task_id=task_id, order=body.get("order", 0),
                 title=body.get("title", ""), status=_parse_enum(SubtaskStatus, body.get("status"), "pending"))
    db.add(st); db.commit(); db.refresh(st)
    _broadcast_task_update(task_id)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}

@router.patch("/{task_id}/subtasks/{sid}")
def update_subtask(task_id: str, sid: str, body: dict, db: Session = Depends(get_session)):
    st = db.get(Subtask, sid)
    if not st or st.task_id != task_id:
        raise HTTPException(404, "subtask not found")
    if "title" in body: st.title = body["title"]
    if "order" in body: st.order = body["order"]
    if "status" in body: st.status = _parse_enum(SubtaskStatus, body["status"])
    db.add(st); db.commit(); db.refresh(st)
    _broadcast_task_update(task_id)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}

@router.post("/{task_id}/gitrefs")
def add_gitref(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    g = GitRef(task_id=task_id, ref_type=_parse_enum(RefType, body.get("ref_type"), "branch"),
               value=body.get("value", ""), note=body.get("note", ""))
    db.add(g); db.commit(); db.refresh(g)
    _broadcast_task_update(task_id)
    return {"id": g.id, "task_id": g.task_id, "ref_type": g.ref_type.value,
            "value": g.value, "note": g.note}

@router.post("/{task_id}/history")
def add_history(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    import json as _json
    h = HistoryEvent(task_id=task_id, type=body.get("type", ""),
                     payload=_json.dumps(body.get("payload")) if body.get("payload") is not None else None)
    db.add(h); db.commit(); db.refresh(h)
    _broadcast_task_update(task_id)
    return {"id": h.id, "task_id": h.task_id, "type": h.type,
            "payload": h.payload, "at": h.at.isoformat()}

@router.post("/{task_id}/discussions")
def add_discussion(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    conclusions = body.get("conclusions", "")
    status = "closed" if conclusions else "open"
    d = Discussion(task_id=task_id, topic=body.get("topic", ""), agent=body.get("agent", ""),
                   status=status, summary=body.get("summary", ""), conclusions=conclusions,
                   ended_at=_now() if status == "closed" else None)
    db.add(d); db.commit(); db.refresh(d)
    for m in body.get("messages", []):
        db.add(DiscussionMessage(discussion_id=d.id, author=m.get("author", ""),
                                 role=m.get("role", "user"), content=m.get("content", "")))
    db.commit()
    _broadcast_task_update(task_id)
    return {"id": d.id, "task_id": d.task_id, "topic": d.topic, "agent": d.agent,
            "status": d.status, "summary": d.summary, "conclusions": d.conclusions,
            "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None}

@router.get("/{task_id}/discussions")
def list_discussions(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    rows = db.exec(select(Discussion).where(Discussion.task_id == task_id).order_by(Discussion.started_at)).all()
    out = []
    for d in rows:
        msgs = db.exec(select(DiscussionMessage).where(DiscussionMessage.discussion_id == d.id).order_by(DiscussionMessage.at)).all()
        out.append({
            "id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
            "summary": d.summary, "conclusions": d.conclusions,
            "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None,
            "messages": [{"author": m.author, "role": m.role, "content": m.content,
                          "at": m.at.isoformat()} for m in msgs],
        })
    return {"task_id": task_id, "discussions": out}

@router.delete("/{task_id}")
def cancel_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404)
    t.state = TaskState.CANCELLED
    db.add(t)
    db.commit()
    _broadcast_task_update(task_id)
    return {"ok": True, "state": "cancelled"}

@router.post("/claim")
def claim_task(agent: str = Query(...), agent_type: str = Query(None),
               project: str = Query(None), workspace: str = Query(None),
               files: str = Query(None), db: Session = Depends(get_session)):
    existing = db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()
    if existing:
        return {"id": existing.id, "task_id": existing.task_id, "state": existing.state.value,
                "agent_name": existing.agent_name}
    q = select(Task).where(Task.state == TaskState.QUEUED)
    if agent_type:
        q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
    rows = db.exec(
        q.order_by(Task.priority.desc(), Task.created_at.asc())
    ).all()
    now = _now()
    task = None
    for t in rows:
        if t.schedule_type == "once" and t.run_at:
            run_at = t.run_at
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if run_at > now:
                continue
        task = t
        break
    if not task:
        return Response(status_code=204)
    if project and not task.project:
        task.project = project
    if workspace and not task.workspace:
        task.workspace = workspace
    if files and not task.files:
        task.files = [f.strip() for f in files.split(",") if f.strip()]
    run = Run(
        id=str(uuid.uuid4())[:8],
        task_id=task.id,
        agent_name=agent,
        state=RunState.CLAIMED,
        attempt=task.attempt + 1,
        started_at=_now(),
        last_heartbeat=_now(),
    )
    task.state = TaskState.CLAIMED
    task.attempt += 1
    db.add(run)
    db.add(task)
    db.commit()
    db.refresh(run)
    _broadcast_task_update(task.id)
    return {"id": run.id, "task_id": run.task_id, "state": run.state.value,
            "agent_name": run.agent_name, "attempt": run.attempt}
