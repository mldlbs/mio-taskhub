"""想法驾驶舱 /cockpit 聚合测试（FR-3/FR-4）。"""
import asyncio

import httpx
import pytest

from mio_taskhub.main import app
from mio_taskhub.api import cockpit


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


async def _make_idea(c, **kw):
    r = await c.post("/api/v1/ideas", json={"title": kw.pop("title", "驾驶舱想法"), **kw})
    assert r.status_code == 200
    return r.json()["id"]


def test_cockpit_structure():
    """FR-3：sections 区块级 status/reason/cached_at + next_action 占位，无整包 degraded。"""
    async def k(c):
        iid = await _make_idea(c, goal="目标", success_metric="指标")
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        body = r.json()
        assert body["idea_id"] == iid
        assert "degraded" not in body  # 禁止整包 degraded 字段
        assert body["next_action"] is None  # 步骤④ 接规则引擎
        assert set(body["sections"].keys()) == {
            "goal", "hypotheses", "mvp", "tasks", "risks", "approvals", "retrospective",
        }
        for name, sec in body["sections"].items():
            assert sec["status"] == "ok", f"{name} 应为 ok"
            assert "data" in sec
        # idea 字段区真实聚合
        assert body["sections"]["goal"]["data"]["goal"] == "目标"
        assert body["sections"]["mvp"]["data"]["mvp_scope"] == ""
    _with_client(k)


def test_cockpit_404():
    async def k(c):
        r = await c.get("/api/v1/ideas/nosuchid/cockpit")
        assert r.status_code == 404
    _with_client(k)


def test_cockpit_single_section_error_degrades_only_that_section(monkeypatch):
    """FR-4：单区块抛异常 → 仅该区 degraded，其余 ok，接口仍 200。"""
    async def k(c):
        iid = await _make_idea(c)

        async def boom(idea, db):
            raise RuntimeError("mio down")

        monkeypatch.setitem(cockpit.SECTION_BUILDERS, "hypotheses", boom)
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        secs = r.json()["sections"]
        assert secs["hypotheses"]["status"] == "degraded"
        assert secs["hypotheses"]["reason"] == "RuntimeError"
        assert secs["goal"]["status"] == "ok"
        assert secs["tasks"]["status"] == "ok"
    _with_client(k)


def test_cockpit_section_timeout(monkeypatch):
    """FR-4：区块超时预算（注入慢区块）→ 仅该区 degraded timeout。"""
    async def k(c):
        iid = await _make_idea(c)

        async def slow(idea, db):
            await asyncio.sleep(0.5)
            return {"data": {}}

        monkeypatch.setitem(cockpit.SECTION_BUILDERS, "mvp", slow)
        monkeypatch.setitem(cockpit.SECTION_TIMEOUTS, "mvp", 0.05)
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        secs = r.json()["sections"]
        assert secs["mvp"]["status"] == "degraded"
        assert secs["mvp"]["reason"] == "timeout"
        assert secs["goal"]["status"] == "ok"
    _with_client(k)


def test_cockpit_total_budget_trims_slow_sections(monkeypatch):
    """FR-4：总预算 5s 裁剪未就绪区块（注入更小预算验证行为）。"""
    async def k(c):
        iid = await _make_idea(c)

        async def slow(idea, db):
            await asyncio.sleep(1.0)
            return {"data": {}}

        monkeypatch.setitem(cockpit.SECTION_BUILDERS, "hypotheses", slow)
        monkeypatch.setattr(cockpit, "TOTAL_BUDGET", 0.1)
        monkeypatch.setitem(cockpit.SECTION_TIMEOUTS, "hypotheses", 3.0)
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        secs = r.json()["sections"]
        assert secs["hypotheses"]["status"] == "degraded"
        assert secs["hypotheses"]["reason"] == "total_timeout"
        assert secs["goal"]["status"] == "ok"  # 已就绪照常返回
    _with_client(k)


def test_cockpit_null_fields_safe():
    """旧数据 NULL 新字段 → 各区块空值安全渲染。"""
    async def k(c):
        iid = await _make_idea(c)
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        secs = r.json()["sections"]
        assert secs["goal"]["data"] == {"goal": "", "success_metric": "",
                                        "constraints": "", "out_of_scope": ""}
        assert secs["risks"]["data"]["items"] == []
    _with_client(k)


def _mk_task(iid, title, depends_on=None, **kw):
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task
    with Session(engine) as s:
        t = Task(title=title, idea_id=iid, depends_on=depends_on or [], **kw)
        s.add(t)
        s.commit()
        return t.id


def test_cockpit_tasks_downstream_graph():
    """FR-9：一层下游（谁依赖我）入图，edges 指向下游。"""
    async def k(c):
        iid = await _make_idea(c)
        a = _mk_task(iid, "任务A")
        b = _mk_task("other", "任务B", depends_on=[a])   # 其他想法的，但依赖 A → 下游
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        data = r.json()["sections"]["tasks"]["data"]
        ids = {t["id"]: t for t in data["items"]}
        assert a in ids and not ids[a]["downstream"]
        assert b in ids and ids[b]["downstream"]
        assert data["has_cycle"] is False
        assert data["graph"]["edges"] == [{"from": a, "to": b}]
        assert data["folded"] is False
        assert data["total"] == 2
    _with_client(k)


def test_cockpit_tasks_cycle_degrades_to_list():
    """FR-9：检测到依赖环 → graph=None + 警告条，仍给任务列表。"""
    async def k(c):
        iid = await _make_idea(c)
        a = _mk_task(iid, "任务A", depends_on=["placeholder"])
        b = _mk_task("other", "任务B", depends_on=[a])
        # 造环：A 依赖 B
        from sqlmodel import Session
        from mio_taskhub.db import engine
        from mio_taskhub.models import Task
        with Session(engine) as s:
            ta = s.get(Task, a)
            ta.depends_on = [b]
            s.add(ta)
            s.commit()
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        data = r.json()["sections"]["tasks"]["data"]
        assert data["has_cycle"] is True
        assert data["graph"] is None
        assert data["warning"] and "环" in data["warning"]
        assert len(data["items"]) == 2
    _with_client(k)


def test_cockpit_tasks_fold_over_20():
    """FR-9：超过 20 个默认折叠。"""
    async def k(c):
        iid = await _make_idea(c)
        for n in range(21):
            _mk_task(iid, f"任务{n}")
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        data = r.json()["sections"]["tasks"]["data"]
        assert data["folded"] is True
        assert data["total"] == 21
        assert len(data["items"]) == 21
    _with_client(k)
