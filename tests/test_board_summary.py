from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.models import TaskState

client = TestClient(app)


def _mk(title, stage="ready", priority=0, **kw):
    body = {"title": title, "stage": stage, "priority": priority}
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


def test_empty_db_zero_values():
    r = client.get("/api/v1/board/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["counts"]["brainstorming"] == 0
    assert data["counts"]["done"] == 0
    assert data["ready_queue"] == []
    assert data["running"] == []
    assert data["alerts"] == []
    assert data["recent_done"] == []
    assert any("无待办" in s for s in data["next_steps"])


def test_counts_by_stage():
    _mk("b", "brainstorming")
    _mk("d", "design")
    _mk("r1", "ready")
    _mk("r2", "ready")
    data = client.get("/api/v1/board/summary").json()
    assert data["counts"]["brainstorming"] == 1
    assert data["counts"]["design"] == 1
    assert data["counts"]["ready"] == 2


def test_ready_queue_sorted_by_priority():
    _mk("low", "ready", priority=0)
    _mk("high", "ready", priority=3)
    q = client.get("/api/v1/board/summary").json()["ready_queue"]
    assert [x["title"] for x in q] == ["high", "low"]
    assert q[0]["priority"] == 3


def test_ready_queue_skips_future_run_at():
    _mk("future", "ready", run_at="2999-01-01T00:00:00+00:00")
    _mk("now", "ready", run_at="2020-01-01T00:00:00+00:00")
    q = client.get("/api/v1/board/summary").json()["ready_queue"]
    assert [x["title"] for x in q] == ["now"]


def test_ready_queue_excludes_claimed():
    _mk("claimed", "ready")
    client.post("/api/v1/agents/register", json={"name": "a", "agent_type": "t"})
    client.post("/api/v1/tasks/claim", params={"agent": "a"})
    q = client.get("/api/v1/board/summary").json()["ready_queue"]
    assert q == []


def test_running_with_agent_filter():
    _mk("t1", "ready")
    client.post("/api/v1/agents/register", json={"name": "a", "agent_type": "t"})
    client.post("/api/v1/agents/register", json={"name": "b", "agent_type": "t"})
    _mk("t1", "ready")
    client.post("/api/v1/tasks/claim", params={"agent": "a"})
    client.post("/api/v1/tasks/claim", params={"agent": "b"})

    all_r = client.get("/api/v1/board/summary").json()["running"]
    assert len(all_r) == 2
    only_a = client.get("/api/v1/board/summary", params={"agent": "a"}).json()["running"]
    assert len(only_a) == 1
    assert only_a[0]["claimed_by"] == "a"


def test_recent_done():
    _mk("d1", "ready")
    client.post("/api/v1/agents/register", json={"name": "a", "agent_type": "t"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "a"}).json()
    client.post(f"/api/v1/runs/{claim['id']}/heartbeat", json={"progress": 100})
    client.post(f"/api/v1/runs/{claim['id']}/result", json={"success": True, "result": "OK"})

    data = client.get("/api/v1/board/summary").json()
    assert len(data["recent_done"]) == 1
    assert data["recent_done"][0]["title"] == "d1"
    assert data["recent_done"][0]["completed_at"] is not None


def test_due_at_alert():
    _mk("overdue", "brainstorming", due_at="2020-01-01T00:00:00+00:00")
    data = client.get("/api/v1/board/summary").json()
    assert any("超截止时间" in a["message"] for a in data["alerts"])
    assert any("超过截止时间" in s for s in data["next_steps"])


def test_next_steps_mentions_ready():
    _mk("a", "ready", priority=1)
    data = client.get("/api/v1/board/summary").json()
    assert any("待领取" in s for s in data["next_steps"])


def test_blocked_dependency_alert():
    parent = _mk("bp", stage="cancelled")
    child = _mk("bc", stage="planning", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    assert any("依赖阻塞" in a["message"] and child["id"] in a["message"] for a in data["alerts"])


def test_completed_dependency_no_alert():
    # 前置完成（state=completed）不应告警；需先领取并完成
    parent = _mk("cp", stage="ready")
    client.post("/api/v1/agents/register", json={"name": "b-a", "agent_type": "t"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "b-a"}).json()
    client.post(f"/api/v1/runs/{claim['id']}/heartbeat", json={"progress": 100})
    client.post(f"/api/v1/runs/{claim['id']}/result", json={"success": True, "result": "ok"})
    child = _mk("cc", stage="planning", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    assert not any("依赖阻塞" in a["message"] for a in data["alerts"])


def test_ready_dependency_not_flagged_as_blocked():
    parent = _mk("rp", stage="cancelled")
    child = _mk("rc", stage="ready", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    # ready 任务可被领取，不应报「无法放行」
    assert not any(child["id"] in a["message"] and "依赖阻塞" in a["message"] for a in data["alerts"])


def test_done_stage_dependency_no_alert():
    parent = _mk("dp", stage="done")
    child = _mk("dc", stage="planning", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    assert not any("依赖阻塞" in a["message"] for a in data["alerts"])


# ── READY 阶段依赖盲区告警（2026-10-07）────────────────────────────────
# 既有告警只覆盖 stage ∈ {brainstorming, design, planning}；READY 任务不受
# 依赖门控（claim.py:68 / background.py:389 都不检查 depends_on），因此需要
# 独立可见性。以下只验证「暴露问题」，不改变任何领取行为。

def test_ready_with_cancelled_dep_reports_deadlock():
    """死结：前置已取消 → 依赖链永远不会被放行，必须报warning。"""
    parent = _mk("dead-parent", stage="cancelled")
    child = _mk("dead-child", stage="ready", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    hits = [a for a in data["alerts"] if child["id"] in a["message"]]
    assert hits, "祖先已取消的 READY 任务应报依赖死结"
    assert any(a["level"] == "warning" and "依赖死结" in a["message"] for a in hits)


def test_ready_with_pending_dep_reports_info():
    """越级：前置仍在进行 → READY 无门控，可能被提前领走，报 info。"""
    parent = _mk("pend-parent", stage="ready")
    child = _mk("pend-child", stage="ready", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    hits = [a for a in data["alerts"] if child["id"] in a["message"]]
    assert hits, "前置未完成的 READY 任务应提示可能被提前领走"
    assert any(a["level"] == "info" for a in hits)
    # 明确提示「无依赖门控」，让读的人知道这是机制缺口
    assert any("无依赖门控" in a["message"] for a in hits)


def test_ready_dep_satisfied_no_new_alert():
    """前置已完成 → 不产生新告警（避免噪声）。"""
    parent = _mk("ok-parent", stage="ready")
    claim = client.post("/api/v1/tasks/claim", params={"agent": "ok-agent"}).json()
    # 把 parent 领走并完成
    client.post(f"/api/v1/runs/{claim['id']}/heartbeat", json={"progress": 100})
    client.post(f"/api/v1/runs/{claim['id']}/result", json={"success": True, "result": "ok"})
    _mk("ok-child", stage="ready", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    assert not any("依赖死结" in a["message"] for a in data["alerts"])


def test_ready_dependency_alert_does_not_change_claim_behaviour():
    """关键：告警是纯可见性——依赖未满足的 READY 任务**仍可被 claim**。

    这条测试锁定「本轮不改行为」的边界：若将来加了 READY 阶段门控，
    它会失败并提醒同步更新文案与预期。
    """
    parent = _mk("beh-parent", stage="ready")
    child = _mk("beh-child", stage="ready", depends_on=[parent["id"]])
    r = client.post("/api/v1/tasks/claim",
                    params={"agent": "beh-agent", "task_id": child["id"]})
    assert r.status_code == 200, "本轮不改领取行为，依赖未满足也仍可领"
