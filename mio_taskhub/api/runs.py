from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Run, RunState, Task, TaskState, TaskStage
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event, broadcast_for_event

router = APIRouter(prefix="/runs", tags=["runs"])

# 指数退避基数（秒），可按需调整；失败后等待 2^attempt * BASE 秒后重入队列
BASE_RETRY_SECONDS = 2.0


def _backoff_seconds(attempt: int, base: float = BASE_RETRY_SECONDS) -> float:
    try:
        return float((2 ** max(1, attempt)) * base)
    except Exception:
        return float(base)


def _retry_at_for(task) -> timedelta:
    # task.attempt 为已执行的次数（claim 时 +1），退避基于当前 attempt
    secs = _backoff_seconds(task.attempt)
    return timedelta(seconds=secs)

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
    payload = {"success": success}
    if task:
        if success:
            task.state = TaskState.COMPLETED
            task.stage = TaskStage.REVIEW
            task.retry_at = None
            task_state = "completed"
            payload["state"] = task_state
        else:
            # 失败：根据 attempt / max_retries 决定重试或失败，带指数退避
            if task.attempt < task.max_retries:
                # max_retries=0 意味着不重试，直接失败；否则进入 retrying 并设定下次重试时间
                if task.max_retries == 0:
                    task.state = TaskState.FAILED
                    task.retry_at = None
                    task_state = "failed"
                    payload.update({"state": task_state, "reason": "max_retries=0"})
                else:
                    task.state = TaskState.RETRYING
                    task.retry_count = (task.retry_count or 0) + 1
                    backoff = _retry_at_for(task)
                    task.retry_at = _now() + backoff
                    task_state = "retrying"
                    payload.update({
                        "state": task_state,
                        "attempt": task.attempt,
                        "max_retries": task.max_retries,
                        "retry_at": task.retry_at.isoformat(),
                        "backoff_seconds": backoff.total_seconds(),
                    })
            else:
                task.state = TaskState.FAILED
                task.retry_at = None
                task_state = "failed"
                payload.update({"state": task_state, "attempt": task.attempt, "max_retries": task.max_retries})
        db.add(task)
    else:
        payload["state"] = None
    event = emit_event(db, type="task_result", entity="task", entity_id=run.task_id,
                       run_id=run.id, payload=payload)
    # 额外事件：重试调度，便于前端倒计时与告警
    if task_state == "retrying":
        retry_evt = emit_event(db, type="task_retry_scheduled", entity="task", entity_id=task.id,
                               run_id=run.id, payload={
                                   "attempt": task.attempt, "max_retries": task.max_retries,
                                   "retry_at": task.retry_at.isoformat(), "backoff_seconds": payload.get("backoff_seconds"),
                               })
        # broadcast 会在 commit 后统一，这里先记下；commit 后一起广播
        # 为简化，直接在 commit 后广播 retry_evt
        db.add(run)
        db.commit()
        db.refresh(run)
        db.refresh(task)
        broadcast_for_event(event)
        broadcast_for_event(retry_evt)
        return {"id": run.id, "state": run.state.value, "result": run.result,
                "task_state": task_state, "retry_at": task.retry_at.isoformat(),
                "backoff_seconds": payload.get("backoff_seconds")}
    db.add(run)
    db.commit()
    db.refresh(run)
    broadcast_for_event(event)
    return {"id": run.id, "state": run.state.value, "result": run.result, "task_state": task_state}
