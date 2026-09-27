"""P3 FR-27：驾驶舱复盘区真实聚合（run 成败 + 最近明细 + P2 评审记录）。"""
import asyncio
from datetime import datetime, timedelta

import httpx
from sqlmodel import Session, select

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.api import cockpit
from mio_taskhub.models import Discussion, Run, RunState, Task


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


async def _make_idea(c, title="复盘想法"):
    r = await c.post("/api/v1/ideas", json={"title": title})
    assert r.status_code == 200
    return r.json()["id"]


def _mk_task(iid, title):
    from mio_taskhub.models import Task as T
    with Session(engine) as s:
        t = T(title=title, idea_id=iid, depends_on=[])
        s.add(t)
        s.commit()
        return t.id


def _mk_run(task_id, state, exit_code=None, finished_at=None, result=None):
    import uuid
    with Session(engine) as s:
        r = Run(id=str(uuid.uuid4())[:8], task_id=task_id, agent_name="tester",
                state=state, attempt=1, exit_code=exit_code,
                finished_at=finished_at, result=result)
        s.add(r)
        s.commit()
        return r.id


def _retro(iid):
    async def k(c):
        r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
        assert r.status_code == 200
        return r.json()["sections"]["retrospective"]
    return _with_client(k)


def test_run_outcome_summary_and_recent_runs():
    """FR-27：finished 按 exit_code 分 success/failure、非 finished 计 pending；明细倒序 + 截断 200。"""
    iid = _with_client(_make_idea)
    t1 = _mk_task(iid, "任务1")
    t2 = _mk_task(iid, "任务2")
    other = _mk_task("other", "他想任务")  # 不属于本想法，不计入
    base = datetime(2026, 9, 27, 12, 0, 0)
    _mk_run(t1, RunState.FINISHED, exit_code=0, finished_at=base,
            result="ok " * 100)
    _mk_run(t2, RunState.FINISHED, exit_code=1, finished_at=base + timedelta(minutes=5),
            result="boom")
    _mk_run(t2, RunState.RETRYING, finished_at=None)
    _mk_run(t2, RunState.RUNNING)
    _mk_run(other, RunState.FINISHED, exit_code=0, finished_at=base)

    sec = _retro(iid)
    assert sec["status"] == "ok"
    data = sec["data"]
    assert data["summary"] == {"success": 1, "failure": 1, "pending": 2, "total": 4}
    assert len(data["items"]) == 2  # 最近 5 条内，finished 2 条
    first = data["items"][0]  # 倒序：exit_code=1 的更晚
    assert first["task_id"] == t2 and first["exit_code"] == 1
    assert first["task_title"] == "任务2" and first["result_excerpt"] == "boom"
    second = data["items"][1]
    assert second["task_id"] == t1 and second["exit_code"] == 0
    assert len(second["result_excerpt"]) == 200  # 截断 200 字符
    assert data["reviews"] == []


def test_review_records_aggregated():
    """FR-27：mode=review 且 closed 的讨论倒序入复盘，三计数（决策/行动项/已转任务）正确。"""
    iid = _with_client(_make_idea)
    with Session(engine) as s:
        s.add(Discussion(idea_id=iid, topic="P3 评审", mode="review",
                         status="closed", ended_at=datetime(2026, 9, 27, 10, 0, 0),
                         review={"decisions": [{"id": "d1"}, {"id": "d2"}],
                                 "action_items": [
                                     {"id": "a1", "task_id": "t-1"},
                                     {"id": "a2", "task_id": ""},
                                     {"id": "a3", "task_id": None}]}))
        s.add(Discussion(idea_id=iid, topic="自由讨论", mode="free",
                         status="closed", ended_at=datetime(2026, 9, 27, 11, 0, 0),
                         review={"decisions": ["x"], "action_items": []}))
        s.add(Discussion(idea_id=iid, topic="未关闭评审", mode="review",
                         status="open", review={"decisions": ["y"]}))
        s.add(Discussion(idea_id="other", topic="他想评审", mode="review",
                         status="closed", review={"decisions": ["z"]}))
        s.commit()

    sec = _retro(iid)
    assert sec["status"] == "ok"
    revs = sec["data"]["reviews"]
    assert len(revs) == 1  # 只计本想法 + mode=review + closed
    r = revs[0]
    assert r["topic"] == "P3 评审" and r["ended_at"] is not None
    assert r["decision_count"] == 2
    assert r["action_item_count"] == 3
    assert r["converted_count"] == 1  # task_id 非空且非 None 才算已转


def test_empty_ok_and_section_degraded():
    """FR-27：空数据 status=ok 全 0；单区异常仅本区 degraded，接口不 500。"""
    iid = _with_client(_make_idea)
    sec = _retro(iid)
    assert sec["status"] == "ok"
    assert sec["data"]["summary"] == {"success": 0, "failure": 0,
                                      "pending": 0, "total": 0}
    assert sec["data"]["items"] == [] and sec["data"]["reviews"] == []

    # 模拟构建器异常 → 仅本区 degraded（真实 builder 均为协程）
    async def boom(*a, **kw):
        raise RuntimeError("kaboom")
    orig = cockpit.SECTION_BUILDERS["retrospective"]
    cockpit.SECTION_BUILDERS["retrospective"] = boom
    try:
        async def k(c):
            r = await c.get(f"/api/v1/ideas/{iid}/cockpit")
            assert r.status_code == 200
            secs = r.json()["sections"]
            assert secs["retrospective"]["status"] == "degraded"
            assert secs["retrospective"]["reason"] == "RuntimeError"
            assert secs["goal"]["status"] == "ok"  # 其他区不受拖累
        _with_client(k)
    finally:
        cockpit.SECTION_BUILDERS["retrospective"] = orig
