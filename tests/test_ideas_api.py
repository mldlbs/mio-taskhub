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


def test_idea_versioning_full():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t"})).json()["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "补充"})
        assert r.status_code == 200
        assert r.json()["version"] == 2
        d = await c.get(f"/api/v1/ideas/{iid}")
        changes = d.json()["changes"]
        assert len(changes) == 1
        assert changes[0]["version"] == 2
        assert changes[0]["diff"] == {"description": {"old": "", "new": "补充"}}
    _with_client(k)


def test_idea_versioning_history_only_and_none():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t", "description": "a"})).json()["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "b", "versioning": "history_only"})
        assert r.json()["version"] == 1          # 不递增
        d = await c.get(f"/api/v1/ideas/{iid}")
        assert len(d.json()["changes"]) == 1      # 但留痕
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "c", "versioning": "none"})
        assert r.json()["version"] == 1
        d = await c.get(f"/api/v1/ideas/{iid}")
        assert len(d.json()["changes"]) == 1      # 无新增
    _with_client(k)


def test_idea_versioning_no_change_no_version():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t", "description": "a"})).json()["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "a"})
        assert r.json()["version"] == 1           # 未变化不触发
    _with_client(k)


def test_idea_versioning_invalid():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t"})).json()["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "x", "versioning": "bogus"})
        assert r.status_code == 422
    _with_client(k)


def test_change_tracking_task_created_and_dedup():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "需求A"})).json()["id"]
        # 拆解产生关联 task（breakdown 自动设 idea_id 与 BROKEN_DOWN）
        r = await c.post(f"/api/v1/ideas/{iid}/breakdown", json={
            "tasks": [{"ref": "t1", "title": "实现A"}]
        })
        assert r.status_code == 200
        # 第一次修改 → 生成变更任务（含 change_reason）
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "v2描述", "change_reason": "需求方补充"})
        tasks = (await c.get("/api/v1/tasks")).json()
        ct = [t for t in tasks if t["idea_id"] == iid and t["title"].startswith("[变更]")]
        assert len(ct) == 1
        assert ct[0]["title"] == "[变更] 需求A v2"
        assert ct[0]["stage"] == "review"
        det = (await c.get(f"/api/v1/tasks/{ct[0]['id']}")).json()
        assert "v2" in det["description"]
        assert "需求方补充" in det["description"]
        # 第二次修改 → 更新而非新建
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "v3描述"})
        tasks = (await c.get("/api/v1/tasks")).json()
        ct = [t for t in tasks if t["idea_id"] == iid and t["title"].startswith("[变更]")]
        assert len(ct) == 1
        assert ct[0]["title"] == "[变更] 需求A v3"
    _with_client(k)


def test_idea_versioning_diff_on_other_fields():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t", "project": "", "labels": []})).json()["id"]
        # 改 project → 一条记录，diff 键为 project
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"project": "p1"})
        assert r.json()["version"] == 2
        d = await c.get(f"/api/v1/ideas/{iid}")
        changes = d.json()["changes"]
        assert len(changes) == 1
        assert set(changes[0]["diff"].keys()) == {"project"}
        assert changes[0]["diff"]["project"] == {"old": "", "new": "p1"}
        # 改 labels → 第二条记录，diff 键为 labels
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"labels": ["x", "y"]})
        assert r.json()["version"] == 3
        d = await c.get(f"/api/v1/ideas/{iid}")
        changes = d.json()["changes"]
        assert len(changes) == 2
        assert set(changes[0]["diff"].keys()) == {"labels"}   # 最新在前（id desc）
        assert changes[0]["diff"]["labels"] == {"old": [], "new": ["x", "y"]}
    _with_client(k)


def test_change_tracking_respects_flags():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "需求B"})).json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/breakdown", json={"tasks": [{"ref": "t1", "title": "实现B"}]})
        # history_only → 不生成
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "h", "versioning": "history_only"})
        tasks = (await c.get("/api/v1/tasks")).json()
        assert not any(t["idea_id"] == iid and t["title"].startswith("[变更]") for t in tasks)
        # full + track_change=false → 不生成
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "f", "track_change": False})
        tasks = (await c.get("/api/v1/tasks")).json()
        assert not any(t["idea_id"] == iid and t["title"].startswith("[变更]") for t in tasks)
        # full → 生成
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "g"})
        tasks = (await c.get("/api/v1/tasks")).json()
        assert any(t["idea_id"] == iid and t["title"].startswith("[变更]") for t in tasks)
    _with_client(k)


def test_change_tracking_requires_associated_task():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "需求C"})).json()["id"]
        # 未拆解（无关联 task）→ 不生成
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "v2"})
        tasks = (await c.get("/api/v1/tasks")).json()
        assert not any(t["idea_id"] == iid and t["title"].startswith("[变更]") for t in tasks)
    _with_client(k)