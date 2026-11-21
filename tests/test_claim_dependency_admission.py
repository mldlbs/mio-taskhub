# -*- coding: utf-8 -*-
"""领取准入的 DAG 依赖与 fail-closed 回归测试。"""
import uuid

from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.api.claim import (
    _claim_ready,
    atomic_claim,
    claim_for,
    pick_candidate_task,
)
from mio_taskhub.models import Task, TaskStage, TaskState


def _id():
    return uuid.uuid4().hex[:8]


def _task(*, depends_on=None, state=TaskState.QUEUED,
          stage=TaskStage.READY, title="claim-dependency"):
    return Task(
        id=_id(),
        title=title,
        depends_on=list(depends_on or []),
        state=state,
        stage=stage,
    )


def test_explicit_task_id_cannot_bypass_unfinished_dependency():
    with Session(engine) as db:
        prerequisite = _task(state=TaskState.RUNNING, stage=TaskStage.IMPLEMENTING)
        dependent = _task(depends_on=[prerequisite.id])
        db.add(prerequisite)
        db.add(dependent)
        db.commit()

        assert claim_for("dep-explicit-" + _id(), db, task_id=dependent.id) is None
        db.refresh(dependent)
        assert dependent.state == TaskState.QUEUED


def test_automatic_claim_skips_task_with_unfinished_dependency():
    with Session(engine) as db:
        prerequisite = _task(state=TaskState.RUNNING, stage=TaskStage.IMPLEMENTING)
        dependent = _task(depends_on=[prerequisite.id])
        db.add(prerequisite)
        db.add(dependent)
        db.commit()

        assert claim_for("dep-auto-" + _id(), db) is None
        db.refresh(dependent)
        assert dependent.state == TaskState.QUEUED


def test_claim_allows_task_after_all_dependencies_complete():
    with Session(engine) as db:
        prerequisite = _task(state=TaskState.COMPLETED, stage=TaskStage.DONE)
        dependent = _task(depends_on=[prerequisite.id])
        db.add(prerequisite)
        db.add(dependent)
        db.commit()

        run = claim_for("dep-complete-" + _id(), db, task_id=dependent.id)
        assert run is not None
        assert run.task_id == dependent.id


def test_missing_dependency_fails_closed():
    with Session(engine) as db:
        dependent = _task(depends_on=["missing-" + _id()])
        db.add(dependent)
        db.commit()

        assert claim_for("dep-missing-" + _id(), db, task_id=dependent.id) is None
        db.refresh(dependent)
        assert dependent.state == TaskState.QUEUED


def test_atomic_claim_rechecks_dependency_after_candidate_selection():
    with Session(engine) as db:
        prerequisite = _task(state=TaskState.COMPLETED, stage=TaskStage.DONE)
        dependent = _task(depends_on=[prerequisite.id])
        db.add(prerequisite)
        db.add(dependent)
        db.commit()

        candidate = pick_candidate_task(db, None, dependent.id)
        assert candidate is not None

        # 模拟候选选出后依赖记录消失；最终 CAS 前必须重新拒绝。
        db.delete(prerequisite)
        db.commit()
        assert atomic_claim(db, "dep-race-" + _id(), candidate) is None
        db.refresh(dependent)
        assert dependent.state == TaskState.QUEUED


def test_readiness_exception_fails_closed_in_block_mode(monkeypatch):
    from mio_taskhub import completion_gate

    monkeypatch.setenv("MIO_CLAIM_GATE", "block")

    def explode(*args, **kwargs):
        raise RuntimeError("readiness backend unavailable")

    monkeypatch.setattr(completion_gate, "build_readiness", explode)
    with Session(engine) as db:
        task = _task()
        db.add(task)
        db.commit()
        assert _claim_ready(db, task) is False
