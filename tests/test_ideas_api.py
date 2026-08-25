import asyncio
import httpx
import pytest
from sqlmodel import Session
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


def test_idea_history_pagination():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t"})).json()["id"]
        for desc in ("a", "b", "c", "d", "e"):
            await c.patch(f"/api/v1/ideas/{iid}", json={"description": desc})
        d = (await c.get(f"/api/v1/ideas/{iid}", params={"limit": 2})).json()
        assert len(d["changes"]) == 2                 # 默认返回最新 N 条
        latest = [x["version"] for x in d["changes"]]
        assert latest == sorted(latest, reverse=True)  # 按 id 倒序 = 新→旧
        before = d["changes"][-1]["id"]
        d2 = (await c.get(f"/api/v1/ideas/{iid}", params={"before_id": before, "limit": 2})).json()
        assert len(d2["changes"]) == 2                 # 游标翻页
        assert all(x["id"] < before for x in d2["changes"])
        d3 = (await c.get(f"/api/v1/ideas/{iid}", params={"include_changes": "false"})).json()
        assert "changes" not in d3
    _with_client(k)


# ==================== ADR 测试 ====================

def test_adr_evolve_to_adr():
    """测试 Idea 演化为 ADR"""
    async def k(c):
        # 创建 Idea 并推进到 formed
        r = await c.post("/api/v1/ideas", json={"title": "ADR测试", "project": "test"})
        iid = r.json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
        
        # 演化为 ADR
        r = await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={
            "madr_context": "测试背景",
            "madr_decision": "测试决策",
            "madr_consequences": "测试后果",
            "madr_alternatives": [{"name": "方案A", "pros": "优点", "cons": "缺点"}],
            "reason": "测试演化"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["idea_type"] == "adr"
        assert data["adr_status"] == "proposed"
        assert data["madr_context"] == "测试背景"
        assert data["madr_decision"] == "测试决策"
        assert data["version"] == 2
    _with_client(k)


def test_adr_evolve_already_adr():
    """测试已演化的 ADR 不能重复演化"""
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "ADR测试"})
        iid = r.json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
        
        # 第一次演化
        await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={})
        # 第二次演化应失败
        r = await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={})
        assert r.status_code == 409
    _with_client(k)


def test_adr_evolve_wrong_status():
    """测试非 formed 状态不能演化"""
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "ADR测试"})
        iid = r.json()["id"]
        # new 状态不能演化
        r = await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={})
        assert r.status_code == 422
    _with_client(k)


def test_adr_action_accept():
    """测试 accept 操作"""
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "ADR测试"})
        iid = r.json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
        await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={})
        
        r = await c.post(f"/api/v1/ideas/{iid}/adr-action", json={
            "action": "accept",
            "reason": "方案可行"
        })
        assert r.status_code == 200
        assert r.json()["adr_status"] == "accepted"
    _with_client(k)


def test_adr_action_reject():
    """测试 reject 操作"""
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "ADR测试"})
        iid = r.json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
        await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={})
        
        r = await c.post(f"/api/v1/ideas/{iid}/adr-action", json={
            "action": "reject",
            "reason": "方案不可行"
        })
        assert r.status_code == 200
        assert r.json()["adr_status"] == "rejected"
    _with_client(k)


def test_adr_action_deprecate():
    """测试 deprecate 操作"""
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "ADR测试"})
        iid = r.json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
        await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={})
        await c.post(f"/api/v1/ideas/{iid}/adr-action", json={"action": "accept"})
        
        r = await c.post(f"/api/v1/ideas/{iid}/adr-action", json={
            "action": "deprecate",
            "reason": "已过时"
        })
        assert r.status_code == 200
        assert r.json()["adr_status"] == "deprecated"
    _with_client(k)


def test_adr_action_supersede():
    """测试 supersede 操作"""
    async def k(c):
        # 创建两个 ADR
        r1 = await c.post("/api/v1/ideas", json={"title": "旧ADR"})
        old_id = r1.json()["id"]
        await c.post(f"/api/v1/ideas/{old_id}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{old_id}/status", json={"status": "formed"})
        await c.post(f"/api/v1/ideas/{old_id}/evolve-to-adr", json={})
        await c.post(f"/api/v1/ideas/{old_id}/adr-action", json={"action": "accept"})
        
        r2 = await c.post("/api/v1/ideas", json={"title": "新ADR"})
        new_id = r2.json()["id"]
        await c.post(f"/api/v1/ideas/{new_id}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{new_id}/status", json={"status": "formed"})
        await c.post(f"/api/v1/ideas/{new_id}/evolve-to-adr", json={})
        await c.post(f"/api/v1/ideas/{new_id}/adr-action", json={"action": "accept"})
        
        # 旧 ADR 被新 ADR 取代
        r = await c.post(f"/api/v1/ideas/{old_id}/adr-action", json={
            "action": "supersede",
            "replacement_id": new_id,
            "reason": "新方案更优"
        })
        assert r.status_code == 200
        assert r.json()["adr_status"] == "superseded"
        assert r.json()["superseded_by"] == new_id
    _with_client(k)


def test_adr_action_wrong_status():
    """测试错误状态下的操作"""
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "ADR测试"})
        iid = r.json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
        await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={})
        
        # proposed 状态不能 deprecate
        r = await c.post(f"/api/v1/ideas/{iid}/adr-action", json={"action": "deprecate"})
        assert r.status_code == 422
    _with_client(k)


def test_adr_list_filter():
    """测试 ADR 列表筛选"""
    async def k(c):
        # 创建普通 idea
        await c.post("/api/v1/ideas", json={"title": "普通想法"})

        # 创建 ADR
        r = await c.post("/api/v1/ideas", json={"title": "ADR", "project": "test"})
        iid = r.json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
        await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={})

        # 按 idea_type 筛选
        r = await c.get("/api/v1/ideas", params={"idea_type": "adr"})
        assert r.status_code == 200
        assert r.json()["count"] == 1
        assert r.json()["ideas"][0]["idea_type"] == "adr"

        # 按 adr_status 筛选
        r = await c.get("/api/v1/ideas", params={"adr_status": "proposed"})
        assert r.status_code == 200
        assert r.json()["count"] == 1
    _with_client(k)


def test_adr_number_auto_increment():
    """测试 ADR 序号自增"""
    async def k(c):
        r1 = await c.post("/api/v1/ideas", json={"title": "ADR-1"})
        iid1 = r1.json()["id"]
        await c.post(f"/api/v1/ideas/{iid1}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid1}/status", json={"status": "formed"})
        r1 = await c.post(f"/api/v1/ideas/{iid1}/evolve-to-adr", json={})
        assert r1.json()["adr_number"] == 1

        r2 = await c.post("/api/v1/ideas", json={"title": "ADR-2"})
        iid2 = r2.json()["id"]
        await c.post(f"/api/v1/ideas/{iid2}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid2}/status", json={"status": "formed"})
        r2 = await c.post(f"/api/v1/ideas/{iid2}/evolve-to-adr", json={})
        assert r2.json()["adr_number"] == 2
    _with_client(k)


def test_outbox_event_created_on_evolve():
    """测试演化时创建 OutboxEvent"""
    from sqlmodel import select
    from mio_taskhub.models import OutboxEvent, OutboxStatus
    from mio_taskhub.db import engine

    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "Outbox测试"})
        iid = r.json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
        await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={
            "madr_context": "test context",
            "madr_decision": "test decision",
        })

        with Session(engine) as db:
            events = db.exec(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == iid)
            ).all()
            assert len(events) == 1
            assert events[0].event_type == "evolve-to-adr"
            assert events[0].status == OutboxStatus.PENDING
            assert events[0].payload["adr_number"] == 1
    _with_client(k)


def test_outbox_event_created_on_adr_action():
    """测试 ADR 操作时创建 OutboxEvent"""
    from sqlmodel import select
    from mio_taskhub.models import OutboxEvent, OutboxStatus
    from mio_taskhub.db import engine

    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "Outbox测试"})
        iid = r.json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
        await c.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
        await c.post(f"/api/v1/ideas/{iid}/evolve-to-adr", json={})

        await c.post(f"/api/v1/ideas/{iid}/adr-action", json={"action": "accept"})

        with Session(engine) as db:
            events = db.exec(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == iid)
                .where(OutboxEvent.event_type == "accept")
            ).all()
            assert len(events) == 1
            assert events[0].payload["adr_status"] == "accepted"
    _with_client(k)


def test_git_sync_render_adr_markdown():
    """测试 ADR Markdown 渲染"""
    from mio_taskhub.git_sync import _render_adr_markdown
    from mio_taskhub.models import Idea, IdeaType, IdeaStatus
    from datetime import datetime

    idea = Idea(
        id="test-123",
        title="Test ADR",
        idea_type=IdeaType.ADR,
        adr_number=1,
        adr_status=IdeaStatus.ACCEPTED,
        version=1,
        madr_context="Test context",
        madr_decision="Test decision",
        madr_consequences="Test consequences",
        madr_alternatives=[{"title": "Alt 1", "description": "Description 1"}],
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    md = _render_adr_markdown(idea)
    assert "# ADR-001: Test ADR" in md
    assert "ACCEPTED" in md
    assert "Test context" in md
    assert "Test decision" in md
    assert "Alt 1" in md


def test_git_sync_render_adr_alternatives_string():
    """madr_alternatives 为字符串时应整段渲染，而非逐字符拆成列表"""
    from mio_taskhub.git_sync import _render_adr_markdown
    from mio_taskhub.models import Idea, IdeaType, IdeaStatus
    from datetime import datetime

    idea = Idea(
        id="test-456",
        title="Str Alt ADR",
        idea_type=IdeaType.ADR,
        adr_number=2,
        adr_status=IdeaStatus.PROPOSED,
        version=1,
        madr_context="ctx",
        madr_decision="dec",
        madr_consequences="cons",
        madr_alternatives="A) 独立实体表 B) 同步写 Git",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    md = _render_adr_markdown(idea)
    alt_section = md.split("## Alternatives")[1]
    assert "A) 独立实体表 B) 同步写 Git" in alt_section
    assert "\n2. " not in alt_section  # 未被逐字符编号


def test_git_sync_render_readme():
    """测试 README 索引渲染"""
    from mio_taskhub.git_sync import _render_readme
    from mio_taskhub.models import Idea, IdeaType, IdeaStatus
    from datetime import datetime

    adrs = [
        Idea(
            id="test-1", title="ADR One", idea_type=IdeaType.ADR,
            adr_number=1, adr_status=IdeaStatus.ACCEPTED,
            updated_at=datetime.now(),
        ),
        Idea(
            id="test-2", title="ADR Two", idea_type=IdeaType.ADR,
            adr_number=2, adr_status=IdeaStatus.PROPOSED,
            updated_at=datetime.now(),
        ),
    ]

    md = _render_readme(adrs)
    assert "ADR-001" in md
    assert "ADR-002" in md
    assert "ADR One" in md
    assert "ADR Two" in md