# -*- coding: utf-8 -*-
"""claim 的 project 过滤测试（task 0fe91462）。

此前 project query 参数被静默忽略（pick_candidate_task 从不按它过滤），
导致 idle_worker --project / night_runner「项目范围」形同虚设。
"""
import uuid

from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.api.claim import claim_for, pick_candidate_task
from mio_taskhub.models import Task, TaskStage, TaskState


def _task(project, title="t", stage=TaskStage.READY, priority=0):
    return Task(id=str(uuid.uuid4())[:8], title=title, project=project,
                state=TaskState.QUEUED, stage=stage, priority=priority)


def test_claim_filters_by_project():
    with Session(engine) as db:
        a = _task("proj-a", "A")
        b = _task("proj-b", "B")
        db.add(a); db.add(b); db.commit()
        run = claim_for("w1", db, project_scope="proj-a")
        assert run is not None
        assert run.task_id == a.id


def test_claim_without_project_any():
    with Session(engine) as db:
        a = _task("proj-a", "A")
        db.add(a); db.commit()
        run = claim_for("w2", db)
        assert run is not None and run.task_id == a.id


def test_claim_no_match_returns_none():
    with Session(engine) as db:
        db.add(_task("proj-a", "A")); db.commit()
        assert claim_for("w3", db, project_scope="proj-zzz") is None


def test_claim_multi_project():
    with Session(engine) as db:
        a = _task("proj-a", "A")
        c = _task("proj-c", "C")
        db.add(a); db.add(c); db.commit()
        run = claim_for("w4", db, project_scope="proj-a,proj-c")
        assert run is not None
        assert run.task_id in (a.id, c.id)


def test_claim_project_does_not_match_empty_project_task():
    """限定了项目范围时，无 project 的任务不应被选中。"""
    with Session(engine) as db:
        db.add(_task("", "no-proj")); db.commit()
        assert claim_for("w5", db, project_scope="proj-a") is None


def test_pick_candidate_project_filter_direct():
    with Session(engine) as db:
        b = _task("proj-b", "B", priority=5)
        a = _task("proj-a", "A", priority=0)
        db.add(a); db.add(b); db.commit()
        cand = pick_candidate_task(db, None, None, "proj-a")
        assert cand is not None and cand.id == a.id
