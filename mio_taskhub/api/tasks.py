import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskState, Run, RunState
from mio_taskhub.utils import _now

router = APIRouter(prefix="/tasks", tags=["tasks"])

@router.post("", response_model=dict)
def create_task(body: dict, db: Session = Depends(get_session)):
    t = Task(
        id=str(uuid.uuid4())[:8],
        title=body.get("title", ""),
        description=body.get("description", ""),
        target_agent_type=body.get("target_agent_type"),
        priority=body.get("priority", 0),
        schedule_type=body.get("schedule_type", "once"),
        run_at=body.get("run_at"),
        cron_expr=body.get("cron_expr"),
        est_duration_min=body.get("est_duration_min", 30),
        depends_on=body.get("depends_on"),
        max_retries=body.get("max_retries", 3),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return {
        "id": t.id, "title": t.title, "state": t.state.value,
        "priority": t.priority, "created_at": t.created_at.isoformat(),
    }

@router.get("", response_model=list)
def list_tasks(state: str = None, agent_type: str = None, db: Session = Depends(get_session)):
    q = select(Task)
    if state:
        q = q.where(Task.state == TaskState(state))
    if agent_type:
        q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
    rows = db.exec(q).all()
    return [
        {"id": r.id, "title": r.title, "state": r.state.value,
         "priority": r.priority, "target_agent_type": r.target_agent_type}
        for r in rows
    ]

@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return {"id": t.id, "title": t.title, "state": t.state.value}

@router.delete("/{task_id}")
def cancel_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404)
    t.state = TaskState.CANCELLED
    db.add(t)
    db.commit()
    return {"ok": True, "state": "cancelled"}

@router.post("/claim")
def claim_task(agent: str = Query(...), agent_type: str = Query(None), db: Session = Depends(get_session)):
    existing = db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()
    if existing:
        return {"id": existing.id, "task_id": existing.task_id, "state": existing.state.value,
                "agent_name": existing.agent_name}
    q = select(Task).where(Task.state == TaskState.QUEUED)
    if agent_type:
        q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
    q = q.order_by(Task.priority.desc(), Task.created_at.asc())
    task = db.exec(q).first()
    if not task:
        return Response(status_code=204)
    run = Run(
        id=str(uuid.uuid4())[:8],
        task_id=task.id,
        agent_name=agent,
        state=RunState.CLAIMED,
        attempt=task.attempt + 1,
        started_at=_now(),
    )
    task.state = TaskState.CLAIMED
    task.attempt += 1
    db.add(run)
    db.add(task)
    db.commit()
    db.refresh(run)
    return {"id": run.id, "task_id": run.task_id, "state": run.state.value,
            "agent_name": run.agent_name, "attempt": run.attempt}
