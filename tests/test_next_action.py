"""下一步动作引擎 + dismiss 复活 + 高风险判定测试（FR-6/FR-7/FR-8）。"""
import asyncio

import httpx
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Discussion, Idea, Task, TaskState
from mio_taskhub.next_action import (compute_next_action, dismiss_rule,
                                     is_high_risk, next_action_order,
                                     risk_tag_vocab)
from mio_taskhub.utils import _now
from datetime import timedelta


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


async def _make(c, **kw):
    r = await c.post("/api/v1/ideas", json={"title": kw.pop("title", "想法"), **kw})
    assert r.status_code == 200
    return r.json()["id"]


def _get_idea(iid) -> Idea:
    with Session(engine) as s:
        return s.get(Idea, iid)


def _add_task(iid, **kw) -> str:
    with Session(engine) as s:
        t = Task(title=kw.pop("title", "任务"), idea_id=iid, **kw)
        s.add(t)
        s.commit()
        return t.id


def _add_discussion(iid, status="closed", conclusions=""):
    with Session(engine) as s:
        d = Discussion(idea_id=iid, topic="评审", status=status, conclusions=conclusions)
        s.add(d)
        s.commit()
        return d.id


# ---------- FR-6 优先级序 ----------

def test_priority_missing_goal_wins_over_blocked():
    """多条件同时命中只显示最高序一条（缺 goal > blocked）。"""
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c)  # 无 goal/metric → 规则1 命中
        _add_task(iid, title="卡住的任务", block_reason="等待外部依赖")

    _with_client(k)
    with Session(engine) as s:
        na = compute_next_action(s, s.get(Idea, iid))
    assert na["rule_id"] == "missing_goal"


def test_priority_falls_through_after_goal_filled():
    """补全目标后 → 下一条命中（blocked_task）浮出。"""
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c)
        _add_task(iid, title="卡住的任务", block_reason="等待外部依赖")

    _with_client(k)
    with Session(engine) as s:
        idea = s.get(Idea, iid)
        idea.goal = "目标"
        idea.success_metric = "指标"
        s.add(idea)
        s.commit()
        na = compute_next_action(s, idea)
    assert na["rule_id"] == "blocked_task"


def test_priority_order_env_override(monkeypatch):
    """NFR-4：MIO_NEXT_ACTION_ORDER 覆盖默认序。"""
    monkeypatch.setenv("MIO_NEXT_ACTION_ORDER", "blocked_task,missing_goal")
    assert next_action_order() == ["blocked_task", "missing_goal"]
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c)  # 缺 goal
        _add_task(iid, block_reason="x")

    _with_client(k)
    with Session(engine) as s:
        na = compute_next_action(s, s.get(Idea, iid))
    assert na["rule_id"] == "blocked_task"


def test_rule_doc_unapproved():
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c, goal="g", success_metric="m")
        _add_task(iid, doc_statuses={"spec": {"state": "draft"}})

    _with_client(k)
    with Session(engine) as s:
        na = compute_next_action(s, s.get(Idea, iid))
    assert na["rule_id"] == "doc_unapproved"
    assert "1 个文档" in na["action"]

    # approved 后不再命中
    with Session(engine) as s:
        t = s.exec(__import__("sqlmodel").select(Task).where(Task.idea_id == iid)).first()
        t.doc_statuses = {"spec": {"state": "approved"}}
        s.add(t)
        s.commit()
        assert compute_next_action(s, s.get(Idea, iid)) is None


def test_rule_missing_action_items():
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c, goal="g", success_metric="m")
        _add_discussion(iid, status="closed", conclusions="")

    _with_client(k)
    with Session(engine) as s:
        na = compute_next_action(s, s.get(Idea, iid))
    assert na["rule_id"] == "missing_action_items"


def test_no_rule_returns_none():
    async def k(c):
        iid = await _make(c, goal="g", success_metric="m")
        with Session(engine) as s:
            assert compute_next_action(s, s.get(Idea, iid)) is None
    _with_client(k)


# ---------- FR-7 dismiss / 复活 / 过期 ----------

def test_dismiss_then_revival_on_snapshot_change():
    """dismiss 后 condition_snapshot 变化 → 立即复活（不等 7 天）。"""
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c)  # goal/metric 均缺

    _with_client(k)
    with Session(engine) as s:
        idea = s.get(Idea, iid)
        na = compute_next_action(s, idea)
        assert na["rule_id"] == "missing_goal"
        dismiss_rule(s, idea, "missing_goal")
        # 条件未变 → dismiss 生效
        assert compute_next_action(s, idea) is None
        # 只补 goal → snapshot 变化 → 立即复活（仍缺 metric）
        idea = s.get(Idea, iid)
        idea.goal = "目标"
        s.add(idea)
        s.commit()
        na = compute_next_action(s, idea)
        assert na is not None
        assert na["rule_id"] == "missing_goal"
        assert na["snapshot"]["goal_present"] is True
        assert na["snapshot"]["metric_present"] is False


def test_dismiss_expires_after_7_days():
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c)

    _with_client(k)
    with Session(engine) as s:
        idea = s.get(Idea, iid)
        pref = dismiss_rule(s, idea, "missing_goal")
        pref.dismissed_at = _now() - timedelta(days=8)
        s.add(pref)
        s.commit()
        assert compute_next_action(s, idea) is not None  # 过期 → 复活


def test_dismiss_falls_through_to_next_rule():
    """dismiss 当前规则 → 显示顺位下一条（若仍命中）。"""
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c)  # 规则1
        _add_task(iid, block_reason="x")  # 规则3 也命中

    _with_client(k)
    with Session(engine) as s:
        idea = s.get(Idea, iid)
        dismiss_rule(s, idea, "missing_goal")
        na = compute_next_action(s, idea)
    assert na["rule_id"] == "blocked_task"


def test_dismiss_endpoint_and_revival_via_api():
    async def k(c):
        iid = await _make(c)
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        na = r.json()["next_action"]
        assert na["rule_id"] == "missing_goal"
        r = await c.post(f"/api/v1/ideas/{iid}/next-action/dismiss",
                         json={"rule_id": "missing_goal"})
        assert r.status_code == 200
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.json()["next_action"] is None
        # 未命中规则 dismiss → 409
        r = await c.post(f"/api/v1/ideas/{iid}/next-action/dismiss",
                         json={"rule_id": "blocked_task"})
        assert r.status_code == 409
        # 补 goal → snapshot 变化 → 复活
        await c.patch(f"/api/v1/ideas/{iid}", json={"goal": "g"})
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.json()["next_action"]["rule_id"] == "missing_goal"
    _with_client(k)


# ---------- FR-8 高风险判定 ----------

def test_high_risk_tags_intersection():
    async def k(c):
        iid = await _make(c, tags=["合规", "自定义"])
        with Session(engine) as s:
            assert is_high_risk(s.get(Idea, iid)) is True
        iid2 = await _make(c, title="t2", tags=["普通标签"])
        with Session(engine) as s:
            assert is_high_risk(s.get(Idea, iid2)) is False
        iid3 = await _make(c, title="t3")
        with Session(engine) as s:
            assert is_high_risk(s.get(Idea, iid3)) is False  # 空 tags
    _with_client(k)


def test_high_risk_vocab_env_override(monkeypatch):
    monkeypatch.setenv("MIO_IDEA_RISK_TAGS", "严重, P0")
    assert risk_tag_vocab() == ["严重", "P0"]
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c, tags=["合规"])  # 默认词表命中、新词表不命中

    _with_client(k)
    with Session(engine) as s:
        assert is_high_risk(s.get(Idea, iid)) is False


def test_high_risk_drives_unverified_assumption_rule():
    """高风险 + 未验证假设 → 规则4 命中（规则1-3 不满足）。"""
    iid = None

    async def k(c):
        nonlocal iid
        iid = await _make(c, goal="g", success_metric="m", tags=["高风险"],
                          assumptions=[{"hid": "h1", "text": "假设"}])

    _with_client(k)
    with Session(engine) as s:
        na = compute_next_action(s, s.get(Idea, iid))
    assert na["rule_id"] == "unverified_high_risk_assumption"

    # 验证后不再命中
    with Session(engine) as s:
        idea = s.get(Idea, iid)
        idea.assumptions = [{"hid": "h1", "text": "假设", "status": "validated"}]
        s.add(idea)
        s.commit()
        assert compute_next_action(s, s.get(Idea, iid)) is None


def test_cockpit_outputs_high_risk_flag():
    async def k(c):
        iid = await _make(c, tags=["用户数据"])
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.json()["high_risk"] is True
        iid2 = await _make(c, title="t2", tags=[])
        r = await c.get(f"/api/v1/ideas/{iid2}/cockpit")
        assert r.json()["high_risk"] is False
    _with_client(k)
