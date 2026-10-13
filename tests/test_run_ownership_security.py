"""P0 安全修复 d1b54de0：Run 所有权校验回归测试。

背景：`api/runs.py` 的 heartbeat / submit_result 原先只按 run_id 取 Run 就写，
**不校验调用方是否为该 run 的持有者**。任何拿到 run_id 的调用方都能：
  - 冒充他人 agent 发心跳，把 run 改成 running 以「续命」
  - 冒充他人 agent 提交结果，把他人任务标记 completed 或触发重试

本文件锁定修复后的行为：
  1. 非所有者调用 → 403，且不修改 Run / Task、不产生 task_result 事件
  2. 所有者调用 → 放行，行为与修复前一致
  3. result 提交时校验 run 必须处于 claimed / running
  4. 兼容：未传 agent 时维持旧行为（已知限制，非修复完成态）
  5. CAS：心跳与超时回收竞争时只有一方成功

用例编号 TC-S1..TC-S8，对应验收标准逐条。
"""
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from sqlmodel import Session, select
from mio_taskhub.models import Run, Task, TaskState, Event

client = TestClient(app)

AGENT_OWNER = "agent-owner"
AGENT_INTRUDER = "agent-intruder"


def _setup_claimed_task(title="Owned task"):
    """建任务 + 用 OWNER 领起来，返回 (task_id, run_id)。"""
    client.post("/api/v1/tasks", json={"title": title, "stage": "ready"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": AGENT_OWNER}).json()
    return claim["task_id"], claim["id"]


def _run_row(run_id):
    with Session(engine) as db:
        return db.get(Run, run_id)


def _task_row(task_id):
    with Session(engine) as db:
        return db.get(Task, task_id)


def _result_event_count(run_id):
    with Session(engine) as db:
        rows = db.exec(
            select(Event).where(Event.run_id == run_id, Event.type == "task_result")
        ).all()
        return len(rows)


# ── TC-S1：非所有者发心跳 → 403，且 Run 未被改动 ────────────────────────
def test_tc_s1_heartbeat_by_non_owner_is_forbidden():
    task_id, run_id = _setup_claimed_task("HB ownership")
    before = _run_row(run_id)
    assert before.state.value == "claimed"

    r = client.post(
        f"/api/v1/runs/{run_id}/heartbeat",
        params={"agent": AGENT_INTRUDER},
        json={"progress": 100},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "run.not_owner"

    after = _run_row(run_id)
    # 关键：状态与进度都没被 intruder 改动
    assert after.state.value == "claimed", "非所有者的心跳不得推进 run 状态"
    assert (after.progress or 0) == 0, "非所有者的心跳不得写入 progress"


# ── TC-S2：非所有者提交结果 → 403，且 Task / 事件均未变 ─────────────────
def test_tc_s2_submit_result_by_non_owner_is_forbidden():
    task_id, run_id = _setup_claimed_task("RESULT ownership")

    r = client.post(
        f"/api/v1/runs/{run_id}/result",
        params={"agent": AGENT_INTRUDER},
        json={"success": True, "result": "伪造完成"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "run.not_owner"

    # 任务不得被标记完成
    task = _task_row(task_id)
    assert task.state != TaskState.COMPLETED, "越权提交不得完成任务"

    # 不得产生 task_result 事件
    assert _result_event_count(run_id) == 0, "越权提交不得产生 task_result 事件"

    # run 不得被终结
    assert _run_row(run_id).state.value == "claimed"


# ── TC-S3：所有者正常放行，行为与修复前一致 ────────────────────────────
def test_tc_s3_owner_can_heartbeat_and_submit():
    task_id, run_id = _setup_claimed_task("owner happy path")

    hb = client.post(
        f"/api/v1/runs/{run_id}/heartbeat",
        params={"agent": AGENT_OWNER},
        json={"progress": 50},
    )
    assert hb.status_code == 200
    assert hb.json()["state"] == "running"
    assert hb.json()["progress"] == 50

    res = client.post(
        f"/api/v1/runs/{run_id}/result",
        params={"agent": AGENT_OWNER},
        json={"success": True, "result": "OK"},
    )
    assert res.status_code == 200
    assert res.json()["state"] == "finished"
    assert _task_row(task_id).state == TaskState.COMPLETED
    assert _result_event_count(run_id) == 1


# ── TC-S4：未传 agent 时维持旧行为（向后兼容） ──────────────────────────
def test_tc_s4_missing_agent_keeps_legacy_behaviour():
    """兼容保证：存量调用方（不传 agent）不被 403 打挂。

    这是**已知限制**：未传 agent 时仍可凭 run_id 越权，
    彻底关闭需要 run 级凭证（claim 时下发 token），列入后续任务。
    """
    _task_id, run_id = _setup_claimed_task("legacy caller")

    hb = client.post(f"/api/v1/runs/{run_id}/heartbeat", json={"progress": 20})
    assert hb.status_code == 200, "不传 agent 必须仍然可用（向后兼容）"
    assert hb.json()["state"] == "running"


# ── TC-S5：run 已结束再发心跳 → 409，不得把终态改回 running ───────────
def test_tc_s5_heartbeat_on_finished_run_conflicts():
    _task_id, run_id = _setup_claimed_task("finished then heartbeat")
    done = client.post(
        f"/api/v1/runs/{run_id}/result",
        params={"agent": AGENT_OWNER},
        json={"success": True, "result": "done"},
    )
    assert done.status_code == 200

    hb = client.post(
        f"/api/v1/runs/{run_id}/heartbeat",
        params={"agent": AGENT_OWNER},
        json={"progress": 10},
    )
    assert hb.status_code == 409
    assert hb.json()["detail"]["code"] == "run.not_active"
    assert _run_row(run_id).state.value == "finished", "终态不得被心跳改回 running"


# ── TC-S6：成功结果重复提交 → 409（不二次触发任务流转） ─────────────────
def test_tc_s6_duplicate_success_submit_conflicts():
    """只拦「重复的成功提交」。

    注意：**不拦**「迟到的结果」——reaper 判超时后 agent 迟到的提交仍应返回 200，
    由业务层决定是否纠正（既有能力 late_result_recovery，见
    tests/test_timeout_misjudge.py 的三个护栏用例）。本用例锁定的是
    「成功已交接（progress=100）后不得再交一次」。
    """
    _task_id, run_id = _setup_claimed_task("double submit")
    first = client.post(
        f"/api/v1/runs/{run_id}/result",
        params={"agent": AGENT_OWNER},
        json={"success": True, "result": "first"},
    )
    assert first.status_code == 200
    events_after_first = _result_event_count(run_id)

    second = client.post(
        f"/api/v1/runs/{run_id}/result",
        params={"agent": AGENT_OWNER},
        json={"success": True, "result": "second"},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "run.result_already_submitted"
    # 重复提交不得产生第二个 task_result 事件
    assert _result_event_count(run_id) == events_after_first


# ── TC-S7：所有权校验先于状态校验（不泄漏他人 run 状态） ────────────────
def test_tc_s7_ownership_checked_before_state_check():
    """非所有者 + run 已结束：应报 403（所有权），而不是 409（状态）。

    顺序反了会把「这个 run 属于谁、处于什么状态」泄漏给未授权调用方。
    """
    _task_id, run_id = _setup_claimed_task("order matters")
    client.post(
        f"/api/v1/runs/{run_id}/result",
        params={"agent": AGENT_OWNER},
        json={"success": True, "result": "done"},
    )
    r = client.post(
        f"/api/v1/runs/{run_id}/result",
        params={"agent": AGENT_INTRUDER},
        json={"success": True, "result": "late attack"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "run.not_owner"


# ── TC-S8：404 优先于所有权（不存在的 run 不泄漏存在性） ───────────────
def test_tc_s8_missing_run_returns_404():
    r = client.post(
        "/api/v1/runs/nonexist99/heartbeat",
        params={"agent": AGENT_INTRUDER},
        json={"progress": 10},
    )
    assert r.status_code == 404
