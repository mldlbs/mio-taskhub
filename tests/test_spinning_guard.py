# -*- coding: utf-8 -*-
"""自动建任务链路空转治理（task 18c2c6e9 / P1-1）。

覆盖：① 下游消费意愿检查（堆积超阈值则 skipped）；② 默认关闭开关；③ 历史归档脚本幂等可逆。
不真连外部：全部走内存/临时 DB。
"""
import os
import uuid

import pytest
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.models import (
    ScheduledJob, ScheduledJobActionType, ScheduledJobExecution,
    Task, TaskState, TaskStage,
)
from mio_taskhub.scheduling import cron_engine as ce
from mio_taskhub.seed import _idea_autogen_enabled, seed_idea_generate_job


def _mk_job(db, **cfg):
    job = ScheduledJob(
        id=str(uuid.uuid4())[:8],
        name="test-guard",
        cron_expr="0 0 * * *",
        action_type=ScheduledJobActionType.CREATE_TASK,
        action_config={"title": "[定时] 自动生成创意想法", **cfg},
        enabled=True,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _mk_pending(db, job, n):
    for _ in range(n):
        db.add(Task(
            id=str(uuid.uuid4())[:8],
            title="[定时] 自动生成创意想法",
            state=TaskState.QUEUED,
            stage=TaskStage.READY,
            labels=[f"cron:{job.id}"],
        ))
    db.commit()


# ---------- ① 消费意愿检查 ----------

def test_guard_skips_when_pending_over_threshold():
    with Session(engine) as db:
        job = _mk_job(db, max_pending_tasks=3)
        _mk_pending(db, job, 3)
        engine_ = ce.CronEngine(poll_interval=999)
        reason = engine_._should_skip_task_creation(job, db)
    assert reason and "堆积" in reason


def test_guard_allows_when_below_threshold():
    with Session(engine) as db:
        job = _mk_job(db, max_pending_tasks=3)
        _mk_pending(db, job, 2)
        engine_ = ce.CronEngine(poll_interval=999)
        assert engine_._should_skip_task_creation(job, db) is None


def test_guard_ignores_claimed_tasks():
    """已认领的任务不算「未消费堆积」。"""
    with Session(engine) as db:
        job = _mk_job(db, max_pending_tasks=2)
        _mk_pending(db, job, 2)
        # 把其中一条标记为已认领 → 剩 1 条未认领 < 阈值 2 → 不跳过
        t = db.exec(select(Task).where(Task.labels.contains(f"cron:{job.id}"))).first()
        from datetime import datetime, timezone
        t.claimed_at = datetime.now(timezone.utc)
        db.add(t)
        db.commit()
        engine_ = ce.CronEngine(poll_interval=999)
        assert engine_._should_skip_task_creation(job, db) is None


def test_guard_disabled_by_env(monkeypatch):
    monkeypatch.setenv("MIO_CRON_PENDING_GUARD", "0")
    with Session(engine) as db:
        job = _mk_job(db, max_pending_tasks=1)
        _mk_pending(db, job, 5)
        engine_ = ce.CronEngine(poll_interval=999)
        assert engine_._should_skip_task_creation(job, db) is None


def test_execute_job_records_skipped_status():
    """堆积超阈值时 _execute_job 记 skipped 且不建任务。"""
    with Session(engine) as db:
        job = _mk_job(db, max_pending_tasks=2)
        _mk_pending(db, job, 2)
        before = len(db.exec(select(Task)).all())
        ce.CronEngine(poll_interval=999)._execute_job(job.id, db)
        db.refresh(job)
        after = len(db.exec(select(Task)).all())
        ex = db.exec(
            select(ScheduledJobExecution).where(ScheduledJobExecution.job_id == job.id)
        ).all()
    assert after == before, "skipped 时不应新建任务"
    assert any(e.status == "skipped" for e in ex)
    assert job.last_status is not None


# ---------- ①b 生成类 webhook 的消费意愿检查 ----------

def _mk_webhook_job(db, url="http://127.0.0.1:48620/api/v1/ideas/templates/generate", **cfg):
    job = ScheduledJob(
        id=str(uuid.uuid4())[:8],
        name="test-idea-webhook",
        cron_expr="0 10 * * *",
        action_type=ScheduledJobActionType.WEBHOOK,
        action_config={"url": url, "method": "POST", **cfg},
        enabled=True,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _mk_new_ideas(db, n):
    from mio_taskhub.models import Idea, IdeaStatus
    for k in range(n):
        db.add(Idea(id=f"idea{k:04d}", title=f"auto idea {k}",
                    status=IdeaStatus.NEW, labels=["auto-generated"]))
    db.commit()


def test_webhook_guard_skips_when_unconsumed_ideas_pile_up():
    from mio_taskhub.models import Idea, IdeaStatus
    with Session(engine) as db:
        job = _mk_webhook_job(db, max_pending_ideas=3)
        _mk_new_ideas(db, 3)
        reason = ce.CronEngine(poll_interval=999)._should_skip_webhook_generation(job, db)
    assert reason and "未消费" in reason


def test_webhook_guard_allows_below_threshold():
    with Session(engine) as db:
        job = _mk_webhook_job(db, max_pending_ideas=3)
        _mk_new_ideas(db, 2)
        assert ce.CronEngine(poll_interval=999)._should_skip_webhook_generation(job, db) is None


def test_webhook_guard_ignores_non_generation_webhook():
    """非生成类 webhook（url 不含生成端点且未标记）不受保护。"""
    with Session(engine) as db:
        job = _mk_webhook_job(db, url="http://example.com/notify", max_pending_ideas=1)
        _mk_new_ideas(db, 5)
        assert ce.CronEngine(poll_interval=999)._should_skip_webhook_generation(job, db) is None


def test_webhook_guard_counts_only_unconsumed_new():
    """只有 NEW 状态且 auto-generated 的想法计入；已评审的不算。"""
    from mio_taskhub.models import Idea, IdeaStatus
    with Session(engine) as db:
        job = _mk_webhook_job(db, max_pending_ideas=2)
        db.add(Idea(id="done0001", title="reviewed", status=IdeaStatus.FERMENTING,
                    labels=["auto-generated"]))
        db.add(Idea(id="done0002", title="manual", status=IdeaStatus.NEW, labels=[]))
        db.commit()
        assert ce.CronEngine(poll_interval=999)._should_skip_webhook_generation(job, db) is None


# ---------- ② 默认关闭开关 ----------
def test_idea_autogen_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MIO_IDEA_AUTOGEN", raising=False)
    assert _idea_autogen_enabled() is False


@pytest.mark.parametrize("val", ["1", "auto", "on", "true", "yes"])
def test_idea_autogen_enabled_when_set(monkeypatch, val):
    monkeypatch.setenv("MIO_IDEA_AUTOGEN", val)
    assert _idea_autogen_enabled() is True


def test_seed_creates_disabled_job_by_default(monkeypatch):
    monkeypatch.delenv("MIO_IDEA_AUTOGEN", raising=False)
    with Session(engine) as db:
        seed_idea_generate_job(db)
        job = db.exec(select(ScheduledJob).where(ScheduledJob.name == "idea-generate")).first()
    assert job is not None
    assert job.enabled is False, "默认应关闭自动生成链"


def test_seed_disables_existing_enabled_job(monkeypatch):
    """已存在的开启态 idea-generate job，在默认关闭下应被关停（幂等）。"""
    monkeypatch.delenv("MIO_IDEA_AUTOGEN", raising=False)
    with Session(engine) as db:
        # init_db 已播种该 job（默认关闭）；先人为打开，验证 seed 会再次关停
        job = db.exec(select(ScheduledJob).where(ScheduledJob.name == "idea-generate")).first()
        assert job is not None, "init_db 应已播种 idea-generate"
        job.enabled = True
        db.add(job)
        db.commit()
        seed_idea_generate_job(db)
        db.refresh(job)
    assert job.enabled is False


# ---------- ③ 历史归档脚本 ----------

def test_archive_script_idempotent_and_reversible():
    import importlib.util
    import sys as _sys

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "archive_spinning_tasks.py",
    )
    spec = importlib.util.spec_from_file_location("archive_spinning_tasks", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["archive_spinning_tasks"] = mod
    spec.loader.exec_module(mod)

    with Session(engine) as db:
        db.add(Task(
            id="spin0001", title="[定时] 自动生成创意想法",
            state=TaskState.CANCELLED, stage=TaskStage.CANCELLED, labels=[],
        ))
        db.commit()

    assert mod.run(apply=True, undo=False) == 0
    with Session(engine) as db:
        t = db.get(Task, "spin0001")
        assert mod.ARCHIVE_LABEL in t.labels
    # 幂等：再跑一次不重复
    mod.run(apply=True, undo=False)
    with Session(engine) as db:
        t = db.get(Task, "spin0001")
        assert t.labels.count(mod.ARCHIVE_LABEL) == 1
    # 可逆
    mod.run(apply=True, undo=True)
    with Session(engine) as db:
        t = db.get(Task, "spin0001")
        assert mod.ARCHIVE_LABEL not in t.labels


def test_archive_script_dry_run_no_write():
    import importlib.util
    import sys as _sys

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "archive_spinning_tasks.py",
    )
    spec = importlib.util.spec_from_file_location("archive_spinning_tasks2", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["archive_spinning_tasks2"] = mod
    spec.loader.exec_module(mod)

    with Session(engine) as db:
        db.add(Task(id="spin0002", title="[定时] 自动生成创意想法",
                    state=TaskState.CANCELLED, stage=TaskStage.CANCELLED, labels=[]))
        db.commit()
    mod.run(apply=False, undo=False)
    with Session(engine) as db:
        assert mod.ARCHIVE_LABEL not in (db.get(Task, "spin0002").labels or [])


def test_archive_script_matches_cron_generated_queued_task():
    """label 判据：由 cron 生成（cron:<id> + auto）的未认领 QUEUED 任务也应被归档。"""
    import importlib.util
    import sys as _sys

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "archive_spinning_tasks.py",
    )
    spec = importlib.util.spec_from_file_location("archive_spinning_tasks3", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["archive_spinning_tasks3"] = mod
    spec.loader.exec_module(mod)

    with Session(engine) as db:
        db.add(Task(id="sync0001", title="每日数据同步任务", state=TaskState.QUEUED,
                    stage=TaskStage.READY, labels=["auto", "daily", "cron:a9cbd255"]))
        # 干扰项：手动任务（无 cron 标签）不应被归档
        db.add(Task(id="manual01", title="手动任务", state=TaskState.QUEUED,
                    stage=TaskStage.READY, labels=["urgent"]))
        db.commit()
    mod.run(apply=True, undo=False)
    with Session(engine) as db:
        assert mod.ARCHIVE_LABEL in (db.get(Task, "sync0001").labels or [])
        assert mod.ARCHIVE_LABEL not in (db.get(Task, "manual01").labels or [])
