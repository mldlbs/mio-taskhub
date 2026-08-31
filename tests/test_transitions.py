"""M1 Step 3 测试：apply_transition 状态转换应用器。"""
import pytest
from sqlmodel import Session

from mio_taskhub.db import engine, init_db
from mio_taskhub.models import Task, TaskState, TaskStage, TaskEvent
from mio_taskhub.status import State, Stage, ActorType, IllegalTransition
from mio_taskhub.transitions import (
    apply_transition,
    _orm_to_status_state, _orm_to_status_stage,
)


def _mk(title="t1", state=TaskState.QUEUED, stage=TaskStage.READY):
    with Session(engine) as s:
        t = Task(id=title, title=title, state=state, stage=stage)
        s.add(t); s.commit(); s.refresh(t)
        s.expunge(t)
    return t


class TestEnumMapping:
    def test_state_blocked_failed_maps_to_queued(self):
        assert _orm_to_status_state(TaskState.BLOCKED_FAILED) == State.QUEUED

    def test_state_normal_mapping(self):
        assert _orm_to_status_state(TaskState.QUEUED) == State.QUEUED
        assert _orm_to_status_state(TaskState.COMPLETED) == State.COMPLETED

    def test_stage_cancelled_maps_to_brainstorming(self):
        assert _orm_to_status_stage(TaskStage.CANCELLED) == Stage.BRAINSTORMING

    def test_stage_normal_mapping(self):
        assert _orm_to_status_stage(TaskStage.IMPLEMENTING) == Stage.IMPLEMENTING


class TestApplyTransitionHappy:
    def test_claim_sets_claimed_at(self):
        # 完整领取流程：T1 claim(ready) → T12 start_impl
        t = _mk("h1", TaskState.QUEUED, TaskStage.READY)
        _, e1 = apply_transition(
            t, State.CLAIMED, Stage.READY,
            ActorType.AGENT, "agent-x",
        )
        assert t.state == TaskState.CLAIMED
        assert t.stage == TaskStage.READY
        assert t.claimed_at is not None
        assert e1.event_type == "claimed"
        _, e2 = apply_transition(
            t, State.CLAIMED, Stage.IMPLEMENTING,
            ActorType.AGENT, "agent-x",
        )
        assert t.stage == TaskStage.IMPLEMENTING
        assert e2.event_type == "stage_changed"

    def test_submit_sets_completed_at(self):
        t = _mk("h2", TaskState.RUNNING, TaskStage.IMPLEMENTING)
        _, event = apply_transition(
            t, State.COMPLETED, Stage.IMPLEMENTING,
            ActorType.AGENT, "agent-x",
        )
        assert t.state == TaskState.COMPLETED
        assert t.completed_at is not None
        assert event.event_type == "completed"

    def test_send_to_review_sets_review_started(self):
        t = _mk("h3", TaskState.COMPLETED, TaskStage.IMPLEMENTING)
        _, event = apply_transition(
            t, State.COMPLETED, Stage.REVIEW,
            ActorType.SYSTEM, "auto-dispatch",
        )
        assert t.review_started_at is not None
        assert event.event_type == "stage_changed"

    def test_finalize_fully_done(self):
        t = _mk("h4", TaskState.COMPLETED, TaskStage.REVIEW)
        _, event = apply_transition(
            t, State.COMPLETED, Stage.DONE,
            ActorType.SYSTEM, "auto-finalize",
        )
        assert t.state == TaskState.COMPLETED
        assert t.stage == TaskStage.DONE
        assert event.event_type == "stage_changed"

    def test_bounce_increments_count(self):
        t = _mk("h5", TaskState.FAILED, TaskStage.REVIEW)
        assert t.bounce_count == 0
        _, _ = apply_transition(
            t, State.QUEUED, Stage.IMPLEMENTING,
            ActorType.SYSTEM, "auto-bounce",
        )
        assert t.bounce_count == 1

    def test_cancel_sets_cancelled_at(self):
        t = _mk("h6", TaskState.QUEUED, TaskStage.READY)
        _, event = apply_transition(
            t, State.CANCELLED, Stage.READY,
            ActorType.USER, "user-1",
            reason="用户取消",
        )
        assert t.cancelled_at is not None
        assert event.reason == "用户取消"

    def test_fail_sets_failed_at(self):
        t = _mk("h7", TaskState.RUNNING, TaskStage.IMPLEMENTING)
        _, _ = apply_transition(
            t, State.FAILED, Stage.IMPLEMENTING,
            ActorType.AGENT, "agent-x",
            reason="测试失败",
        )
        assert t.failed_at is not None


class TestApplyTransitionReject:
    def test_illegal_transition_raises(self):
        t = _mk("r1", TaskState.QUEUED, TaskStage.BRAINSTORMING)
        with pytest.raises(IllegalTransition):
            apply_transition(
                t, State.RUNNING, Stage.IMPLEMENTING,
                ActorType.AGENT, "a1",
            )

    def test_wrong_actor_raises(self):
        t = _mk("r2", TaskState.QUEUED, Stage.READY)
        with pytest.raises(IllegalTransition):
            apply_transition(
                t, State.CLAIMED, Stage.IMPLEMENTING,
                ActorType.USER,  # T12 不允许 user
                "u1",
            )


class TestEventPersistence:
    def test_event_persisted_to_db(self):
        t = _mk("p1", TaskState.QUEUED, TaskStage.READY)
        # T1 + T12
        _, e1 = apply_transition(t, State.CLAIMED, Stage.READY, ActorType.AGENT, "a1")
        _, e2 = apply_transition(t, State.CLAIMED, Stage.IMPLEMENTING, ActorType.AGENT, "a1")
        with Session(engine) as s:
            s.add(t); s.add(e1); s.add(e2); s.commit()
        with Session(engine) as s:
            evs = s.query(TaskEvent).filter(TaskEvent.task_id == "p1").order_by(TaskEvent.id).all()
        assert len(evs) == 2
        assert evs[0].event_type == "claimed"
        assert evs[0].to_state == "claimed"
        assert evs[1].event_type == "stage_changed"
        assert evs[1].to_stage == "implementing"
        assert evs[1].actor_type == "agent"
