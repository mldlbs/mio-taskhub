# tests/test_agent_heartbeat.py
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Agent, AgentStatus

client = TestClient(app)


def _agent(name):
    with Session(engine) as s:
        return s.get(Agent, name)


def test_heartbeat_upsert_registers_new_agent():
    r = client.post("/api/v1/agents/heartbeat", json={"name": "hup"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "online"
    a = _agent("hup")
    assert a is not None
    assert a.status == AgentStatus.ONLINE
    assert a.last_heartbeat is not None


def test_heartbeat_refreshes_existing_agent():
    client.post("/api/v1/agents/register", json={"name": "hexist", "agent_type": "t"})
    with Session(engine) as s:
        a = s.get(Agent, "hexist")
        a.status = AgentStatus.OFFLINE
        a.last_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)
        s.add(a); s.commit()
    r = client.post("/api/v1/agents/heartbeat", json={"name": "hexist"})
    assert r.status_code == 200
    a = _agent("hexist")
    assert a.status == AgentStatus.ONLINE
    hb = a.last_heartbeat
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    assert (datetime.now(timezone.utc) - hb).total_seconds() < 10


def test_heartbeat_does_not_write_event():
    client.post("/api/v1/agents/heartbeat", json={"name": "hevent"})
    ev = client.get("/api/v1/events", params={"after_seq": 0}).json()
    types = {e["type"] for e in ev["events"]}
    assert "agent_heartbeat" not in types


def test_heartbeat_requires_name():
    r = client.post("/api/v1/agents/heartbeat", json={})
    assert r.status_code in (400, 422)


import mio_taskhub.background as background


def _mk_agent(name, status=AgentStatus.ONLINE, hb_age_sec=None):
    with Session(engine) as s:
        a = Agent(name=name, agent_type="t", status=status)
        if hb_age_sec is not None:
            a.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=hb_age_sec)
        else:
            a.last_heartbeat = datetime.now(timezone.utc)
        s.add(a); s.commit()


def _agent_status(name):
    with Session(engine) as s:
        return s.get(Agent, name).status


def test_stale_agent_marked_offline():
    _mk_agent("stale1", hb_age_sec=600)  # 10 分钟未心跳
    background._mark_stale_agents()
    assert _agent_status("stale1") == AgentStatus.OFFLINE


def test_fresh_agent_stays_online():
    _mk_agent("fresh1", hb_age_sec=10)
    background._mark_stale_agents()
    assert _agent_status("fresh1") == AgentStatus.ONLINE


def test_offline_agent_not_touched():
    _mk_agent("off1", status=AgentStatus.OFFLINE, hb_age_sec=9999)
    background._mark_stale_agents()
    assert _agent_status("off1") == AgentStatus.OFFLINE


def test_stale_agent_no_longer_assigned():
    _mk_agent("stale2", hb_age_sec=600)
    client.post("/api/v1/tasks", json={"title": "stale-task", "stage": "ready"})
    background._mark_stale_agents()
    background._assign_to_idle_agents()
    with Session(engine) as s:
        from mio_taskhub.models import Task, Run, TaskState
        t = s.exec(select(Task).where(Task.title == "stale-task")).first()
        assert t.state == TaskState.QUEUED  # 离线 agent 不再被分配


def test_agent_offline_recycles_run():
    """agent OFFLINE 后其 run 被 _on_timeout 回收（即使 task.timeout_min 很大）。"""
    _mk_agent("zombie", status=AgentStatus.OFFLINE)
    from mio_taskhub.models import Task, TaskState, Run, RunState, TaskStage
    with Session(engine) as s:
        t = Task(title="zombie-task", stage=TaskStage.READY, timeout_min=600)  # 10 分钟超时
        s.add(t); s.commit(); s.refresh(t)
        tid = t.id
    claim = client.post("/api/v1/tasks/claim", params={"agent": "zombie"}).json()
    rid = claim["id"]
    # 人为制造 run 心跳过期（但未到 task.timeout_min）
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=200)
        s.add(run); s.commit()
    background._on_timeout(rid, tid)
    with Session(engine) as s:
        run = s.get(Run, rid)
        assert run.state == RunState.FINISHED
        task = s.get(Task, tid)
        assert task.state == TaskState.QUEUED


def test_scheduler_tick_runs_all_three():
    _mk_agent("tick-agent", hb_age_sec=600)  # stale
    client.post("/api/v1/tasks", json={"title": "tick-task", "stage": "ready"})
    background._scheduler_tick()
    assert _agent_status("tick-agent") == AgentStatus.OFFLINE


def test_sweep_recycles_offline_agent_run_despite_large_timeout():
    """端到端：agent OFFLINE 时，即使 task.timeout_min 很大，sweep 也回收其 run。"""
    _mk_agent("sweep-zombie", status=AgentStatus.OFFLINE)
    from mio_taskhub.models import Task, TaskStage, TaskState, Run, RunState
    with Session(engine) as s:
        t = Task(title="sweep-zombie-task", stage=TaskStage.READY, timeout_min=600)
        s.add(t); s.commit(); s.refresh(t)
        tid = t.id
    claim = client.post("/api/v1/tasks/claim", params={"agent": "sweep-zombie"}).json()
    rid = claim["id"]
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=200)  # 未到 run 超时
        s.add(run); s.commit()
    # 直接跑 sweep（走真实触发路径）
    sweep = background.HeartbeatSweep(get_runs=background._get_runs,
                                  on_timeout=background._on_timeout, on_alive=lambda r: None)
    sweep._sweep()
    with Session(engine) as s:
        run = s.get(Run, rid)
        assert run.state == RunState.FINISHED
        task = s.get(Task, tid)
        assert task.state == TaskState.QUEUED


# ── 误判防线：agent OFFLINE 不得旁路 run 心跳新鲜度 ──────────────────────
# 2026-09-17 实测：13 个 task 在 agent 仍在正常上报 progress 时被判 FAILED，
# 根因是 _sweep 只看 agent 级 OFFLINE 就判死（agent 心跳 180s 超时，而 agent
# 只在 claim 前心跳一次）。run 级心跳新鲜时必须保持存活。

def _claim_offline_agent_task(agent_name, title, timeout_min=None):
    from mio_taskhub.models import Task, TaskStage
    _mk_agent(agent_name, status=AgentStatus.OFFLINE)
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage.READY, timeout_min=timeout_min)
        s.add(t); s.commit(); s.refresh(t)
        tid = t.id
    claim = client.post("/api/v1/tasks/claim", params={"agent": agent_name}).json()
    return tid, claim["id"]


def test_sweep_keeps_fresh_run_when_agent_offline():
    """agent OFFLINE 但 run 刚心跳过 → 不得判死（生产误判的核心场景）。"""
    from mio_taskhub.models import Task, Run, RunState, TaskState
    tid, rid = _claim_offline_agent_task("fresh-offline", "fresh-offline-task", 600)

    timed_out = []
    sweep = background.HeartbeatSweep(get_runs=background._get_runs,
                                      on_timeout=lambda r, t: timed_out.append(r),
                                      on_alive=lambda r: None)
    sweep._sweep()  # run.last_heartbeat = claim 时刻（刚刚）

    assert rid not in timed_out
    with Session(engine) as s:
        assert s.get(Run, rid).state in (RunState.CLAIMED, RunState.RUNNING)
        assert s.get(Task, tid).state != TaskState.FAILED


def test_sweep_keeps_run_within_baseline_when_agent_offline():
    """run 心跳落后 71s（实测生产值）仍在基线内 → 不得判死。"""
    from mio_taskhub.models import Task, Run, RunState
    tid, rid = _claim_offline_agent_task("baseline-offline", "baseline-offline-task", 600)
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=71)
        s.add(run); s.commit()

    timed_out = []
    sweep = background.HeartbeatSweep(get_runs=background._get_runs,
                                      on_timeout=lambda r, t: timed_out.append(r),
                                      on_alive=lambda r: None)
    sweep._sweep()
    assert rid not in timed_out
    with Session(engine) as s:
        assert s.get(Run, rid).state in (RunState.CLAIMED, RunState.RUNNING)


def test_offline_agent_reclaim_capped_at_baseline():
    """agent OFFLINE 时有效超时被收紧到基线：落后 130s 仍超出基线 → 回收。"""
    from mio_taskhub.models import Task, Run, RunState, TaskState
    tid, rid = _claim_offline_agent_task("capped-offline", "capped-offline-task", 600)
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=130)
        s.add(run); s.commit()

    timed_out = []
    sweep = background.HeartbeatSweep(get_runs=background._get_runs,
                                      on_timeout=lambda r, t: timed_out.append(r),
                                      on_alive=lambda r: None)
    sweep._sweep()
    assert rid in timed_out
