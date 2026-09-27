"""P3 FR-28：假设关联表建表迁移（幂等回填）、双写兼容（P1 端点契约不变）、读端点。"""
import asyncio
import uuid

import httpx
from sqlmodel import Session, select

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.api import ideas as ideas_api
from mio_taskhub.models import Idea, IdeaAssumptionLink
from mio_taskhub.migrations import run_migrations


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


def _fake_creativity(ids=("h1", "h2")):
    def f(**kw):
        return {"available": True, "ok": True,
                "items": [{"id": x, "title": f"t{x}", "status": "active"} for x in ids]}
    return f


async def _make_idea(c, **kw):
    r = await c.post("/api/v1/ideas", json={"title": kw.pop("title", "关联表想法"), **kw})
    assert r.status_code == 200
    return r.json()


def test_migration_backfill_idempotent():
    """FR-28：旧数据 hypotheses[] 建行、assumptions 按 hid 回填（容忍 hid/id 两形态），重复启动不重复建行。"""
    iid = uuid.uuid4().hex[:8]
    with Session(engine) as s:
        s.add(Idea(id=iid, title="迁移想法",
                   hypotheses=["h1", "h2", "h3"],
                   assumptions=[{"hid": "h1", "status": "validated", "note": "n1",
                                 "confirmed_by": "alice"},
                                {"id": "h2", "status": "active"}]))
        s.commit()

    run_migrations(engine)  # 首次：建表 + 回填
    with Session(engine) as s:
        rows = s.exec(select(IdeaAssumptionLink).where(
            IdeaAssumptionLink.idea_id == iid)).all()
        by = {r.hypothesis_id: r for r in rows}
        assert set(by) == {"h1", "h2", "h3"}
        assert by["h1"].status == "validated" and by["h1"].note == "n1"
        assert by["h1"].confirmed_by == "alice"
        assert by["h2"].status == "active"          # id 形态条目也匹配
        assert by["h3"].status == "unverified"       # 无缓存条目 → 默认值
        assert by["h3"].note == "" and by["h3"].confirmed_by == ""

    run_migrations(engine)  # 二次：幂等
    with Session(engine) as s:
        rows = s.exec(select(IdeaAssumptionLink).where(
            IdeaAssumptionLink.idea_id == iid)).all()
        assert len(rows) == 3


def test_import_and_patch_dual_write_p1_contract_unchanged(monkeypatch):
    """FR-28：import 建行 / PATCH upsert / hypotheses 移除删行；P1 两端点签名与响应逐键不变。"""
    monkeypatch.setattr(ideas_api.mio_runtime, "creativity", _fake_creativity())
    state = {}

    async def k(c):
        # P1 语义：PATCH 只回写已存在的 assumptions 条目 → 先带条目创建
        base = await _make_idea(c, assumptions=[{"hid": "h1", "status": "active"}])
        iid = base["id"]
        state["iid"] = iid

        # import：P1 响应键集合不变
        r = await c.post(f"/api/v1/ideas/{iid}/hypotheses/import",
                         json={"ids": ["h1", "h2"]})
        assert r.status_code == 200
        b = r.json()
        assert set(b.keys()) == {"ok", "added", "hypotheses", "idea"}
        assert b["ok"] is True and b["added"] == ["h1", "h2"]
        assert b["hypotheses"] == ["h1", "h2"]

        # PATCH 回写：P1 响应 = _idea_json 全结构（与 GET idea 键一致）
        r2 = await c.patch(f"/api/v1/ideas/{iid}/assumptions/h1",
                           json={"status": "validated", "note": "n1",
                                 "confirmed_by": "qa"})
        assert r2.status_code == 200
        c0 = await _make_idea(c, title="契约基线")  # POST 同为 _idea_json 结构
        assert set(r2.json().keys()) == set(c0.keys())  # P1 契约逐键
        e1 = next(e for e in r2.json()["assumptions"] if e["hid"] == "h1")
        assert e1["status"] == "validated" and e1["confirmed_by"] == "qa"

        # 幂等重放：值全同不 bump 版本（P1 语义）
        v = r2.json()["version"]
        r3 = await c.patch(f"/api/v1/ideas/{iid}/assumptions/h1",
                           json={"status": "validated", "note": "n1",
                                 "confirmed_by": "qa"})
        assert r3.status_code == 200 and r3.json()["version"] == v

        # 移除关联：hypotheses 收缩到 ["h1"] → h2 无 assumptions 条目 → 删行
        r4 = await c.patch(f"/api/v1/ideas/{iid}", json={"hypotheses": ["h1"]})
        assert r4.status_code == 200
        assert r4.json()["hypotheses"] == ["h1"]
    _with_client(k)

    with Session(engine) as s:
        rows = s.exec(select(IdeaAssumptionLink).where(
            IdeaAssumptionLink.idea_id == state["iid"])).all()
        by = {r.hypothesis_id: r for r in rows}
        assert set(by) == {"h1"}
        assert by["h1"].status == "validated" and by["h1"].note == "n1"
        assert by["h1"].confirmed_by == "qa"


def test_assumption_links_endpoint(monkeypatch):
    """FR-28：GET .../assumption-links 返回唯一键行（按 hid 排序），未知 idea 404。"""
    monkeypatch.setattr(ideas_api.mio_runtime, "creativity", _fake_creativity())

    async def k(c):
        base = await _make_idea(c)
        iid = base["id"]
        r = await c.post(f"/api/v1/ideas/{iid}/hypotheses/import",
                         json={"ids": ["h2", "h1"]})
        assert r.status_code == 200

        r2 = await c.get(f"/api/v1/ideas/{iid}/assumption-links")
        assert r2.status_code == 200
        b = r2.json()
        assert b["idea_id"] == iid and b["total"] == 2
        assert [x["hypothesis_id"] for x in b["links"]] == ["h1", "h2"]  # 排序
        assert all(x["status"] == "unverified" for x in b["links"])
        assert {k2 for x in b["links"] for k2 in x.keys()} == {
            "id", "idea_id", "hypothesis_id", "status", "note",
            "confirmed_by", "updated_at"}

        r3 = await c.get("/api/v1/ideas/nosuchid/assumption-links")
        assert r3.status_code == 404
    _with_client(k)
