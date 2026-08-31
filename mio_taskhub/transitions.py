"""M1 Step 3: 状态转换应用器。

集中处理一次合法状态转换的副作用：
 - 更新 task.state / task.stage
 - 设置对应时间戳（claimed_at / running_started_at / review_started_at /
   completed_at / failed_at / cancelled_at / last_transition_at）
 - bounce_count += 1（如 increments_bounce）
 - 构造 TaskEvent（调用方负责 session.add + commit）

调用方约定：
 - 在事务中先 session.add(task)（若新建），再调用本函数，最后 session.add(event)
 - actor_type / actor_id 必填；系统动作用 ActorType.SYSTEM
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, Tuple

from mio_taskhub.models import Task, TaskEvent, TaskState as OrmTaskState, TaskStage as OrmTaskStage
from mio_taskhub.status import (
    State, Stage, ActorType, Transition,
    validate_transition, IllegalTransition,
)


# ---------- ORM ↔ status 枚举映射 ----------
def _orm_to_status_state(s) -> State:
    """TaskState → State。BLOCKED_FAILED 视为 QUEUED（迁移约定）。"""
    v = s.value if hasattr(s, "value") else s
    if v == "blocked_failed":
        return State.QUEUED
    return State(v)


def _orm_to_status_stage(st) -> Stage:
    """TaskStage → Stage。CANCELLED 视为 BRAINSTORMING（迁移约定）。"""
    v = st.value if hasattr(st, "value") else st
    if v == "cancelled":
        return Stage.BRAINSTORMING
    return Stage(v)


def _status_to_orm_state(s: State):
    return OrmTaskState(s.value)


def _status_to_orm_stage(s: Stage):
    return OrmTaskStage(s.value)


# ---------- 核心 ----------
def apply_transition(
    task: Task,
    to_state: State,
    to_stage: Stage,
    actor_type: ActorType,
    actor_id: str,
    reason: str = "",
    metadata: Optional[dict] = None,
) -> Tuple[Transition, TaskEvent]:
    """
    校验 + 应用一次合法状态转换，返回 (Transition, TaskEvent)。
    调用方负责把 event 加入 session 并 commit。
    """
    from_state = _orm_to_status_state(task.state)
    from_stage = _orm_to_status_stage(task.stage)

    t = validate_transition(from_state, from_stage, to_state, to_stage, actor_type)

    # 写回 ORM
    task.state = _status_to_orm_state(to_state)
    task.stage = _status_to_orm_stage(to_stage)
    # cancelled 是终态 state，ORM stage 也要设为 CANCELLED（M1 无 cancelled stage，映射为 brainstorming）
    if to_state == State.CANCELLED:
        task.stage = OrmTaskStage.CANCELLED

    now = datetime.now(timezone.utc)
    if to_state == State.CLAIMED and task.claimed_at is None:
        task.claimed_at = now
    if to_state == State.RUNNING:
        task.running_started_at = now
    if to_stage == Stage.REVIEW and task.review_started_at is None:
        task.review_started_at = now
    if to_state == State.COMPLETED:
        task.completed_at = now
    if to_state == State.FAILED:
        task.failed_at = now
    if to_state == State.CANCELLED:
        task.cancelled_at = now
    task.last_transition_at = now

    if t.increments_bounce:
        task.bounce_count = (task.bounce_count or 0) + 1

    event = TaskEvent(
        task_id=task.id,
        event_type=t.event_type,
        from_state=from_state.value,
        from_stage=from_stage.value,
        to_state=to_state.value,
        to_stage=to_stage.value,
        actor_type=actor_type.value,
        actor_id=actor_id or "",
        reason=reason or "",
        event_metadata=metadata,
        created_at=now,
    )
    return t, event


def record_post_claim(task: Task, actor_id: str) -> Optional[TaskEvent]:
    """原子 claim 成功后补记 T1 TaskEvent + 设置 claimed_at / last_transition_at。

    调用时机：_claim_for 的 UPDATE ... WHERE state=queued 成功后，
    任务已被设为 CLAIMED，此处只记录事件和时间戳（不改 state/stage）。
    返回 TaskEvent 供调用方 add+commit。
    """
    from_state = _orm_to_status_state(task.state)
    from_stage = _orm_to_status_stage(task.stage)
    if from_state != State.CLAIMED:
        return None
    now = datetime.now(timezone.utc)
    if task.claimed_at is None:
        task.claimed_at = now
    task.last_transition_at = now
    return TaskEvent(
        task_id=task.id,
        event_type="claimed",
        from_state=State.QUEUED.value,
        from_stage=from_stage.value,
        to_state=State.CLAIMED.value,
        to_stage=Stage.IMPLEMENTING.value,
        actor_type=ActorType.AGENT.value,
        actor_id=actor_id or "",
        reason="atomic claim",
        created_at=now,
    )
