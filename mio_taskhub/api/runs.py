from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Run, RunState, Task, TaskState
from mio_taskhub.utils import _now

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
    db.add(run)
    if task:
        db.add(task)
    db.commit()
    db.refresh(run)
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
    if task:
        if success:
            task.state = TaskState.COMPLETED
        else:
            if task.attempt < task.max_retries:
                task.state = TaskState.RETRYING
            else:
                task.state = TaskState.FAILED
        db.add(task)
    db.add(run)
    db.commit()
    db.refresh(run)
    return {"id": run.id, "state": run.state.value, "result": run.result}
