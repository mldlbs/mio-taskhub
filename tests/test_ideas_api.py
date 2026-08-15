import asyncio
import httpx
import pytest
from mio_taskhub.main import app


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


def test_idea_create_list_get():
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "想法墙", "description": "记录随手冒出的需求", "project": "mio"})
        assert r.status_code == 200
        i = r.json()
        assert i["status"] == "new"
        assert i["id"]

        r = await c.get("/api/v1/ideas")
        assert r.status_code == 200
        assert r.json()["count"] == 1

        r = await c.get(f"/api/v1/ideas/{i['id']}")
        assert r.status_code == 200
        assert r.json()["discussions"] == []

        r = await c.get("/api/v1/ideas/xxxx")
        assert r.status_code == 404
    _with_client(k)


def test_idea_requires_title():
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"description": "no title"})
        assert r.status_code == 422
    _with_client(k)


def test_idea_update():
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "t"})
        iid = r.json()["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "补充", "project": "p"})
        assert r.status_code == 200
        assert r.json()["description"] == "补充"
        assert r.json()["project"] == "p"
    _with_client(k)


def test_idea_status_flow():
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "t"})
        iid = r.json()["id"]

        async def set_status(st):
            return await c.post(f"/api/v1/ideas/{iid}/status", json={"status": st})

        assert (await set_status("fermenting")).json()["status"] == "fermenting"
        assert (await set_status("formed")).json()["status"] == "formed"
        assert (await set_status("broken_down")).json()["status"] == "broken_down"
        assert (await set_status("new")).status_code == 422          # cannot go backwards
        assert (await set_status("archived")).status_code == 422     # cannot archive after broken_down
        assert (await set_status("bogus")).status_code == 400
    _with_client(k)


def test_discussion_on_idea():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t"})).json()["id"]
        r = await c.post("/api/v1/discussions", json={"idea_id": iid, "topic": "怎么做", "agent": "me"})
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "open"
        assert d["idea_id"] == iid

        r = await c.post(f"/api/v1/discussions/{d['id']}/messages",
                         json={"author": "me", "role": "user", "content": "需求点"})
        assert r.status_code == 200
        r = await c.post(f"/api/v1/discussions/{d['id']}/messages",
                         json={"author": "opencode", "role": "ask", "content": "要 MVP 吗？"})
        assert r.status_code == 200

        r = await c.get(f"/api/v1/discussions/{d['id']}")
        msgs = r.json()["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user" and msgs[1]["role"] == "ask"

        r = await c.post(f"/api/v1/discussions/{d['id']}/messages", json={"content": "  "})
        assert r.status_code == 422
    _with_client(k)


def test_discussion_list_and_close():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t"})).json()["id"]
        did = (await c.post("/api/v1/discussions", json={"idea_id": iid, "topic": "x"})).json()["id"]

        r = await c.get("/api/v1/discussions", params={"ref_type": "idea", "ref_id": iid})
        assert r.json()["count"] == 1

        r = await c.post(f"/api/v1/discussions/{did}/close", json={"conclusions": "做 Idea 表", "summary": "一轮"})
        assert r.status_code == 200
        assert r.json()["status"] == "closed"
        assert r.json()["conclusions"] == "做 Idea 表"
    _with_client(k)


def test_discussion_bindings():
    async def k(c):
        r = await c.post("/api/v1/discussions", json={"topic": "none"})
        assert r.status_code == 422
        r = await c.post("/api/v1/discussions", json={"idea_id": "nope", "topic": "x"})
        assert r.status_code == 404
        iid = (await c.post("/api/v1/ideas", json={"title": "t"})).json()["id"]
        r = await c.post("/api/v1/discussions", json={"idea_id": iid, "topic": "y", "conclusions": "直接结论"})
        assert r.json()["status"] == "closed"
    _with_client(k)