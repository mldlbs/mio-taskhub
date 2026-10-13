"""抢单模式 A 批：grab_mode + 只读大厅 + 派单让位（FR-1..FR-5）。

范围严格限定在2026-10-07 裁剪后的 5 条 FR。**不测**Future 项
（悬赏／匹配评分／拒单／失约／竞争统计／GrabAttempt）——它们需求证据不足。

两个必须锁死的不变量（用户指定）：
- INV-1：`grab_mode=false` 的任务行为与改动前**完全一致**
- INV-2：`grab_mode=true` 的任务不进dispatcher 候选，但能被现有 claim() 领取
"""
from fastapi.testclient import TestClient

from mio_taskhub import background
from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Agent, AgentStatus, Task, TaskState, TaskStage
from sqlmodel import Session, select

client = TestClient(app)


def _mk(title, grab_mode=False, stage="ready", **kw):
    """建任务并显式设置 grab_mode（建单默认 False，需再 PATCH 或直改）。"""
    r = client.post("/api/v1/tasks", json={"title": title, "stage": stage, **kw})
    tid = r.json()["id"]
    if grab_mode:
        client.patch(f"/api/v1/tasks/{tid}", json={"grab_mode": True})
    return tid


def _row(tid):
    with Session(engine) as s:
        return s.get(Task, tid)


def _mk_online_agent(name, agent_type=""):
    with Session(engine) as s:
        s.add(Agent(name=name, agent_type=agent_type, status=AgentStatus.ONLINE))
        s.commit()


# ── FR-1：grab_mode 字段 ────────────────────────────────────────────────

def test_fr1_default_is_false_for_new_task():
    """FR-1 验收：未传时默认 false（不改变既有任务行为）。"""
    tid = _mk("默认 false")
    assert _row(tid).grab_mode is False, "新建任务未传 grab_mode 时必须为 False"


def test_fr1_can_be_set_via_create():
    """FR-1 验收：建单可直接写入 grab_mode=true。"""
    r = client.post("/api/v1/tasks",
                    json={"title": "建单即标记", "stage": "ready", "grab_mode": True})
    assert r.json()["grab_mode"] is True
    assert _row(r.json()["id"]).grab_mode is True


def test_fr1_can_be_toggled_via_patch():
    """FR-1 验收：改单可切换，且两个方向都生效。"""
    tid = _mk("可切换")
    client.patch(f"/api/v1/tasks/{tid}", json={"grab_mode": True})
    assert _row(tid).grab_mode is True
    client.patch(f"/api/v1/tasks/{tid}", json={"grab_mode": False})
    assert _row(tid).grab_mode is False


def test_fr1_appears_in_list_and_detail():
    """grab_mode 在列表与详情中可见（便于诊断）。"""
    tid = _mk("可见性", grab_mode=True)
    lst = client.get("/api/v1/tasks").json()
    hit = [t for t in lst if t["id"] == tid]
    assert hit and hit[0]["grab_mode"] is True
    assert client.get(f"/api/v1/tasks/{tid}").json()["grab_mode"] is True


# ── FR-2：只读大厅 ─────────────────────────────────────────────────────

def test_fr2_hall_only_returns_grab_mode_ready_queued():
    """FR-2 验收：只返回 grab_mode=true 且 queued+ready 的任务。"""
    want = _mk("大厅应出现", grab_mode=True)
    _mk("大厅不应出现-未标记")          # grab_mode=False
    _mk("大厅不应出现-非ready", grab_mode=True, stage="brainstorming")
    got = client.get("/api/v1/tasks/hall").json()
    ids = [i["id"] for i in got["items"]]
    assert want in ids
    assert len(ids) == 1, f"大厅只应含 1 条，实际 {ids}"


def test_fr2_item_shape_has_diagnostic_fields():
    """FR-2 验收：含id/标题/描述/target/优先级/预估耗时/创建时间。"""
    tid = _mk("字段完整", grab_mode=True, priority=2,
              target_agent_type="frontend", est_duration_min=45,
              description="D" * 300, acceptance_criteria="AC" * 200)
    item = [i for i in client.get("/api/v1/tasks/hall").json()["items"] if i["id"] == tid][0]
    for k in ("id", "title", "description", "target_agent_type", "priority",
              "est_duration_min", "created_at", "project", "labels",
              "depends_on", "acceptance_criteria"):
        assert k in item, f"大厅条目缺字段 {k}"
    assert item["target_agent_type"] == "frontend"
    assert item["priority"] == 2
    assert item["est_duration_min"] == 45
    # 描述与验收标准截断到 200，避免响应体膨胀
    assert len(item["description"]) == 200
    assert len(item["acceptance_criteria"]) == 200


def test_fr2_hall_is_read_only_zero_side_effect():
    """FR-2 验收：大厅零副作用——连续查询不改变任何状态/attempt/事件。"""
    tid = _mk("零副作用", grab_mode=True)
    before = _row(tid)
    snap = (before.state, before.stage, before.attempt)
    # 查询前该任务的事件数（建单会产生 task_created）
    ev_before = len(client.get(f"/api/v1/tasks/{tid}/events").json().get("events", []))
    for _ in range(5):
        client.get("/api/v1/tasks/hall")
    after = _row(tid)
    assert (after.state, after.stage, after.attempt) == snap, "大厅查询改变了任务状态"
    ev_after = len(client.get(f"/api/v1/tasks/{tid}/events").json().get("events", []))
    assert ev_after == ev_before, f"大厅查询产生了事件：{ev_before} -> {ev_after}"


def test_fr2_hall_empty_returns_empty_list_not_null():
    """空大厅返回 items: []，不是 null。"""
    got = client.get("/api/v1/tasks/hall").json()
    assert got["items"] == [] and got["total"] == 0


def test_fr2_hall_limit_and_order():
    """limit 截断 + priority desc 排序（沿用既有 claim 的排序语义）。"""
    for i in range(3):
        _mk(f"排序-{i}", grab_mode=True, priority=i % 2)
    got = client.get("/api/v1/tasks/hall").json()
    assert got["total"] == 3, "三个标记任务都应在大厅"
    prios = [i["priority"] for i in got["items"]]
    assert prios == sorted(prios, reverse=True), f"应按 priority 降序，实际 {prios}"
    # limit 截断
    one = client.get("/api/v1/tasks/hall?limit=1").json()
    assert len(one["items"]) == 1 and one["limit"] == 1
    assert one["total"] == 3, "total 是过滤后总数，不受 limit 影响"


def test_fr2_hall_fifo_within_same_priority():
    """同priority 时按 created_at 升序（FIFO），与 claim 排序一致。"""
    ids = [_mk(f"fifo-{i}", grab_mode=True, priority=1) for i in range(3)]
    got = client.get("/api/v1/tasks/hall").json()
    got_ids = [i["id"] for i in got["items"] if i["id"] in ids]
    assert got_ids == ids, f"同优先级应保持创建顺序，实际 {got_ids}"


def test_fr2_hall_filters():
    """target_agent_type / project 过滤，空值不过滤。"""
    a = _mk("过滤A", grab_mode=True, target_agent_type="frontend")
    _mk("过滤B", grab_mode=True, target_agent_type="backend")
    got = client.get("/api/v1/tasks/hall?target_agent_type=frontend").json()
    ids = [i["id"] for i in got["items"]]
    assert a in ids and len(ids) == 1


def test_fr2_limit_out_of_range_rejected():
    """limit 越界由 FastAPI 直接 422（边界由框架保证，不自造错误码）。"""
    assert client.get("/api/v1/tasks/hall?limit=0").status_code == 422
    assert client.get("/api/v1/tasks/hall?limit=201").status_code == 422


# ── INV-2 + FR-3/FR-4：派单让位 + 仍可被现有 claim 领取 ────────────────

def test_inv2_grab_task_not_assigned_by_dispatcher():
    """INV-2/FR-4：grab_mode=true 的任务跑一轮调度 tick 后仍为 queued。"""
    tid = _mk("让位", grab_mode=True, priority=3)  # 最高优先级，确保若被排除必然是谓词生效
    _mk_online_agent("idle-agent-a", "cli")
    background._assign_to_idle_agents()
    row = _row(tid)
    assert row.state == TaskState.QUEUED, "grab_mode=true 的任务被 dispatcher 指派了"
    assert row.stage == TaskStage.READY


def test_inv2_grab_task_still_claimable_via_existing_claim():
    """INV-2/FR-3：不进派单候选，但**能**被现有 claim(task_id=) 领取。"""
    tid = _mk("可主动领取", grab_mode=True)
    _mk_online_agent("active-agent", "cli")
    # 先跑一轮 tick 确认它没被自动派走
    background._assign_to_idle_agents()
    assert _row(tid).state == TaskState.QUEUED
    # 再用既有 claim 主动领取（不新增端点）
    r = client.post("/api/v1/tasks/claim",
                    params={"agent": "active-agent", "task_id": tid})
    assert r.status_code == 200, f"claim 失败：{r.text[:200]}"
    assert r.json()["task_id"] == tid
    assert _row(tid).state == TaskState.CLAIMED


def test_fr4_claim_response_shape_unchanged():
    """FR-3：claim 成功响应结构与改造前一致（未新增抢单专属字段）。"""
    tid = _mk("响应形状", grab_mode=True)
    r = client.post("/api/v1/tasks/claim", params={"agent": "shape-agent", "task_id": tid})
    body = r.json()
    for k in ("id", "task_id", "state", "agent_name", "attempt"):
        assert k in body, f"claim 响应缺既有字段 {k}"
    #不应出现任何抢单专属字段
    for k in ("bounty_at_grab", "latency_ms", "winner", "decision"):
        assert k not in body, f"claim 响应混入了抢单专属字段 {k}"


# ── INV-1：grab_mode=false 行为与改动前完全一致 ─────────────────────────

def test_inv1_false_task_still_dispatched():
    """INV-1/FR-4：grab_mode=false 的任务**仍被** dispatcher 正常指派。"""
    tid = _mk("正常派单", grab_mode=False, priority=3)
    _mk_online_agent("busy-agent", "cli")
    background._assign_to_idle_agents()
    row = _row(tid)
    assert row.state == TaskState.CLAIMED, "grab_mode=false 的任务未被派单（行为回退了）"
    assert row.stage == TaskStage.IMPLEMENTING


def test_inv1_false_task_still_claimable_normally():
    """INV-1：未标记任务的主动领取路径不受影响。"""
    tid = _mk("普通领取", grab_mode=False)
    r = client.post("/api/v1/tasks/claim", params={"agent": "normal-agent", "task_id": tid})
    assert r.status_code == 200 and r.json()["task_id"] == tid


def test_inv1_blind_claim_still_works():
    """INV-1：不带 task_id 的盲领（既有能力）未被破坏。"""
    _mk("盲领目标", grab_mode=False)
    r = client.post("/api/v1/tasks/claim", params={"agent": "blind-agent"})
    assert r.status_code == 200
    assert "id" in r.json()


def test_inv1_dispatcher_ignores_claimed_tasks():
    """INV-1：已被领取的任务不会被重复派发（既有语义不变）。"""
    tid = _mk("已领不重派", grab_mode=False)
    client.post("/api/v1/tasks/claim", params={"agent": "first-agent", "task_id": tid})
    _mk_online_agent("second-agent", "cli")
    background._assign_to_idle_agents()
    assert _row(tid).state == TaskState.CLAIMED
    assert _row(tid).attempt == 1, "attempt 被重复派发累加了"


# ── 迁移 ──────────────────────────────────────────────────────────────

def test_migration_is_idempotent():
    """迁移连跑两次不报错、不改已有数据。"""
    from mio_taskhub.migrations import run_migrations
    tid = _mk("迁移幂等", grab_mode=True)
    before = _row(tid).grab_mode
    run_migrations(engine)
    run_migrations(engine)
    assert _row(tid).grab_mode == before


def test_migration_backfills_false():
    """存量行为空列时读作 False（迁移已兜底，read路径也or兜底）。"""
    with Session(engine) as s:
        nulls = s.exec(
            select(Task).where(Task.grab_mode == None)  # noqa: E711
        ).all()
    assert not nulls, f"存在 grab_mode 为 NULL 的任务：{[t.id for t in nulls]}"
