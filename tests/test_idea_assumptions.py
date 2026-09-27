"""想法落地闭环 P1 包 B：假设导入（FR-12）与单条人工回写（FR-15）。"""
import asyncio
import threading

import httpx
from sqlmodel import Session, select

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.api import ideas as ideas_api
from mio_taskhub.models import Idea, IdeaChange


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


def _fake_creativity(available=True, ok=True, ids=("h1", "h2"), raise_exc=None):
    def f(**kw):
        if raise_exc:
            raise raise_exc
        return {"available": available, "ok": ok,
                "items": [{"id": x, "title": f"t{x}", "status": "active"} for x in ids]}
    return f


async def _make_idea(c, **kw):
    r = await c.post("/api/v1/ideas", json={"title": kw.pop("title", "假设想法"), **kw})
    assert r.status_code == 200
    return r.json()


# ---------- FR-12：假设导入 ----------

def test_hypotheses_import_happy_dedupe_idempotent(monkeypatch):
    """FR-12：集合合并去重、幂等（重复导入 added=[]）、导入进版本流与 diff。"""
    monkeypatch.setattr(ideas_api.mio_runtime, "creativity", _fake_creativity())
    state = {}

    async def k(c):
        base = await _make_idea(c)
        state["iid"] = base["id"]
        v0 = base["version"]
        r = await c.post(f"/api/v1/ideas/{base['id']}/hypotheses/import",
                         json={"ids": ["h1", "h2", "h1"]})
        assert r.status_code == 200
        b = r.json()
        assert b["ok"] is True
        assert b["added"] == ["h1", "h2"]  # 去重保序
        assert b["idea"]["hypotheses"] == ["h1", "h2"]
        assert b["idea"]["version"] == v0 + 1
        # 幂等：重复导入不新增、不重复 bump
        r2 = await c.post(f"/api/v1/ideas/{base['id']}/hypotheses/import",
                          json={"ids": ["h1", "h2"]})
        assert r2.status_code == 200
        assert r2.json()["added"] == []
        assert r2.json()["idea"]["hypotheses"] == ["h1", "h2"]
        assert r2.json()["idea"]["version"] == v0 + 1
    _with_client(k)
    with Session(engine) as s:
        rows = s.exec(select(IdeaChange).where(IdeaChange.idea_id == state["iid"])).all()
        diffs = {k for r in rows for k in (r.diff or {})}
        assert "hypotheses" in diffs  # 导入进 diff（审计可回溯）
        idea = s.get(Idea, state["iid"])
        assert idea.hypotheses == ["h1", "h2"]


def test_hypotheses_import_merges_existing(monkeypatch):
    """FR-12：集合合并——已有的不重复加，只补缺。"""
    monkeypatch.setattr(ideas_api.mio_runtime, "creativity",
                        _fake_creativity(ids=("h1", "h2")))

    async def k(c):
        base = await _make_idea(c, hypotheses=["h2"])
        r = await c.post(f"/api/v1/ideas/{base['id']}/hypotheses/import",
                         json={"ids": ["h1", "h2"]})
        assert r.status_code == 200
        assert r.json()["added"] == ["h1"]
        assert r.json()["idea"]["hypotheses"] == ["h2", "h1"]  # 原序保留
    _with_client(k)


def test_hypotheses_import_unknown_id_422(monkeypatch):
    """FR-12：Mio 里不存在的 id → 422 明确报错，不落库。"""
    monkeypatch.setattr(ideas_api.mio_runtime, "creativity", _fake_creativity(ids=("h1",)))

    async def k(c):
        base = await _make_idea(c)
        r = await c.post(f"/api/v1/ideas/{base['id']}/hypotheses/import",
                         json={"ids": ["h1", "nope"]})
        assert r.status_code == 422
        assert "nope" in r.json()["detail"]
        r2 = await c.get(f"/api/v1/ideas/{base['id']}")
        assert r2.json()["hypotheses"] == []
    _with_client(k)


def test_hypotheses_import_mio_unavailable_503(monkeypatch):
    """FR-12：Mio 不可用（runtime 不可用 / CLI 失败 / 抛异常）→ 503 不 500。"""
    for fake in (_fake_creativity(available=False),
                 _fake_creativity(ok=False),
                 _fake_creativity(raise_exc=RuntimeError("mio down"))):
        monkeypatch.setattr(ideas_api.mio_runtime, "creativity", fake)

        async def k(c):
            base = await _make_idea(c)
            r = await c.post(f"/api/v1/ideas/{base['id']}/hypotheses/import",
                             json={"ids": ["h1"]})
            assert r.status_code == 503
        _with_client(k)


def test_hypotheses_import_validation_422(monkeypatch):
    """FR-12：ids 形状不合法（非 list / 非字符串元素）→ 422。"""
    monkeypatch.setattr(ideas_api.mio_runtime, "creativity", _fake_creativity())

    async def k(c):
        base = await _make_idea(c)
        for bad in ("h1", [123], [""], [None]):
            r = await c.post(f"/api/v1/ideas/{base['id']}/hypotheses/import",
                             json={"ids": bad})
            assert r.status_code == 422, f"bad={bad!r} 应 422"
        # 未知 idea → 404（合法 ids）
        r = await c.post("/api/v1/ideas/nosuchid/hypotheses/import", json={"ids": ["h1"]})
        assert r.status_code == 404
    _with_client(k)


def test_hypotheses_import_empty_noop_skips_mio(monkeypatch):
    """空 ids → 幂等 no-op，且不打 Mio。"""
    calls = []

    def spy(**kw):
        calls.append(kw)
        return _fake_creativity()(**kw)

    monkeypatch.setattr(ideas_api.mio_runtime, "creativity", spy)

    async def k(c):
        base = await _make_idea(c)
        r = await c.post(f"/api/v1/ideas/{base['id']}/hypotheses/import", json={"ids": []})
        assert r.status_code == 200
        assert r.json()["added"] == []
    _with_client(k)
    assert calls == []


# ---------- FR-15：单条假设人工回写 ----------

def test_patch_assumption_single_entry_and_diff():
    """FR-15：只改一条（另一条不动）、diff 键 assumptions[hid]、版本递增。"""
    state = {}

    async def k(c):
        base = await _make_idea(c, assumptions=[
            {"hid": "h1", "status": "active", "note": "n1"},
            {"hid": "h2", "status": "active", "note": "n2"},
        ])
        state["iid"] = base["id"]
        state["v0"] = base["version"]
        r = await c.patch(f"/api/v1/ideas/{base['id']}/assumptions/h2",
                          json={"status": "validated", "confirmed_by": "tester"})
        assert r.status_code == 200
        i = r.json()
        assert i["assumptions"][0] == {"hid": "h1", "status": "active", "note": "n1"}  # 不动
        assert i["assumptions"][1]["status"] == "validated"
        assert i["assumptions"][1]["confirmed_by"] == "tester"
        assert i["assumptions"][1]["note"] == "n2"  # 未传字段保留
        assert i["version"] == state["v0"] + 1
    _with_client(k)
    with Session(engine) as s:
        rows = s.exec(select(IdeaChange).where(IdeaChange.idea_id == state["iid"])).all()
        keys = {k for r in rows for k in (r.diff or {})}
        assert "assumptions[h2]" in keys
        assert "assumptions[h1]" not in keys  # 只写一条的 diff
        hit = next(r for r in rows if "assumptions[h2]" in (r.diff or {}))
        d = hit.diff["assumptions[h2]"]
        assert d["old"]["status"] == "active"
        assert d["new"]["status"] == "validated"


def test_patch_assumption_hid_not_found_404():
    """FR-15：hid 不存在 → 404（不静默创建）。"""
    async def k(c):
        base = await _make_idea(c, assumptions=[{"hid": "h1", "status": "active"}])
        r = await c.patch(f"/api/v1/ideas/{base['id']}/assumptions/nope",
                          json={"status": "validated"})
        assert r.status_code == 404
        r2 = await c.patch("/api/v1/ideas/nosuchid/assumptions/h1",
                           json={"status": "validated"})
        assert r2.status_code == 404
    _with_client(k)


def test_patch_assumption_validation_422():
    """FR-15：空 body / 非字符串值 → 422。"""
    async def k(c):
        base = await _make_idea(c, assumptions=[{"hid": "h1", "status": "active"}])
        r = await c.patch(f"/api/v1/ideas/{base['id']}/assumptions/h1", json={})
        assert r.status_code == 422
        r2 = await c.patch(f"/api/v1/ideas/{base['id']}/assumptions/h1",
                           json={"status": 123})
        assert r2.status_code == 422
    _with_client(k)


def test_patch_assumption_replay_is_noop():
    """FR-15：同 payload 重放幂等——值全同不 bump 版本、不重复进 diff。"""
    state = {}

    async def k(c):
        base = await _make_idea(c, assumptions=[{"hid": "h1", "status": "active"}])
        state["iid"] = base["id"]
        r = await c.patch(f"/api/v1/ideas/{base['id']}/assumptions/h1",
                          json={"status": "validated"})
        assert r.status_code == 200
        state["v1"] = r.json()["version"]
        r2 = await c.patch(f"/api/v1/ideas/{base['id']}/assumptions/h1",
                           json={"status": "validated"})
        assert r2.status_code == 200
        assert r2.json()["version"] == state["v1"]  # 重放不 bump
    _with_client(k)
    with Session(engine) as s:
        rows = s.exec(select(IdeaChange).where(IdeaChange.idea_id == state["iid"])).all()
        assert len(rows) == 1  # 只有一条真实变更


def test_patch_assumption_concurrent_no_lost_update():
    """FR-15：并发两次回写（不同 hid）均进 diff、无丢更新。"""
    state = {}

    async def k(c):
        base = await _make_idea(c, assumptions=[
            {"hid": "h1", "status": "active"},
            {"hid": "h2", "status": "active"},
        ])
        state["iid"] = base["id"]
        state["v0"] = base["version"]
    _with_client(k)

    errors = []

    def worker(hid, status):
        try:
            with Session(engine) as s:
                ideas_api.patch_idea_assumption(state["iid"], hid,
                                                {"status": status}, db=s)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=worker, args=("h1", "validated"))
    t2 = threading.Thread(target=worker, args=("h2", "rejected"))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert errors == [], f"并发回写不应报错: {errors}"

    with Session(engine) as s:
        idea = s.get(Idea, state["iid"])
        assert {e["hid"]: e["status"] for e in idea.assumptions} == {
            "h1": "validated", "h2": "rejected"}  # 双写都生效（无丢更新）
        assert idea.version == state["v0"] + 2  # 两次 bump 串行化
        rows = s.exec(select(IdeaChange).where(IdeaChange.idea_id == state["iid"])).all()
        keys = {k for r in rows for k in (r.diff or {})}
        assert "assumptions[h1]" in keys and "assumptions[h2]" in keys  # 两条都进 diff
