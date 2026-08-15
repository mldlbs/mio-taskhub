# tests/test_breakdown.py
import asyncio
import httpx
from mio_taskhub.main import app


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


def test_breakdown_creates_tasks_and_resolves_refs():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "拆我"})).json()["id"]
        r = await c.post(f"/api/v1/ideas/{iid}/breakdown", json={
            "tasks": [
                {"title": "写 spec", "ref": "t1", "depends_on": []},
                {"title": "写 plan", "ref": "t2", "depends_on": ["t1"]},
            ]
        })
        assert r.status_code == 200
        data = r.json()
        assert data["idea"]["status"] == "broken_down"
        assert len(data["tasks"]) == 2
        t2 = next(x for x in data["tasks"] if x["ref"] == "t2")
        t1 = next(x for x in data["tasks"] if x["ref"] == "t1")
        assert t2["depends_on"] == [t1["id"]]
        d = await c.get(f"/api/v1/tasks/{t1['id']}")
        assert d.json()["idea_id"] == iid
        det = await c.get(f"/api/v1/ideas/{iid}")
        assert len(det.json()["tasks"]) == 2
    _with_client(k)


def test_breakdown_idempotent_409():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "x"})).json()["id"]
        body = {"tasks": [{"title": "a", "depends_on": []}]}
        assert (await c.post(f"/api/v1/ideas/{iid}/breakdown", json=body)).status_code == 200
        assert (await c.post(f"/api/v1/ideas/{iid}/breakdown", json=body)).status_code == 409
    _with_client(k)


def test_breakdown_unknown_ref_422():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "x"})).json()["id"]
        r = await c.post(f"/api/v1/ideas/{iid}/breakdown", json={
            "tasks": [{"title": "a", "depends_on": ["nope"]}]
        })
        assert r.status_code == 422
    _with_client(k)


def test_breakdown_cycle_422_rollback():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "x"})).json()["id"]
        r = await c.post(f"/api/v1/ideas/{iid}/breakdown", json={
            "tasks": [
                {"title": "a", "ref": "a", "depends_on": ["b"]},
                {"title": "b", "ref": "b", "depends_on": ["a"]},
            ]
        })
        assert r.status_code == 422
        det = await c.get(f"/api/v1/ideas/{iid}")
        assert det.json()["status"] == "new"
        tasks = await c.get("/api/v1/tasks")
        assert all("a" not in t["title"] and "b" not in t["title"] for t in tasks.json())
    _with_client(k)


def test_breakdown_404():
    async def k(c):
        r = await c.post("/api/v1/ideas/nope/breakdown", json={"tasks": [{"title": "a"}]})
        assert r.status_code == 404
    _with_client(k)
