# -*- coding: utf-8 -*-
"""T3.1 D1 生命周期化——超时宽限 + 用户可读诊断单测。"""
import os
from mio_taskhub.heartbeat import (
    HeartbeatSweep, RunInfo, DEFAULT_TIMEOUT_SECONDS,
    GRACE_PERIOD_SECONDS, AGENT_OFFLINE_TIMEOUT_SECONDS
)
from mio_taskhub.models import RunState


def test_default_timeout_is_360():
    """T3.1：默认超时上调至 360s（覆盖心跳抖动）。"""
    assert DEFAULT_TIMEOUT_SECONDS == 360


def test_grace_period_exists():
    """T3.1：宽限期常量存在。"""
    assert GRACE_PERIOD_SECONDS == 60


def test_effective_timeout_grace_online_progress():
    """T3.1：agent 在线 + progress>0 → base + grace。"""
    sweep = HeartbeatSweep(timeout_seconds=360, grace_period=60, agent_offline_timeout=120)
    run = RunInfo(
        run_id="r1", task_id="t1", agent_name="a1",
        state=RunState.RUNNING, last_heartbeat=0, attempt=0, max_retries=3,
        timeout_seconds=None, agent_offline=False, progress=50,
    )
    assert sweep.effective_timeout(run) == 420  # 360 + 60


def test_effective_timeout_offline_progress():
    """T3.1：agent OFFLINE + progress>0 → min(base, offline*2)。"""
    sweep = HeartbeatSweep(timeout_seconds=360, grace_period=60, agent_offline_timeout=120)
    run = RunInfo(
        run_id="r1", task_id="t1", agent_name="a1",
        state=RunState.RUNNING, last_heartbeat=0, attempt=0, max_retries=3,
        timeout_seconds=None, agent_offline=True, progress=50,
    )
    assert sweep.effective_timeout(run) == 240  # min(360, 240)


def test_effective_timeout_offline_no_progress():
    """T3.1：agent OFFLINE + progress=0 → min(base, offline)。"""
    sweep = HeartbeatSweep(timeout_seconds=360, grace_period=60, agent_offline_timeout=120)
    run = RunInfo(
        run_id="r1", task_id="t1", agent_name="a1",
        state=RunState.CLAIMED, last_heartbeat=0, attempt=0, max_retries=3,
        timeout_seconds=None, agent_offline=True, progress=0,
    )
    assert sweep.effective_timeout(run) == 120


def test_effective_timeout_online_no_progress():
    """T3.1：agent 在线 + progress=0 → base（不加 grace，never_started 快速回收）。"""
    sweep = HeartbeatSweep(timeout_seconds=360, grace_period=60, agent_offline_timeout=120)
    run = RunInfo(
        run_id="r1", task_id="t1", agent_name="a1",
        state=RunState.CLAIMED, last_heartbeat=0, attempt=0, max_retries=3,
        timeout_seconds=None, agent_offline=False, progress=0,
    )
    assert sweep.effective_timeout(run) == 360


def test_effective_timeout_task_specific():
    """T3.1：任务自带 timeout_seconds 时，grace 叠加在任务窗口上。"""
    sweep = HeartbeatSweep(timeout_seconds=360, grace_period=60, agent_offline_timeout=120)
    run = RunInfo(
        run_id="r1", task_id="t1", agent_name="a1",
        state=RunState.RUNNING, last_heartbeat=0, attempt=0, max_retries=3,
        timeout_seconds=600, agent_offline=False, progress=10,
    )
    assert sweep.effective_timeout(run) == 660  # 600 + 60


def test_env_grace_period(monkeypatch):
    """T3.1：环境变量可覆盖宽限期。"""
    monkeypatch.setenv("MIO_TASKHUB_GRACE_PERIOD_SECONDS", "90")
    from importlib import reload
    import mio_taskhub.heartbeat as hb
    reload(hb)
    assert hb.GRACE_PERIOD_SECONDS == 90
    reload(hb)  # 恢复


def test_diagnostic_hint_in_task_detail():
    """T3.1：FAILED 任务的 detail 响应含 diagnostic_hint 字段。"""
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskState, Run, RunState, Agent, AgentStatus
    from sqlmodel import Session
    from datetime import datetime, timezone

    client = TestClient(app)
    # 建任务
    r = client.post("/api/v1/tasks", json={"title": "diag-test", "stage": "ready"})
    tid = r.json()["id"]
    # 建 agent
    with Session(engine) as s:
        s.add(Agent(name="diag-agent", agent_type="", status=AgentStatus.ONLINE))
        # 建 run（agent offline 失败）
        s.add(Run(
            id="diag-run-1", task_id=tid, agent_name="diag-agent",
            state=RunState.FINISHED, attempt=1, progress=10,
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
            exit_code=1, result="agent offline",
        ))
        # 任务标记 FAILED
        t = s.get(Task, tid)
        t.state = TaskState.FAILED
        s.add(t)
        s.commit()
    # GET task detail
    resp = client.get(f"/api/v1/tasks/{tid}")
    assert resp.status_code == 200
    data = resp.json()
    assert "diagnostic_hint" in data
    assert data["diagnostic_hint"] is not None
    assert "agent" in data["diagnostic_hint"].lower() or "diag-agent" in data["diagnostic_hint"]


def test_diagnostic_hint_none_when_completed():
    """T3.1：COMPLETED 任务 diagnostic_hint 为 None。"""
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskState
    from sqlmodel import Session

    client = TestClient(app)
    r = client.post("/api/v1/tasks", json={"title": "diag-done", "stage": "ready"})
    tid = r.json()["id"]
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.state = TaskState.COMPLETED
        s.add(t)
        s.commit()
    resp = client.get(f"/api/v1/tasks/{tid}")
    assert resp.status_code == 200
    assert resp.json()["diagnostic_hint"] is None
