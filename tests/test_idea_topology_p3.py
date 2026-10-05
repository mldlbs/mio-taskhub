"""P3 FR-26 任务拓扑完整版：多层上下游闭包、环路径、节点上限截断。"""
import asyncio

import httpx

from mio_taskhub.main import app


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


def _mk_task(iid, title, depends_on=None):
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task
    with Session(engine) as s:
        t = Task(title=title, idea_id=iid, depends_on=depends_on or [])
        s.add(t)
        s.commit()
        return t.id


def _make_idea(title="拓扑想法"):
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": title})
        assert r.status_code == 200
        return r.json()["id"]
    return _with_client(k)


def _tasks_data(iid):
    async def k(c):
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        return r.json()["sections"]["tasks"]
    return _with_client(k)


def test_multilayer_upstream_downstream_closure():
    """FR-26：三层上游/下游传递闭包全入图，kind 与计数正确。"""
    iid = _make_idea()
    # 上游链：T1 ← U1 ← U2；下游链：T1 → D1 → D2
    u2 = _mk_task("other", "U2")
    u1 = _mk_task("other", "U1", depends_on=[u2])
    t1 = _mk_task(iid, "T1", depends_on=[u1])
    d1 = _mk_task("other", "D1", depends_on=[t1])
    d2 = _mk_task("other", "D2", depends_on=[d1])

    sec = _tasks_data(iid)
    data = sec["data"]
    assert sec["status"] == "ok"
    kinds = {t["id"]: t["kind"] for t in data["items"]}
    assert kinds[t1] == "direct"
    assert kinds[u1] == "upstream" and kinds[u2] == "upstream"
    assert kinds[d1] == "downstream" and kinds[d2] == "downstream"
    assert data["upstream_total"] == 2 and data["downstream_total"] == 2
    assert data["total"] == 5 and data["has_cycle"] is False
    assert data["cycles"] == [] and data["truncated"] is False
    # P0 downstream 键保留：上游/直接皆非 downstream
    by_id = {t["id"]: t for t in data["items"]}
    assert by_id[u1]["downstream"] is False and by_id[d1]["downstream"] is True
    # 边：闭包内全部依赖边（dep → dependent）
    edges = {(e["from"], e["to"]) for e in data["graph"]["edges"]}
    assert (u2, u1) in edges and (u1, t1) in edges
    assert (t1, d1) in edges and (d1, d2) in edges
    node_ids = {n["id"] for n in data["graph"]["nodes"]}
    assert node_ids == {t1, u1, u2, d1, d2}


def test_cycle_returns_path_and_degrades():
    """FR-26：含环 → cycles 非空环路径 + 仍按 P0 降级列表+警告；无环 cycles=[]。"""
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task

    iid = _make_idea("环想法")
    t1 = _mk_task(iid, "A")
    t2 = _mk_task("other", "B", depends_on=[t1])
    with Session(engine) as s:  # A 依赖 B → 环
        ta = s.get(Task, t1)
        ta.depends_on = [t2]
        s.add(ta)
        s.commit()

    data = _tasks_data(iid)["data"]
    assert data["has_cycle"] is True
    assert data["graph"] is None
    assert data["warning"] and "环" in data["warning"]
    assert len(data["cycles"]) >= 1
    cyc = data["cycles"][0]
    assert set(cyc) == {t1, t2} and len(cyc) == 2  # 环段：两节点（回边不入列）
    assert len(data["items"]) == 2  # P0：降级仍给完整任务列表


def test_node_cap_truncated_and_fold_kept():
    """FR-26：节点上限 100 截断 truncated=true；>20 折叠语义保留。"""
    iid = _make_idea("上限想法")
    root = _mk_task(iid, "根任务")
    for n in range(105):  # 105 下游 → 总节点 106 > 100
        _mk_task("other", f"下游{n}", depends_on=[root])

    data = _tasks_data(iid)["data"]
    assert data["truncated"] is True
    assert data["total"] <= 100
    assert data["folded"] is True  # >20 折叠保留
    assert data["has_cycle"] is False
