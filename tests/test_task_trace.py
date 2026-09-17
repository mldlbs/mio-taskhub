"""任务链路（task trace）测试。

背景：这两个函数过去只从原生 SQL 取时间，却按 datetime 使用（调 .isoformat()、
做减法），并且用 Session.exec(stmt, params) 这种不存在的调用签名，因此
GET /api/v1/traces 恒为空、GET /api/v1/traces/{id} 恒报错。
"""
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


def _seed(task_id, state, stage, *, running_minutes=60, completed=False, events=2):
    from sqlmodel import Session
    from mio_taskhub.db import engine as db_engine
    from mio_taskhub.models import Task, TaskEvent
    from mio_taskhub.utils import _now

    now = _now()
    start = now - timedelta(minutes=running_minutes)
    with Session(db_engine) as session:
        session.add(Task(
            id=task_id,
            title=f"trace {task_id}",
            description="",
            state=state,
            stage=stage,
            created_at=start,
            completed_at=now if completed else None,
        ))
        for i in range(events):
            session.add(TaskEvent(
                task_id=task_id,
                event_type="state_changed" if i else "created",
                to_state=getattr(state, "name", str(state)),
                to_stage=getattr(stage, "name", str(stage)),
                actor_type="agent",
                actor_id="agent-a",
                event_metadata={"seq": i},
                created_at=start + timedelta(minutes=10 * i),
            ))
        session.commit()


def test_get_task_trace_returns_spans_with_numeric_duration():
    from mio_taskhub.models import Task, TaskStage, TaskState
    from mio_taskhub.observability.task_trace import get_task_trace

    _seed("trace-1", TaskState.RUNNING, TaskStage.IMPLEMENTING, completed=False, events=3)

    result = get_task_trace("trace-1")

    assert "error" not in result, result
    assert result["task_id"] == "trace-1"
    assert result["current_state"] == "RUNNING"
    assert result["current_stage"] == "IMPLEMENTING"
    # 事件之间相隔 10 分钟
    assert len(result["spans"]) == 3
    assert result["spans"][0]["duration_seconds"] == 600.0
    # metadata 列（DB 列名 metadata / 属性名 event_metadata）必须还原成 dict
    assert result["spans"][0]["metadata"] == {"seq": 0}
    # created_at 必须是可解析的 ISO 字符串，而不是原始 SQLite 文本
    assert result["created_at"].startswith("20")
    assert "T" in result["created_at"]


def test_get_task_trace_reports_active_state_time():
    from mio_taskhub.models import TaskStage, TaskState
    from mio_taskhub.observability.task_trace import get_task_trace

    _seed("trace-2", TaskState.RUNNING, TaskStage.IMPLEMENTING, events=2)

    summary = get_task_trace("trace-2")["summary"]

    assert summary["transition_count"] == 2
    assert summary["time_in_states"]["RUNNING"] == 600.0
    assert summary["total_duration_seconds"] == 600.0


def test_get_task_trace_unknown_task():
    from mio_taskhub.observability.task_trace import get_task_trace

    assert "error" in get_task_trace("no-such-task")


def test_traces_summary_includes_finished_tasks():
    """仪表盘「任务链路」面板依赖这个列表，过去因为 state 大小写不匹配恒为空。"""
    from mio_taskhub.models import TaskStage, TaskState
    from mio_taskhub.observability.task_trace import get_task_traces_summary

    _seed("done-1", TaskState.COMPLETED, TaskStage.DONE, running_minutes=90, completed=True)
    _seed("failed-1", TaskState.FAILED, TaskStage.REVIEW, running_minutes=30)

    rows = {r["task_id"]: r for r in get_task_traces_summary()}

    assert set(rows) == {"done-1", "failed-1"}
    assert rows["done-1"]["duration_seconds"] == 5400.0
    assert rows["done-1"]["event_count"] == 2
    assert rows["done-1"]["created_at"].startswith("20")
    # FAILED 没有 completed_at，时长为 None 而不是崩溃
    assert rows["failed-1"]["duration_seconds"] is None


def test_traces_summary_excludes_unfinished_tasks():
    from mio_taskhub.models import TaskStage, TaskState
    from mio_taskhub.observability.task_trace import get_task_traces_summary

    _seed("still-queued", TaskState.QUEUED, TaskStage.READY)
    _seed("still-running", TaskState.RUNNING, TaskStage.IMPLEMENTING)

    assert get_task_traces_summary() == []
