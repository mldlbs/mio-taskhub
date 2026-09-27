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


# ---------- P1 包 B（FR-13/FR-14/FR-16）：假设区真实聚合 ----------

@pytest.fixture(autouse=True)
def _clear_hyp_cache():
    cockpit._HYP_CACHE.clear()
    yield
    cockpit._HYP_CACHE.clear()


def test_cockpit_hypotheses_scores_and_broken(monkeypatch):
    """FR-13/FR-14：三元分透传 + 断链标 broken 不移除 + cached_at + source=mio。"""
    calls = []

    def fake(**kw):
        calls.append(kw)
        return {"available": True, "ok": True, "items": [
            {"id": "h1", "title": "假设一", "status": "active",
             "novelty": 9, "feasibility": 8, "impact": 7, "score": 8.0},
        ]}

    monkeypatch.setattr(cockpit.mio_runtime, "creativity", fake)

    async def k(c):
        iid = await _make_idea(c, hypotheses=["h1", "gone"])
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        sec = r.json()["sections"]["hypotheses"]
        assert sec["status"] == "ok"
        assert sec["cached_at"]
        assert sec["data"]["source"] == "mio"
        items = sec["data"]["items"]
        assert [x["id"] for x in items] == ["h1", "gone"]  # 保关联顺序
        assert items[0]["novelty"] == 9 and items[0]["feasibility"] == 8
        assert items[0]["impact"] == 7 and items[0]["score"] == 8.0
        assert items[0]["broken"] is False
        assert items[1] == {"id": "gone", "broken": True}  # 断链：标记但不静默删除
        # FR-16：5min TTL 内缓存命中，第二次请求不再打 Mio
        assert len(calls) == 1
        r2 = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r2.status_code == 200
        assert r2.json()["sections"]["hypotheses"]["status"] == "ok"
        assert len(calls) == 1
    _with_client(k)


def test_cockpit_hypotheses_empty_skips_mio(monkeypatch):
    """未关联假设 → items 空 + source=none，不打 Mio。"""

    def boom(**kw):
        raise AssertionError("不应调用 Mio")

    monkeypatch.setattr(cockpit.mio_runtime, "creativity", boom)

    async def k(c):
        iid = await _make_idea(c)
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        sec = r.json()["sections"]["hypotheses"]
        assert sec["status"] == "ok"
        assert sec["data"] == {"items": [], "source": "none", "total": 0}
    _with_client(k)


def test_cockpit_hypotheses_mio_fail_degrades_only_section(monkeypatch):
    """FR-16：Mio CLI 失败 → 仅 hypotheses degraded reason=mio_timeout，其余 ok、接口 200。"""
    monkeypatch.setattr(cockpit.mio_runtime, "creativity",
                        lambda **kw: {"available": True, "ok": False,
                                      "reason": "cli_failed", "items": []})

    async def k(c):
        iid = await _make_idea(c, hypotheses=["h1"])
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        secs = r.json()["sections"]
        assert secs["hypotheses"]["status"] == "degraded"
        assert secs["hypotheses"]["reason"] == "mio_timeout"
        assert secs["goal"]["status"] == "ok"
        assert secs["tasks"]["status"] == "ok"
    _with_client(k)


def test_cockpit_hypotheses_mio_unavailable_degrades(monkeypatch):
    """FR-16：Mio 运行时不可用 → degraded reason=mio_unavailable（分层于 timeout）。"""
    monkeypatch.setattr(cockpit.mio_runtime, "creativity",
                        lambda **kw: {"available": False, "ok": False,
                                      "reason": "runtime_unavailable", "items": []})

    async def k(c):
        iid = await _make_idea(c, hypotheses=["h1"])
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        secs = r.json()["sections"]
        assert secs["hypotheses"]["status"] == "degraded"
        assert secs["hypotheses"]["reason"] == "mio_unavailable"
    _with_client(k)


def test_cockpit_hypotheses_exception_degrades(monkeypatch):
    """跨服务调用抛异常 → 降级 mio_timeout，不 500。"""
    def boom(**kw):
        raise RuntimeError("conn refused")

    monkeypatch.setattr(cockpit.mio_runtime, "creativity", boom)

    async def k(c):
        iid = await _make_idea(c, hypotheses=["h1"])
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        secs = r.json()["sections"]
        assert secs["hypotheses"]["status"] == "degraded"
        assert secs["hypotheses"]["reason"] == "mio_timeout"
    _with_client(k)


def test_cockpit_hypotheses_timeout_budget(monkeypatch):
    """区块级 3s 预算兜底：Mio 调用拖死 → hypotheses degraded timeout（注入小预算）。"""
    import time

    def slow_mio(**kw):
        time.sleep(1.0)  # 同步拖死在线程里，等 wait_for 超时
        return {"available": True, "ok": True, "items": []}

    monkeypatch.setattr(cockpit.mio_runtime, "creativity", slow_mio)
    monkeypatch.setitem(cockpit.SECTION_TIMEOUTS, "hypotheses", 0.05)

    async def k(c):
        iid = await _make_idea(c, hypotheses=["h1"])
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        secs = r.json()["sections"]
        assert secs["hypotheses"]["status"] == "degraded"
        assert secs["hypotheses"]["reason"] == "timeout"
    _with_client(k)
