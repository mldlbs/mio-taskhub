# -*- coding: utf-8 -*-
"""Task.updated_at：创建默认值、状态迁移与领取写入。"""
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage, TaskState
from mio_taskhub.workflow.state_machine import ActorType, Stage, State
from mio_taskhub.workflow.transitions import apply_transition, record_post_claim


def _mk(title="t", state=TaskState.QUEUED, stage=TaskStage.READY) -> Task:
    t = Task(title=title, state=state, stage=stage)
    with Session(engine) as s:
        s.add(t)
        s.commit()
        s.refresh(t)
    return t


def test_task_created_sets_updated_at():
    t = _mk("created")
    assert t.updated_at is not None


def test_apply_transition_updates_updated_at():
    t = _mk("transition")
    _, event = apply_transition(
        t, State.CLAIMED, Stage.READY, ActorType.AGENT, "agent-x")
    assert event is not None
    assert t.updated_at is not None
    assert t.updated_at == t.last_transition_at


def test_record_post_claim_updates_updated_at():
    t = _mk("claim", state=TaskState.CLAIMED, stage=TaskStage.READY)
    event = record_post_claim(t, "agent-x")
    assert event is not None
    assert t.updated_at == t.last_transition_at
