import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mio_taskhub'))


@pytest.fixture(autouse=True)
def setup_db():
    from mio_taskhub.db import init_db
    init_db()
    yield


def _seed_task(task_id, state, minutes_ago=120, retry_count=0, max_retries=3,
               stage=None):
    """写入一个「最后推进于 minutes_ago 分钟前」的任务。"""
    from sqlmodel import Session
    from mio_taskhub.db import engine as db_engine
    from mio_taskhub.models import Task, TaskStage
    from mio_taskhub.utils import _now

    ts = _now() - timedelta(minutes=minutes_ago)
    with Session(db_engine) as session:
        session.add(Task(
            id=task_id,
            title=f"task {task_id}",
            description="",
            state=state,
            stage=stage or TaskStage.IMPLEMENTING,
            retry_count=retry_count,
            max_retries=max_retries,
            created_at=ts,
            last_transition_at=ts,
        ))
        session.commit()


def test_remediation_engine_stalls_detected():
    from mio_taskhub.observability.remediation import RemediationEngine
    engine = RemediationEngine()
    actions = engine.evaluate_stalled_tasks()
    assert isinstance(actions, list)


def test_stalled_detection_reports_actually_stuck_task():
    """RUNNING 且长时间未推进的任务必须被识别出来。

    旧实现因为 state 用小写字面量 + 对字符串取 .tzinfo，永远返回空列表，
    而旧断言只检查 `isinstance(actions, list)`，所以缺陷被测试掩盖了很久。
    """
    from mio_taskhub.models import TaskState
    from mio_taskhub.observability.remediation import RemediationEngine

    _seed_task("stuck-running", TaskState.RUNNING, minutes_ago=120)

    actions = RemediationEngine().evaluate_stalled_tasks()

    assert [a["task_id"] for a in actions] == ["stuck-running"]
    action = actions[0]
    assert action["type"] == "requeue_stuck_task"
    assert action["state"] == "RUNNING"
    assert action["age_minutes"] > 100


def test_queued_task_is_not_treated_as_stuck():
    """排队等待领取不是「卡住」，不能被自动重排。"""
    from mio_taskhub.models import TaskState
    from mio_taskhub.observability.remediation import RemediationEngine

    _seed_task("waiting-queued", TaskState.QUEUED, minutes_ago=60 * 24 * 30)

    actions = RemediationEngine().evaluate_stalled_tasks()

    assert actions == []


def test_stalled_detection_stops_when_retry_budget_exhausted():
    """重试额度用完的任务不再重复重排，避免每 2 分钟无限抖动。"""
    from mio_taskhub.models import TaskState
    from mio_taskhub.observability.remediation import RemediationEngine

    _seed_task("no-budget", TaskState.RUNNING, minutes_ago=120,
               retry_count=3, max_retries=3)
    _seed_task("has-budget", TaskState.RUNNING, minutes_ago=120,
               retry_count=1, max_retries=3)

    actions = RemediationEngine().evaluate_stalled_tasks()

    assert [a["task_id"] for a in actions] == ["has-budget"]


def test_stalled_detection_ignores_recent_activity():
    from mio_taskhub.models import TaskState
    from mio_taskhub.observability.remediation import RemediationEngine

    _seed_task("just-started", TaskState.RUNNING, minutes_ago=2)

    assert RemediationEngine().evaluate_stalled_tasks() == []


def test_stall_threshold_is_configurable(monkeypatch):
    from mio_taskhub.models import TaskState
    from mio_taskhub.observability import remediation
    from mio_taskhub.observability.remediation import RemediationEngine

    _seed_task("half-hour-old", TaskState.CLAIMED, minutes_ago=20)

    # 默认 30 分钟阈值：20 分钟不算卡住
    assert RemediationEngine().evaluate_stalled_tasks() == []

    # 调低到 10 分钟：同一任务应被识别
    monkeypatch.setenv("MIO_TASKHUB_STALL_MINUTES", "10")
    assert [a["task_id"] for a in RemediationEngine().evaluate_stalled_tasks()] == ["half-hour-old"]

    # 非法值回退到默认阈值
    monkeypatch.setenv("MIO_TASKHUB_STALL_MINUTES", "abc")
    assert remediation.stall_threshold_minutes() == remediation.DEFAULT_STALL_MINUTES


def test_remediation_engine_thread_restart():
    from mio_taskhub.observability.remediation import RemediationEngine
    engine = RemediationEngine()
    result = engine.restart_stalled_thread("test_thread")
    assert isinstance(result, dict)
    assert "success" in result


def test_remediation_log():
    from mio_taskhub.observability.remediation import RemediationEngine
    engine = RemediationEngine()
    engine.log("test_action", "Test remediation", success=True)
    recent = engine.recent(limit=10)
    assert len(recent) >= 1


def test_remediation_requeue_task():
    from mio_taskhub.observability.remediation import RemediationEngine
    from mio_taskhub.models import Task, TaskState, TaskStage
    from sqlmodel import Session
    from mio_taskhub.db import engine as db_engine
    engine_obj = RemediationEngine()
    with Session(db_engine) as session:
        task = Task(
            id="test-remediation-1",
            title="Test Task",
            description="test",
            state=TaskState.QUEUED,
            stage=TaskStage.BRAINSTORMING,
        )
        session.add(task)
        session.commit()
    result = engine_obj.requeue_task("test-remediation-1")
    assert result["success"] == True
    with Session(db_engine) as session:
        session.delete(session.get(Task, "test-remediation-1"))
        session.commit()


def _seed_run(task_id, run_id, state, hb_minutes_ago):
    from sqlmodel import Session
    from mio_taskhub.db import engine as db_engine
    from mio_taskhub.models import Run
    from mio_taskhub.utils import _now
    ts = _now() - timedelta(minutes=hb_minutes_ago)
    with Session(db_engine) as session:
        session.add(Run(id=run_id, task_id=task_id, agent_name="a", state=state,
                        attempt=1, progress=10, started_at=ts, last_heartbeat=ts))
        session.commit()


def test_task_with_fresh_run_heartbeat_not_stalled():
    """P1-OBS：有心跳的长任务（>30min 未迁移状态）不应被误判为停滞。"""
    from mio_taskhub.models import TaskState, RunState
    from mio_taskhub.observability.remediation import RemediationEngine
    _seed_task("long-running", TaskState.RUNNING, minutes_ago=120)
    _seed_run("long-running", "r1", RunState.RUNNING, hb_minutes_ago=1)
    actions = RemediationEngine().evaluate_stalled_tasks()
    assert not any(a["task_id"] == "long-running" for a in actions)


def test_task_with_stale_run_heartbeat_is_stalled():
    """心跳也陈旧（>阈值）→ 仍应判停滞。"""
    from mio_taskhub.models import TaskState, RunState
    from mio_taskhub.observability.remediation import RemediationEngine
    _seed_task("dead-running", TaskState.RUNNING, minutes_ago=120)
    _seed_run("dead-running", "r2", RunState.RUNNING, hb_minutes_ago=120)
    actions = RemediationEngine().evaluate_stalled_tasks()
    assert any(a["task_id"] == "dead-running" for a in actions)


def test_requeue_task_closes_active_run():
    """P1-OBS（同 P6）：remediation 重排任务时应关闭其活跃 run。"""
    from sqlmodel import Session
    from mio_taskhub.db import engine as db_engine
    from mio_taskhub.models import TaskState, RunState, Run
    from mio_taskhub.observability.remediation import RemediationEngine
    _seed_task("requeue-me", TaskState.RUNNING, minutes_ago=120)
    _seed_run("requeue-me", "rr1", RunState.RUNNING, hb_minutes_ago=120)
    res = RemediationEngine().requeue_task("requeue-me")
    assert res["success"] is True
    with Session(db_engine) as s:
        assert s.get(Run, "rr1").state == RunState.FINISHED
