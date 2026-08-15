from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Run, RunState, Task, TaskState, TaskStage
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event, broadcast_for_event

router = APIRouter(prefix="/runs", tags=["runs"])

@router.post("/{run_id}/heartbeat")
def heartbeat(run_id: str, body: dict = None, db: Session = Depends(get_session)):
    body = body or {}
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404)
    run.state = RunState.RUNNING
    run.last_heartbeat = _now()
    if "progress" in body:
        run.progress = body["progress"]
    if "checkpoint" in body:
        run.checkpoint = body["checkpoint"]
    task = db.get(Task, run.task_id)
    if task and task.state == TaskState.CLAIMED:
        task.state = TaskState.RUNNING
    event = emit_event(db, type="heartbeat", entity="run", entity_id=run.id,
                       run_id=run.id, payload={"progress": run.progress})
    db.add(run)
    if task:
        db.add(task)
    db.commit()
    db.refresh(run)
    broadcast_for_event(event)
    return {"id": run.id, "state": run.state.value, "progress": run.progress}

@router.post("/{run_id}/result")
def submit_result(run_id: str, body: dict, db: Session = Depends(get_session)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404)
    success = body.get("success", True)
    run.result = body.get("result", "")
    run.exit_code = body.get("exit_code", 0 if success else 1)
    run.finished_at = _now()
    run.state = RunState.FINISHED
    run.progress = 100
    task = db.get(Task, run.task_id)
    task_state = None
    if task:
        if success:
            task.state = TaskState.COMPLETED
            task.stage = TaskStage.REVIEW
            task_state = "completed"
        else:
            if task.attempt < task.max_retries:
                task.state = TaskState.RETRYING
                task_state = "retrying"
            else:
                task.state = TaskState.FAILED
                task_state = "failed"
        db.add(task)
    event = emit_event(db, type="task_result", entity="task", entity_id=run.task_id,
                       run_id=run.id, payload={"success": success, "state": task_state})
    db.add(run)
    db.commit()
    db.refresh(run)
    broadcast_for_event(event)
    return {"id": run.id, "state": run.state.value, "result": run.result}
