# tests/test_timeout_misjudge.py
"""看门狗超时误判的防线测试。

背景（2026-09-17 实测）：mio-taskhub 库里 18 个 FAILED 任务中有 12 个是误判 ——
agent 仍在正常上报 progress、随后还成功提交了 result，任务却在它跑的过程中被判
FAILED。根因三处：

1. `_sweep` 只看 agent 级 OFFLINE 就判死，旁路了 run 级心跳新鲜度
   （agent 心跳 180s 超时，而 agent 往往只在 claim 前心跳一次）
2. 系统侧状态迁移丢弃了 `apply_transition` 返回的 TaskEvent → 判死不留痕
3. FAILED 是终态，agent 随后成功提交的 result 无法回写 → 留下
   task=FAILED + run=成功/exit0 的矛盾终态

本文件覆盖这三条防线的回归。
"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import mio_taskhub.background as background
from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import (
    Agent, AgentStatus, Run, RunState, Task, TaskEvent, TaskStage, TaskState,
)

client = TestClient(app)


def _mk_agent(name, status=AgentStatus.ONLINE):
    with Session(engine) as s:
        s.add(Agent(name=name, agent_type="test", status=status,
                    last_heartbeat=datetime.now(timezone.utc)))
        s.commit()


def _claim(agent, title, max_retries=1, timeout_min=600):
    _mk_agent(agent)
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage.READY,
                 max_retries=max_retries, timeout_min=timeout_min)
        s.add(t); s.commit(); s.refresh(t)
        tid = t.id
    claim = client.post("/api/v1/tasks/claim", params={"agent": agent}).json()
    rid = claim["id"]
    # 让任务进入 RUNNING（真实路径：agent 上报心跳）
    client.post(f"/api/v1/runs/{rid}/heartbeat", json={"progress": 50})
    return tid, rid


def _timeout_kill(rid, tid, agent, hb_age_seconds=200):
    """把 agent 标 OFFLINE + run 心跳做旧，然后走真实判死路径。"""
    with Session(engine) as s:
        a = s.get(Agent, agent)
        a.status = AgentStatus.OFFLINE
        a.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=600)
        run = s.get(Run, rid)
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=hb_age_seconds)
        s.add(a); s.add(run); s.commit()
    background._on_timeout(rid, tid)


# ── 防线 2：系统侧状态迁移必须留痕 ────────────────────────────────────

def test_timeout_kill_records_taskevent():
    tid, rid = _claim("trace-agent", "trace-task")
    _timeout_kill(rid, tid, "trace-agent")

    with Session(engine) as s:
        assert s.get(Task, tid).state == TaskState.FAILED
        events = s.exec(
            select(TaskEvent).where(TaskEvent.task_id == tid)
            .order_by(TaskEvent.created_at)
        ).all()
    failed = [e for e in events if e.to_state == "failed"]
    assert failed, f"超时判死没有留下 taskevent：{[ (e.event_type, e.to_state) for e in events ]}"
    last = failed[-1]
    assert last.actor_type == "system"
    assert last.reason.startswith("agent_offline:")
    assert last.from_state == "running"


def test_requeue_path_records_taskevent_and_reclaims_run():
    """重试额度未耗尽时：任务回队列、run 被回收、且留痕（RUNNING→QUEUED 此前非法）。"""
    tid, rid = _claim("requeue-agent", "requeue-task", max_retries=3)
    _timeout_kill(rid, tid, "requeue-agent")

    with Session(engine) as s:
        assert s.get(Run, rid).state == RunState.FINISHED
        task = s.get(Task, tid)
        assert task.state == TaskState.QUEUED
        assert task.stage == TaskStage.READY
        events = s.exec(
            select(TaskEvent).where(TaskEvent.task_id == tid)
            .order_by(TaskEvent.created_at)
        ).all()
    assert any(e.to_state == "queued" and e.reason.startswith("agent_offline:") for e in events)


# ── 防线 3：迟到的成功结果可以纠正系统超时误判 ─────────────────────────

def test_late_success_recovers_system_timeout_failure():
    tid, rid = _claim("late-agent", "late-task")
    _timeout_kill(rid, tid, "late-agent")
    with Session(engine) as s:
        assert s.get(Task, tid).state == TaskState.FAILED

    # agent 其实干完了，正常提交成功结果
    r = client.post(f"/api/v1/runs/{rid}/result",
                    json={"success": True, "result": "全部完成，测试全绿"})
    assert r.status_code == 200
    body = r.json()
    assert body["task_state"] == "completed"

    with Session(engine) as s:
        task = s.get(Task, tid)
        assert task.state == TaskState.COMPLETED, "系统超时误判应被迟到成功结果纠正"
        assert task.stage == TaskStage.REVIEW
        events = s.exec(
            select(TaskEvent).where(TaskEvent.task_id == tid)
            .order_by(TaskEvent.created_at)
        ).all()
    assert any("late_result_recovery" in (e.reason or "") for e in events)


def test_late_success_rejected_for_agent_reported_failure():
    """agent 自己报的失败不允许被另一个迟到成功结果翻案。"""
    tid, rid = _claim("honest-agent", "honest-task")
    r = client.post(f"/api/v1/runs/{rid}/result",
                    json={"success": False, "result": "接口没跑通"})
    assert r.status_code == 200
    with Session(engine) as s:
        assert s.get(Task, tid).state == TaskState.FAILED

    # 另一路迟到的成功结果不得把任务改判
    r2 = client.post(f"/api/v1/runs/{rid}/result",
                     json={"success": True, "result": "其实好了"})
    assert r2.status_code == 200
    with Session(engine) as s:
        assert s.get(Task, tid).state == TaskState.FAILED


def test_late_success_on_unfinished_task_unaffected():
    """正常路径不受影响：任务未 FAILED 时一切照旧。"""
    tid, rid = _claim("normal-agent", "normal-task")
    r = client.post(f"/api/v1/runs/{rid}/result",
                    json={"success": True, "result": "done"})
    assert r.status_code == 200
    assert r.json()["task_state"] == "completed"
    with Session(engine) as s:
        task = s.get(Task, tid)
        assert task.state == TaskState.COMPLETED
        assert task.stage == TaskStage.REVIEW


# ── 防线 1：agent OFFLINE 不得旁路 run 心跳新鲜度（单元层） ──────────────

def test_effective_timeout_capped_when_agent_offline():
    from mio_taskhub.heartbeat import (
        RunInfo, HeartbeatSweep, AGENT_OFFLINE_TIMEOUT_SECONDS,
    )
    sweep = HeartbeatSweep()
    offline = RunInfo(run_id="r", task_id="t", agent_name="a",
                      state=RunState.RUNNING, last_heartbeat=0.0,
                      attempt=1, max_retries=3, timeout_seconds=600,
                      agent_offline=True)
    online = RunInfo(run_id="r", task_id="t", agent_name="a",
                     state=RunState.RUNNING, last_heartbeat=0.0,
                     attempt=1, max_retries=3, timeout_seconds=600,
                     agent_offline=False)
    assert sweep.effective_timeout(offline) == AGENT_OFFLINE_TIMEOUT_SECONDS
    assert sweep.effective_timeout(online) == 600


def test_effective_timeout_never_below_task_config():
    """任务显式配了更短超时时，agent OFFLINE 不应把上限抬高。"""
    from mio_taskhub.heartbeat import RunInfo, HeartbeatSweep
    sweep = HeartbeatSweep()
    short = RunInfo(run_id="r", task_id="t", agent_name="a",
                    state=RunState.RUNNING, last_heartbeat=0.0,
                    attempt=1, max_retries=3, timeout_seconds=30,
                    agent_offline=True)
    assert sweep.effective_timeout(short) == 30
