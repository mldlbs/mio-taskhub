from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.events import emit_event
from mio_taskhub.models import Task, TaskState, TaskStage, Discussion
from mio_taskhub.workflow.transitions import apply_transition, _orm_to_status_state, _orm_to_status_stage
from mio_taskhub.api.task_helpers import parse_enum
from mio_taskhub.workflow.state_machine import State, State as M1State, Stage as M1Stage, ActorType, IllegalTransition as M1Illegal

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _apply_stage_requirements(t: Task, dst: TaskStage, body: dict, strict: bool = True):
    """校验目标阶段的产出物并设置辅助字段。抛 HTTPException(422) 当产出物缺失。

    design 需 spec_path、planning 需 plan_path、done 需 review_result。
    注意：本函数 **不修改** task.state / task.stage —— 状态变更由调用方通过
    apply_transition() 统一处理。
    strict=False 时（拖拽轻量路径）不强制产出物，仅在提供时记录，
    done 缺 review_result 时自动补默认结论。
    """
    if dst == TaskStage.DESIGN:
        if body.get("spec_path"):
            t.spec_path = body["spec_path"]
        elif strict and not t.spec_path:
            raise HTTPException(422, "design stage requires spec_path")
    if dst == TaskStage.PLANNING:
        if body.get("plan_path"):
            t.plan_path = body["plan_path"]
        elif strict and not t.plan_path:
            raise HTTPException(422, "planning stage requires plan_path")
    if dst == TaskStage.DONE:
        review = body.get("review_result") or t.review_result or "（拖拽完成）"
        if strict and not (body.get("review_result") or t.review_result):
            raise HTTPException(422, "done stage requires review_result")
        t.review_result = review


def _transition_to_stage(task, to_state, stage, actor_type, actor_id, reason, m1_events):
    """Apply a single state transition and collect the resulting event."""
    t, event = apply_transition(task, to_state, stage, actor_type, actor_id, reason)
    if event:
        m1_events.append(event)
    return t, event


def _commit_task_and_events(task, db, m1_events):
    """Commit task and all transition events, then refresh the task."""
    db.add(task)
    for ev in m1_events:
        db.add(ev)
    db.commit()
    db.refresh(task)


@router.delete("/{task_id}")
def cancel_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404)
    from mio_taskhub.workflow.transitions import apply_transition
    from mio_taskhub.workflow.state_machine import State, Stage, ActorType, IllegalTransition
    current_stage = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    try:
        _, m1_event = apply_transition(
            t, State.CANCELLED, Stage(current_stage.value),
            ActorType.USER, "api:cancel_task",
            reason="用户取消",
        )
        db.add(m1_event)
    except IllegalTransition:
        raise HTTPException(409, f"task {task_id} 不可取消（终态）")
    event = emit_event(db, type="task_cancelled", entity="task", entity_id=task_id)
    db.add(t)
    db.commit()
    return {"ok": True, "state": "cancelled"}


@router.post("/{task_id}/retry")
def retry_task(task_id: str, body: dict = None, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    if t.state not in (TaskState.FAILED, TaskState.RETRYING):
        raise HTTPException(409, f"task {task_id} 不可重试（当前状态 {t.state.value}，仅 failed/retrying 可重试）")
    orig_state = t.state.value
    if t.attempt >= t.max_retries:
        t.attempt = 0
        t.retry_count = 0
    t.retry_at = None
    from mio_taskhub.workflow.transitions import apply_transition
    from mio_taskhub.workflow.state_machine import State, Stage, ActorType, IllegalTransition
    current_stage = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    try:
        _, m1_event = apply_transition(
            t, State.QUEUED, Stage.READY,
            ActorType.USER, "api:retry_task",
            reason=f"manual_retry from {orig_state}",
        )
        db.add(m1_event)
    except IllegalTransition as e:
        raise HTTPException(409, f"retry 非法: {e}")
    event = emit_event(db, type="task_retry_manual", entity="task", entity_id=t.id,
                       payload={"from_state": orig_state, "attempt": t.attempt, "max_retries": t.max_retries})
    db.add(t)
    db.commit()
    db.refresh(t)
    requeued = emit_event(db, type="task_retry_requeued", entity="task", entity_id=t.id,
                          payload={"reason": "manual_retry", "attempt": t.attempt, "from": orig_state})
    db.commit()
    return {"id": t.id, "state": t.state.value, "stage": t.stage.value,
            "attempt": t.attempt, "max_retries": t.max_retries, "retry_at": None}


@router.post("/{task_id}/stage")
def advance_stage(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    target = body.get("target_stage")
    if not target:
        raise HTTPException(422, "target_stage is required")
    try:
        dst = TaskStage(target)
    except ValueError:
        raise HTTPException(400, f"invalid target_stage: {target}")
    src = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    if not TaskStage.can_advance(src, dst):
        raise HTTPException(400, f"cannot advance from {src.value} to {dst.value}")
    if dst == TaskStage.DESIGN:
        discussions = db.exec(select(Discussion).where(Discussion.task_id == task_id)).all()
        if not discussions:
            raise HTTPException(422, "design stage requires at least one discussion record")
    _apply_stage_requirements(t, dst, body)
    m1_events = []
    try:
        cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
        from_st = _orm_to_status_stage(src)
        if dst == TaskStage.DONE:
            if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
                _transition_to_stage(t, State.COMPLETED, from_st,
                                     ActorType.USER, "api:advance_stage",
                                     "advance→done: review pass", m1_events)
            _transition_to_stage(t, State.COMPLETED, M1Stage.DONE,
                                  ActorType.SYSTEM, "auto:finalize",
                                  "T6 finalize", m1_events)
        elif dst == TaskStage.CANCELLED:
            _transition_to_stage(t, State.CANCELLED, from_st,
                                  ActorType.USER, "api:advance_stage",
                                  "advance→cancelled", m1_events)
        else:
            actor = ActorType.USER if cur_s == TaskState.QUEUED else ActorType.SYSTEM
            actor_id = "api:advance_stage" if cur_s == TaskState.QUEUED else "auto:advance"
            _transition_to_stage(t, _orm_to_status_state(cur_s), M1Stage(dst.value),
                                  actor, actor_id,
                                  f"advance {src.value}→{dst.value}", m1_events)
    except M1Illegal as exc:
        raise HTTPException(400, f"state machine rejected advance: {exc}")
    emit_event(db, type="task_stage", entity="task", entity_id=task_id,
               payload={"target": dst.value})
    _commit_task_and_events(t, db, m1_events)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "review_result": t.review_result,
            "state": t.state.value}


@router.post("/{task_id}/stage/move")
def move_to_stage(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    target = body.get("target_stage")
    if not target:
        raise HTTPException(422, "target_stage is required")
    try:
        dst = TaskStage(target)
    except ValueError:
        raise HTTPException(400, f"invalid target_stage: {target}")
    src = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    if src in (TaskStage.DONE, TaskStage.CANCELLED):
        raise HTTPException(400, f"cannot move terminal stage {src.value}")
    _apply_stage_requirements(t, dst, body, strict=False)
    if src == dst:
        db.add(t)
        db.commit()
        db.refresh(t)
        return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
                "plan_path": t.plan_path, "review_result": t.review_result,
                "state": t.state.value}
    m1_events = []
    cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
    cur_m1 = _orm_to_status_state(cur_s)
    to_st = _orm_to_status_stage(dst)
    if dst == TaskStage.DONE:
        if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
            from_st_done = _orm_to_status_stage(src)
            _transition_to_stage(t, M1State.COMPLETED, from_st_done,
ActorType.USER, "api:move_to_stage",
                                  "move→done", m1_events)
        _transition_to_stage(t, M1State.COMPLETED, M1Stage.DONE,
                              ActorType.SYSTEM, "auto:finalize",
                              "move→done: finalize", m1_events)
    elif dst == TaskStage.CANCELLED:
        from_st = _orm_to_status_stage(src)
        _transition_to_stage(t, M1State.CANCELLED, from_st,
                              ActorType.USER, "api:move_to_stage",
                              "move→cancelled", m1_events)
    else:
        actor = ActorType.USER if cur_s == TaskState.QUEUED else ActorType.SYSTEM
        actor_id = "api:move_to_stage" if cur_s == TaskState.QUEUED else "auto:move"
        _transition_to_stage(t, cur_m1, to_st, actor, actor_id,
                              f"move {src.value}→{dst.value}", m1_events)
    emit_event(db, type="task_moved", entity="task", entity_id=t.id,
               payload={"from": src.value, "to": dst.value})
    _commit_task_and_events(t, db, m1_events)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "review_result": t.review_result,
            "state": t.state.value}
