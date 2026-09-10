from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskState, TaskStage, Discussion
from mio_taskhub.events import emit_event
from mio_taskhub.api.task_helpers import parse_enum

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


@router.delete("/{task_id}")
def cancel_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404)
    from mio_taskhub.transitions import apply_transition
    from mio_taskhub.state_machine import State, Stage, ActorType, IllegalTransition
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
    from mio_taskhub.transitions import apply_transition
    from mio_taskhub.state_machine import State, Stage, ActorType, IllegalTransition
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
    from mio_taskhub.transitions import apply_transition, _orm_to_status_state, _orm_to_status_stage
    from mio_taskhub.state_machine import State, Stage as M1Stage, ActorType, IllegalTransition as M1Illegal
    m1_events = []
    try:
        cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
        from_st = _orm_to_status_stage(src)
        if dst == TaskStage.DONE:
            if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
                _, e1 = apply_transition(
                    t, State.COMPLETED, from_st,
                    ActorType.USER, "api:advance_stage",
                    reason="advance→done: review pass",
                )
                if e1: m1_events.append(e1)
            _, e2 = apply_transition(
                t, State.COMPLETED, M1Stage.DONE,
                ActorType.SYSTEM, "auto:finalize",
                reason="T6 finalize",
            )
            if e2: m1_events.append(e2)
        elif dst == TaskStage.CANCELLED:
            _, e = apply_transition(
                t, State.CANCELLED, from_st,
                ActorType.USER, "api:advance_stage",
                reason="advance→cancelled",
            )
            if e: m1_events.append(e)
        else:
            actor = ActorType.USER if cur_s == TaskState.QUEUED else ActorType.SYSTEM
            actor_id = "api:advance_stage" if cur_s == TaskState.QUEUED else "auto:advance"
            _, e = apply_transition(
                t, _orm_to_status_state(cur_s),
                M1Stage(dst.value), actor, actor_id,
                reason=f"advance {src.value}→{dst.value}",
            )
            if e: m1_events.append(e)
    except M1Illegal as exc:
        raise HTTPException(400, f"state machine rejected advance: {exc}")
    event = emit_event(db, type="task_stage", entity="task", entity_id=task_id,
                       payload={"target": dst.value})
    db.add(t)
    for ev in m1_events:
        db.add(ev)
    db.commit()
    db.refresh(t)
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
    from mio_taskhub.transitions import apply_transition, _orm_to_status_state, _orm_to_status_stage
    from mio_taskhub.state_machine import State as M1State, Stage as M1Stage, ActorType as M1Actor
    m1_events = []
    cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
    cur_m1 = _orm_to_status_state(cur_s)
    to_st = _orm_to_status_stage(dst)
    if dst == TaskStage.DONE:
        if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
            from_st_done = _orm_to_status_stage(src)
            _, e1 = apply_transition(t, M1State.COMPLETED, from_st_done,
                                     M1Actor.USER, "api:move_to_stage",
                                     reason="move→done")
            if e1: m1_events.append(e1)
        _, e2 = apply_transition(t, M1State.COMPLETED, M1Stage.DONE,
                                 M1Actor.SYSTEM, "auto:finalize",
                                 reason="move→done: finalize")
        if e2: m1_events.append(e2)
    elif dst == TaskStage.CANCELLED:
        from_st = _orm_to_status_stage(src)
        _, e = apply_transition(t, M1State.CANCELLED, from_st,
                                M1Actor.USER, "api:move_to_stage",
                                reason="move→cancelled")
        if e: m1_events.append(e)
    else:
        actor = M1Actor.USER if cur_s == TaskState.QUEUED else M1Actor.SYSTEM
        actor_id = "api:move_to_stage" if cur_s == TaskState.QUEUED else "auto:move"
        _, e = apply_transition(t, cur_m1, to_st, actor, actor_id,
                                reason=f"move {src.value}→{dst.value}")
        if e: m1_events.append(e)
    event = emit_event(db, type="task_moved", entity="task", entity_id=t.id,
                       payload={"from": src.value, "to": dst.value})
    db.add(t)
    for ev in m1_events:
        db.add(ev)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "review_result": t.review_result,
            "state": t.state.value}
