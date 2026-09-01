"""M1 P0-6：状态投影回放测试。

验证：回放 TaskEvent 序列 → 重建的字段与 Task 实际字段一致。
TaskEvent 是 source of truth，Task 字段是投影。
"""
import pytest
from sqlmodel import Session, select
from datetime import datetime, timezone

from mio_taskhub.db import engine, init_db
from mio_taskhub.models import Task, TaskState, TaskStage, TaskEvent
from mio_taskhub.status import State, Stage, ActorType
from mio_taskhub.transitions import apply_transition


def _mk(title="proj1", state=TaskState.QUEUED, stage=TaskStage.BRAINSTORMING):
    with Session(engine) as s:
        t = Task(id=title, title=title, state=state, stage=stage)
        s.add(t); s.commit(); s.refresh(t)
        s.expunge(t)
    return t


def _get_events(task_id: str):
    with Session(engine) as s:
        return list(s.exec(
            select(TaskEvent).where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.id)
        ).all())


def _get_task(task_id: str):
    with Session(engine) as s:
        return s.get(Task, task_id)


def _apply(task_id: str, to_state: State, to_stage: Stage,
           actor: ActorType = ActorType.USER, reason: str = ""):
    with Session(engine) as s:
        t = s.get(Task, task_id)
        _, ev = apply_transition(t, to_state, to_stage, actor, f"test:{to_state.value}", reason=reason)
        s.add(t); s.add(ev); s.commit()
        s.refresh(t)
        s.expunge(t)
    return t


def _replay(events: list) -> dict:
    """从 TaskEvent 序列重建 Task 字段投影。"""
    if not events:
        return {}
    last_ev = events[-1]
    # cancelled 是 state 而非 stage，ORM stage 设为 cancelled
    last_stage = "cancelled" if last_ev.to_state == "cancelled" else last_ev.to_stage
    proj = {
        "state": last_ev.to_state,
        "stage": last_stage,
        "bounce_count": 0,
        "claimed_at": None,
        "running_started_at": None,
        "review_started_at": None,
        "completed_at": None,
        "failed_at": None,
        "cancelled_at": None,
        "last_transition_at": last_ev.created_at,
    }
    for ev in events:
        if ev.event_type == "bounced":
            proj["bounce_count"] += 1
        # 首次进入某 state/stage 的时间戳
        if ev.to_state == "claimed" and proj["claimed_at"] is None:
            proj["claimed_at"] = ev.created_at
        if ev.to_state == "running" and proj["running_started_at"] is None:
            proj["running_started_at"] = ev.created_at
        if ev.to_state == "completed" and proj["completed_at"] is None:
            proj["completed_at"] = ev.created_at
        if ev.to_state == "failed" and proj["failed_at"] is None:
            proj["failed_at"] = ev.created_at
        if ev.to_state == "cancelled" and proj["cancelled_at"] is None:
            proj["cancelled_at"] = ev.created_at
        if ev.to_stage == "review" and proj["review_started_at"] is None:
            proj["review_started_at"] = ev.created_at
    return proj


def _compare(actual: Task, proj: dict):
    """比较 Task 实际字段与投影。时间戳比较精确到秒（SQLite 微秒精度不稳定）。"""
    errors = []
    for field in ("state", "stage", "bounce_count", "claimed_at",
                  "running_started_at", "review_started_at",
                  "completed_at", "failed_at", "cancelled_at",
                  "last_transition_at"):
        actual_val = getattr(actual, field)
        proj_val = proj.get(field)
        # state/stage 是枚举，比较 value
        if field in ("state", "stage"):
            actual_val = actual_val.value if hasattr(actual_val, "value") else actual_val
        # 时间戳比较精确到秒（SQLite 存储会截断微秒）
        if isinstance(actual_val, datetime) and isinstance(proj_val, datetime):
            actual_val = actual_val.replace(microsecond=0)
            proj_val = proj_val.replace(microsecond=0)
        if actual_val != proj_val:
            errors.append(f"  {field}: actual={actual_val!r} proj={proj_val!r}")
    return errors


class TestProjectionReplay:
    def test_full_happy_path(self):
        """brainstorming→design→planning→ready→claim→start_impl→running→complete→review→done"""
        t = _mk("proj_happy")
        _apply(t.id, State.QUEUED, Stage.DESIGN, ActorType.USER, "brain→design")
        _apply(t.id, State.QUEUED, Stage.PLANNING, ActorType.USER, "design→plan")
        _apply(t.id, State.QUEUED, Stage.READY, ActorType.USER, "plan→ready")
        _apply(t.id, State.CLAIMED, Stage.READY, ActorType.AGENT, "claim")
        _apply(t.id, State.CLAIMED, Stage.IMPLEMENTING, ActorType.AGENT, "start_impl")
        _apply(t.id, State.RUNNING, Stage.IMPLEMENTING, ActorType.AGENT, "start")
        _apply(t.id, State.COMPLETED, Stage.IMPLEMENTING, ActorType.AGENT, "finish impl")
        _apply(t.id, State.COMPLETED, Stage.REVIEW, ActorType.USER, "to review")
        _apply(t.id, State.COMPLETED, Stage.DONE, ActorType.SYSTEM, "finalize")

        events = _get_events(t.id)
        actual = _get_task(t.id)
        proj = _replay(events)
        errors = _compare(actual, proj)
        assert not errors, "投影不匹配:\n" + "\n".join(errors)

    def test_claim_sets_claimed_at(self):
        """claim 后 claimed_at 应有值"""
        t = _mk("proj_claim")
        _apply(t.id, State.QUEUED, Stage.READY, ActorType.USER)
        _apply(t.id, State.CLAIMED, Stage.READY, ActorType.AGENT, "claim")

        events = _get_events(t.id)
        actual = _get_task(t.id)
        proj = _replay(events)
        errors = _compare(actual, proj)
        assert not errors, "投影不匹配:\n" + "\n".join(errors)
        assert actual.claimed_at is not None

    def test_bounce_count(self):
        """replay 正确统计 bounced 事件数"""
        t = _mk("proj_bounce")
        # 正常流程到 review
        _apply(t.id, State.QUEUED, Stage.READY, ActorType.USER)
        _apply(t.id, State.CLAIMED, Stage.READY, ActorType.AGENT, "claim")
        _apply(t.id, State.CLAIMED, Stage.IMPLEMENTING, ActorType.AGENT, "start_impl")
        _apply(t.id, State.RUNNING, Stage.IMPLEMENTING, ActorType.AGENT, "start")
        _apply(t.id, State.COMPLETED, Stage.IMPLEMENTING, ActorType.AGENT, "finish")
        _apply(t.id, State.COMPLETED, Stage.REVIEW, ActorType.USER, "to review")
        # 到 done
        _apply(t.id, State.COMPLETED, Stage.DONE, ActorType.SYSTEM, "finalize")

        events = _get_events(t.id)
        actual = _get_task(t.id)
        proj = _replay(events)
        errors = _compare(actual, proj)
        assert not errors, "投影不匹配:\n" + "\n".join(errors)
        # 无 bounced 事件
        bounced_events = [e for e in events if e.event_type == "bounced"]
        assert len(bounced_events) == 0
        assert actual.bounce_count == 0

    def test_cancelled_sets_timestamps(self):
        """cancel 后 cancelled_at 应有值"""
        t = _mk("proj_cancel")
        _apply(t.id, State.QUEUED, Stage.DESIGN, ActorType.USER)
        _apply(t.id, State.CANCELLED, Stage.DESIGN, ActorType.USER, "cancel")

        events = _get_events(t.id)
        actual = _get_task(t.id)
        proj = _replay(events)
        errors = _compare(actual, proj)
        assert not errors, "投影不匹配:\n" + "\n".join(errors)
        assert actual.cancelled_at is not None
        assert actual.state == TaskState.CANCELLED
        assert actual.stage == TaskStage.CANCELLED

    def test_review_started_at(self):
        """进入 review stage 后 review_started_at 应有值"""
        t = _mk("proj_review")
        _apply(t.id, State.QUEUED, Stage.READY, ActorType.USER)
        _apply(t.id, State.CLAIMED, Stage.READY, ActorType.AGENT, "claim")
        _apply(t.id, State.CLAIMED, Stage.IMPLEMENTING, ActorType.AGENT, "start_impl")
        _apply(t.id, State.RUNNING, Stage.IMPLEMENTING, ActorType.AGENT, "start")
        _apply(t.id, State.COMPLETED, Stage.IMPLEMENTING, ActorType.AGENT, "finish")
        _apply(t.id, State.COMPLETED, Stage.REVIEW, ActorType.USER, "to review")

        events = _get_events(t.id)
        actual = _get_task(t.id)
        proj = _replay(events)
        errors = _compare(actual, proj)
        assert not errors, "投影不匹配:\n" + "\n".join(errors)
        assert actual.review_started_at is not None

    def test_running_started_at(self):
        """进入 running 后 running_started_at 应有值"""
        t = _mk("proj_running")
        _apply(t.id, State.QUEUED, Stage.READY, ActorType.USER)
        _apply(t.id, State.CLAIMED, Stage.READY, ActorType.AGENT, "claim")
        _apply(t.id, State.CLAIMED, Stage.IMPLEMENTING, ActorType.AGENT, "start_impl")
        _apply(t.id, State.RUNNING, Stage.IMPLEMENTING, ActorType.AGENT, "start")

        events = _get_events(t.id)
        actual = _get_task(t.id)
        proj = _replay(events)
        errors = _compare(actual, proj)
        assert not errors, "投影不匹配:\n" + "\n".join(errors)
        assert actual.running_started_at is not None

    def test_free_form_moves(self):
        """自由拖拽 stage 跳转后投影正确"""
        t = _mk("proj_free")
        _apply(t.id, State.QUEUED, Stage.DESIGN, ActorType.USER, "brain→design")
        _apply(t.id, State.QUEUED, Stage.REVIEW, ActorType.USER, "design→review")
        _apply(t.id, State.QUEUED, Stage.PLANNING, ActorType.USER, "review→plan")

        events = _get_events(t.id)
        actual = _get_task(t.id)
        proj = _replay(events)
        errors = _compare(actual, proj)
        assert not errors, "投影不匹配:\n" + "\n".join(errors)
        assert actual.state == TaskState.QUEUED
        assert actual.stage == TaskStage.PLANNING

    def test_event_count_matches(self):
        """事件数量与转换次数一致"""
        t = _mk("proj_count")
        _apply(t.id, State.QUEUED, Stage.DESIGN, ActorType.USER)
        _apply(t.id, State.QUEUED, Stage.PLANNING, ActorType.USER)
        _apply(t.id, State.QUEUED, Stage.READY, ActorType.USER)
        _apply(t.id, State.CLAIMED, Stage.READY, ActorType.AGENT, "claim")

        events = _get_events(t.id)
        assert len(events) == 4

    def test_last_transition_at_matches_last_event(self):
        """last_transition_at 等于最后一条事件的 created_at"""
        t = _mk("proj_last")
        _apply(t.id, State.QUEUED, Stage.DESIGN, ActorType.USER)
        _apply(t.id, State.QUEUED, Stage.PLANNING, ActorType.USER)

        events = _get_events(t.id)
        actual = _get_task(t.id)
        proj = _replay(events)
        errors = _compare(actual, proj)
        assert not errors, "投影不匹配:\n" + "\n".join(errors)
        assert actual.last_transition_at == events[-1].created_at
