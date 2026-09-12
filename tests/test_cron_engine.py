"""Tests for mio_taskhub.scheduling.cron_engine — CronEngine core logic."""
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest
from sqlmodel import Session, select

from mio_taskhub.db import engine as db_engine
from mio_taskhub.models import Event, ScheduledJob, ScheduledJobActionType


def test_validate_cron_valid():
    from mio_taskhub.scheduling.cron_engine import validate_cron
    assert validate_cron("0 * * * *") is True
    assert validate_cron("*/5 * * * *") is True
    assert validate_cron("0 9 * * 1-5") is True
    assert validate_cron("30 2 1 * *") is True


def test_validate_cron_invalid():
    from mio_taskhub.scheduling.cron_engine import validate_cron
    assert validate_cron("") is False
    assert validate_cron("invalid") is False
    assert validate_cron("* * *") is False
    assert validate_cron("60 * * * *") is False  # minute > 59


def test_compute_next_run():
    from mio_taskhub.scheduling.cron_engine import compute_next_run
    now = datetime(2026, 9, 7, 8, 0, 0, tzinfo=timezone.utc)
    nxt = compute_next_run("0 9 * * *", after=now)
    assert nxt.hour == 9
    assert nxt.minute == 0
    assert nxt > now


def test_compute_next_runs():
    from mio_taskhub.scheduling.cron_engine import compute_next_runs
    now = datetime(2026, 9, 7, 8, 0, 0, tzinfo=timezone.utc)
    runs = compute_next_runs("0 9 * * *", count=3, after=now)
    assert len(runs) == 3
    for r in runs:
        assert r.hour == 9
        assert r.minute == 0
    # 应该严格递增
    for i in range(1, len(runs)):
        assert runs[i] > runs[i - 1]


def test_cron_engine_start_stop():
    from mio_taskhub.scheduling.cron_engine import CronEngine
    engine = CronEngine(poll_interval=1)
    engine.start()
    assert engine._thread.is_alive()
    engine.stop()
    assert not engine._thread.is_alive()


def test_cron_engine_tick_no_jobs():
    from mio_taskhub.scheduling.cron_engine import CronEngine
    engine = CronEngine(poll_interval=1)
    # tick 不应该崩溃（即使没有 job）
    engine.tick()


def test_cron_engine_add_and_pause():
    from mio_taskhub.scheduling.cron_engine import CronEngine
    from mio_taskhub.models import ScheduledJob, ScheduledJobActionType
    from mio_taskhub.db import engine as db_engine
    from sqlmodel import Session

    engine_obj = CronEngine()
    job = ScheduledJob(
        id="test01",
        name="test job",
        cron_expr="0 9 * * *",
        action_type=ScheduledJobActionType.CREATE_TASK,
        action_config={},
        enabled=True,
    )
    engine_obj.add_job(job)

    # Save to DB so pause/resume can find it
    with Session(db_engine) as db:
        db.add(job)
        db.commit()

    # Pause
    paused = engine_obj.pause_job("test01")
    assert paused is not None
    assert paused.enabled is False
    assert paused.next_run_at is None

    # Resume
    resumed = engine_obj.resume_job("test01")
    assert resumed is not None
    assert resumed.enabled is True
    assert resumed.next_run_at is not None

    # Cleanup
    engine_obj.remove_job("test01")


def test_cron_job_event_persisted_to_db():
    """P0 回归：cron job 执行后 Event 必须持久化到 Event 表（不仅广播）。

    验证：
    - Event 表中存在 type=scheduled_job_executed 的记录
    - event.id 已生成（非 None）
    - payload 包含 status 和 action_type
    """
    from mio_taskhub.scheduling.cron_engine import CronEngine

    engine_obj = CronEngine()
    job = ScheduledJob(
        id="test-evt",
        name="event persist test",
        cron_expr="0 9 * * *",
        action_type=ScheduledJobActionType.CREATE_TASK,
        action_config={"title": "cron created task", "stage": "ready"},
        enabled=True,
    )
    engine_obj.add_job(job)
    with Session(db_engine) as db:
        db.add(job)
        db.commit()

    # 执行 job（触发 _execute_job → emit_event → commit）
    with Session(db_engine) as db:
        engine_obj._execute_job("test-evt", db)

    # 查询 Event 表，确认事件已持久化
    with Session(db_engine) as db:
        events = db.exec(
            select(Event).where(Event.type == "scheduled_job_executed")
        ).all()
        assert len(events) >= 1, "Event 表中未找到 scheduled_job_executed 事件"
        evt = events[-1]
        assert evt.id is not None, "event.id 不应为 None（未持久化）"
        assert evt.entity == "scheduled_job"
        assert evt.entity_id == "test-evt"
        payload = json.loads(evt.payload) if evt.payload else {}
        assert payload.get("status") == "ok"
        assert payload.get("action_type") == "create_task"

    # Cleanup
    engine_obj.remove_job("test-evt")
