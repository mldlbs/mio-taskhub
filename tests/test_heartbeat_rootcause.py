# -*- coding: utf-8 -*-
"""心跳失败根因修复（task 0712a33e / P1-2）。

覆盖：① 窗口不再无条件收窄到 120s（在工作 vs 从未启动）；② never_started 干净 requeue
且不消耗 attempt；③ requeue 后可领取（Verify）；④ 真超时仍准时回收。
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.models import (
    Agent, AgentStatus, Run, RunState, Task, TaskStage, TaskState,
)
from mio_taskhub.heartbeat import HeartbeatSweep, RunInfo
from mio_taskhub import background as bg


def _runinfo(**kw):
    base = dict(run_id="r1", task_id="t1", agent_name="a1", state=RunState.RUNNING,
                last_heartbeat=0.0, attempt=1, max_retries=3,
                timeout_seconds=300, agent_offline=False, progress=0)
    base.update(kw)
    return RunInfo(**base)


# ---------- ① 窗口自适应 ----------

def test_window_not_shrunk_to_120_when_working():
    """agent OFFLINE 但已上报进度 → 不得收到 120s（应保留任务窗口，最多 2×offline）。"""
    sweep = HeartbeatSweep()
    r = _runinfo(agent_offline=True, progress=50, timeout_seconds=300)
    # offline 上限 120 → 放宽到 240；任务窗口 300 → min = 240，绝非 120
    assert sweep.effective_timeout(r) == 240


def test_window_shrunk_when_never_started():
    """从未心跳（progress=0）+ OFFLINE → 快速回收到 offline 上限。"""
    sweep = HeartbeatSweep()
    r = _runinfo(agent_offline=True, progress=0, timeout_seconds=300)
    assert sweep.effective_timeout(r) == 120


def test_window_keeps_task_budget_when_online():
    sweep = HeartbeatSweep()
    r = _runinfo(agent_offline=False, progress=0, timeout_seconds=5400)
    assert sweep.effective_timeout(r) == 5400


def test_long_task_working_not_killed_at_120s():
    """长任务（est=90min→timeout 5400）在工作：即使 agent offline，120s 也不判死。"""
    sweep = HeartbeatSweep()
    now = 1_000_000.0
    r = _runinfo(agent_offline=True, progress=10, timeout_seconds=5400,
                 last_heartbeat=now - 130)  # 130s 无心跳
    assert sweep.effective_timeout(r) > 130, "130s 不该判死在工作中的长任务"


# ---------- ② never_started 干净 requeue ----------

def _mk(db, *, progress, attempt, max_retries, agent_status=AgentStatus.OFFLINE, last_hb_delta=200):
    agent = db.exec(select(Agent).where(Agent.name == "hb-agent")).first()
    if not agent:
        agent = Agent(name="hb-agent", agent_type="cli", status=agent_status)
        db.add(agent)
    else:
        agent.status = agent_status
    task = Task(id=str(uuid.uuid4())[:8], title="hb task", state=TaskState.RUNNING,
                stage=TaskStage.IMPLEMENTING, attempt=attempt, max_retries=max_retries)
    db.add(task)
    db.commit()
    db.refresh(task)
    run = Run(id=str(uuid.uuid4())[:8], task_id=task.id, agent_name="hb-agent",
              state=RunState.RUNNING, attempt=attempt, progress=progress,
              started_at=datetime.now(timezone.utc) - timedelta(seconds=last_hb_delta),
              last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=last_hb_delta))
    db.add(run)
    db.commit()
    db.refresh(run)
    return task, run


def test_never_started_requeues_without_consuming_attempt():
    with Session(engine) as db:
        task, run = _mk(db, progress=0, attempt=3, max_retries=3)  # attempt 已到上限
        tid, rid, att_before = task.id, run.id, task.attempt
        bg._on_timeout(rid, tid)
        db.expire_all()
        t = db.get(Task, tid)
        r = db.get(Run, rid)
    assert r.state == RunState.FINISHED and r.exit_code == 1
    assert t.state == TaskState.QUEUED, "never_started 应干净重入队列，而非直接 FAILED"
    assert t.stage == TaskStage.READY
    assert t.attempt == att_before, "never_started 不应消耗 attempt"
    assert t.bounce_count == 1, "never_started 应计一次 bounce"


def test_never_started_bounded_eventually_fails():
    """有界：bounce_count 达 max_retries*2 后判 FAILED，避免 spawn 永久损坏导致空转。"""
    with Session(engine) as db:
        task, run = _mk(db, progress=0, attempt=1, max_retries=1)  # max_bounces = 2
        task.bounce_count = 2  # 已达上限
        db.add(task)
        db.commit()
        tid, rid = task.id, run.id
        bg._on_timeout(rid, tid)
        db.expire_all()
        t = db.get(Task, tid)
    assert t.state == TaskState.FAILED


def test_real_timeout_still_fails_at_max_retries():
    """有进度（真在工作后超时）+ 已达上限 → 仍走 FAILED。"""
    with Session(engine) as db:
        task, run = _mk(db, progress=50, attempt=3, max_retries=3, agent_status=AgentStatus.ONLINE)
        tid, rid = task.id, run.id
        bg._on_timeout(rid, tid)
        db.expire_all()
        t = db.get(Task, tid)
    assert t.state == TaskState.FAILED


def test_real_timeout_requeues_below_max_retries():
    with Session(engine) as db:
        task, run = _mk(db, progress=50, attempt=1, max_retries=3, agent_status=AgentStatus.ONLINE)
        tid, rid = task.id, run.id
        bg._on_timeout(rid, tid)
        db.expire_all()
        t = db.get(Task, tid)
    assert t.state == TaskState.QUEUED


# ---------- reaper 决策证据（task 79b856ef）----------

def test_reaper_emits_never_started_evidence():
    from mio_taskhub.models import Event
    with Session(engine) as db:
        task, run = _mk(db, progress=0, attempt=1, max_retries=3)
        tid, rid = task.id, run.id
        bg._on_timeout(rid, tid)
    with Session(engine) as db:
        ev = db.exec(select(Event).where(Event.type == "reaper_decision",
                                         Event.entity_id == rid)).first()
    assert ev is not None
    import json as _j
    p = _j.loads(ev.payload)
    assert p["reaper_kind"] == "never_started"
    assert p["run_id"] == rid and p["task_id"] == tid
    for k in ("agent_id", "agent_status", "progress", "last_heartbeat",
              "heartbeat_lag_seconds", "effective_timeout_seconds", "decision_reason", "outcome"):
        assert k in p, k


def test_reaper_emits_agent_offline_evidence():
    from mio_taskhub.models import Event
    with Session(engine) as db:
        task, run = _mk(db, progress=50, attempt=1, max_retries=3,
                        agent_status=AgentStatus.OFFLINE)
        tid, rid = task.id, run.id
        bg._on_timeout(rid, tid)
    with Session(engine) as db:
        ev = db.exec(select(Event).where(Event.type == "reaper_decision",
                                         Event.entity_id == rid)).first()
    import json as _j
    p = _j.loads(ev.payload)
    assert p["reaper_kind"] == "agent_offline"
    assert p["progress"] == 50
    assert str(p["agent_status"]).lower() == "offline"
