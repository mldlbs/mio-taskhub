from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Run, RunState, Task, TaskState, TaskStage
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event, broadcast_for_event
from mio_taskhub.transitions import apply_transition
from mio_taskhub.status import State, Stage, ActorType, IllegalTransition as M1Illegal

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


def _safe_transition(task, to_state, to_stage, actor_type, actor_id, reason="", metadata=None):
    """走 M1 状态机；非法时返回 (None, None) 不抛（保持旧行为兼容）。"""
    try:
        cur = task.stage if isinstance(task.stage, TaskStage) else TaskStage(task.stage)
        from_stage = Stage(cur.value if cur != TaskStage.CANCELLED else "brainstorming")
        _, ev = apply_transition(
            task, to_state, to_stage,
            actor_type, actor_id, reason=reason, metadata=metadata,
        )
        return _, ev
    except M1Illegal:
        return None, None


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
    m1_event = None
    if task and task.state == TaskState.CLAIMED:
        # M1: T2 (claimed, implementing) → (running, implementing)
        _, m1_event = _safe_transition(
            task, State.RUNNING, Stage.IMPLEMENTING,
            ActorType.AGENT, run.agent_name,
            reason="heartbeat",
        )
    event = emit_event(db, type="heartbeat", entity="run", entity_id=run.id,
                       run_id=run.id, payload={"progress": run.progress})
    db.add(run)
    if task:
        db.add(task)
    if m1_event is not None:
        db.add(m1_event)
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
    m1_events = []
    if task:
        if success:
            task_state, payload = _handle_success(task, run, m1_events)
        else:
            task_state, payload = _handle_failure(task, run, m1_events, payload)
        db.add(task)
    else:
        payload["state"] = None
    event = emit_event(db, type="task_result", entity="task", entity_id=run.task_id,
                       run_id=run.id, payload=payload)
    db.add(run)
    for ev in m1_events:
        db.add(ev)
    extra = _emit_retry_event_if_needed(db, task, task_state, run, payload)
    db.commit()
    db.refresh(run)
    db.refresh(task)
    broadcast_for_event(event)
    if extra is not None:
        broadcast_for_event(extra)
    if task_state == "retrying":
        return {"id": run.id, "state": run.state.value, "result": run.result,
                "task_state": task_state, "retry_at": task.retry_at.isoformat(),
                "backoff_seconds": payload.get("backoff_seconds")}
    return {"id": run.id, "state": run.state.value, "result": run.result, "task_state": task_state}


def _handle_success(task, run, m1_events):
    """成功路径：claimed→running→completed→review。"""
    if task.state == TaskState.CLAIMED:
        _, e0 = _safe_transition(task, State.RUNNING, Stage.IMPLEMENTING,
                                 ActorType.AGENT, run.agent_name, reason="auto-start before submit")
        if e0: m1_events.append(e0)
    _, e1 = _safe_transition(task, State.COMPLETED, Stage.IMPLEMENTING,
                             ActorType.AGENT, run.agent_name, reason="submit_result success")
    if e1: m1_events.append(e1)
    task.retry_at = None
    _, e2 = _safe_transition(task, State.COMPLETED, Stage.REVIEW,
                             ActorType.SYSTEM, "auto:send-to-review", reason="T4")
    if e2: m1_events.append(e2)
    return "completed", {"success": True, "state": "completed"}


def _handle_failure(task, run, m1_events, payload):
    """失败路径：重试或失败，带指数退避。"""
    if task.state == TaskState.CLAIMED:
        _, e0 = _safe_transition(task, State.RUNNING, Stage.IMPLEMENTING,
                                 ActorType.AGENT, run.agent_name, reason="auto-start before fail")
        if e0: m1_events.append(e0)
    if task.attempt < task.max_retries:
        if task.max_retries == 0:
            return _fail_permanently(task, run, m1_events)
        return _fail_with_retry(task, run, m1_events, payload)
    return _fail_permanently(task, run, m1_events)


def _fail_permanently(task, run, m1_events):
    """永久失败（max_retries=0 或已耗尽）。"""
    _, e = _safe_transition(task, State.FAILED, Stage.IMPLEMENTING,
                            ActorType.AGENT, run.agent_name, reason="submit_result fail (max reached)")
    if e: m1_events.append(e)
    task.retry_at = None
    return "failed", {"success": False, "state": "failed", "attempt": task.attempt, "max_retries": task.max_retries}


def _fail_with_retry(task, run, m1_events, payload):
    """失败后重试（指数退避）。"""
    _, e1 = _safe_transition(task, State.FAILED, Stage.IMPLEMENTING,
                             ActorType.AGENT, run.agent_name, reason="submit_result fail")
    if e1: m1_events.append(e1)
    task.retry_count = (task.retry_count or 0) + 1
    backoff = _retry_at_for(task)
    task.retry_at = _now() + backoff
    _, e2 = _safe_transition(task, State.RETRYING, Stage.IMPLEMENTING,
                             ActorType.SYSTEM, "auto:retry", reason="T9")
    if e2: m1_events.append(e2)
    return "retrying", {
        "success": False, "state": "retrying",
        "attempt": task.attempt, "max_retries": task.max_retries,
        "retry_at": task.retry_at.isoformat(), "backoff_seconds": backoff.total_seconds(),
    }


def _emit_retry_event_if_needed(db, task, task_state, run, payload):
    """如果是 retrying 状态，发送额外的 retry_scheduled 事件。"""
    if task_state == "retrying":
        extra = emit_event(db, type="task_retry_scheduled", entity="task", entity_id=task.id,
                           run_id=run.id, payload={
                               "attempt": task.attempt, "max_retries": task.max_retries,
                               "retry_at": task.retry_at.isoformat(),
                               "backoff_seconds": payload.get("backoff_seconds"),
                           })
        db.add(extra)
        return extra
    return None
