# -*- coding: utf-8 -*-
"""P2a 任务层级：迁移、父子关系、领取过滤与回归。

范围严格限定 P2a：
- 只验证 parent_id / role 与领取过滤；
- 不测拆分校验、上下文包、集成门禁（P2b–P2d）。
"""
import os
import tempfile
import threading
import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlmodel import Session, select

from mio_taskhub import background
from mio_taskhub.api.claim import atomic_claim, claim_for, pick_candidate_task
from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.migrations import run_migrations
from mio_taskhub.models import Agent, AgentStatus, Run, Task, TaskState


client = TestClient(app)


def _enable(monkeypatch):
    monkeypatch.setenv("MIO_DECOMPOSE_STAGE", "hierarchy")


def _mk(title, **kw):
    r = client.post("/api/v1/tasks", json={"title": title, "stage": "ready", **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _row(tid):
    with Session(engine) as s:
        return s.get(Task, tid)


def _mk_pair(monkeypatch):
    _enable(monkeypatch)
    pid = _mk("parent")
    cid = _mk("child", parent_id=pid)
    return pid, cid


def _mk_online_agent(name, agent_type="cli"):
    from datetime import datetime, timezone
    with Session(engine) as s:
        s.add(Agent(name=name, agent_type=agent_type, status=AgentStatus.ONLINE,
                    last_heartbeat=datetime.now(timezone.utc)))
        s.commit()


# ── 迁移 ──────────────────────────────────────────────────────────────

def test_migration_idempotent_and_backfills_legacy_leaf():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE task (id VARCHAR PRIMARY KEY, title VARCHAR, state VARCHAR)"))
            conn.execute(text(
                "INSERT INTO task (id, title, state) VALUES ('old1', 'legacy', 'queued')"))
            conn.commit()

        run_migrations(eng)
        run_migrations(eng)  # 重复执行不得报错/改数据

        with eng.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("task")}
            assert {"parent_id", "role", "complexity",
                    "context_package", "decomposition",
                    "gate_tier", "gate_tier_reason", "deviations",
                    "completion_run_id"} <= cols
            row = conn.execute(text(
                "SELECT parent_id, role, complexity, context_package, decomposition, "
                "gate_tier, gate_tier_reason, completion_run_id "
                "FROM task WHERE id='old1'")).fetchone()
            assert row[0] is None
            assert row[1] == "leaf"
            assert row[2] is None and row[3] is None and row[4] is None
            assert row[5] == "light" and row[6] == "" and row[7] == ""
            idx = {r[1] for r in conn.execute(text("PRAGMA index_list('task')"))}
            assert "ix_task_parent_id" in idx
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_legacy_null_role_still_claimable():
    """历史/异常 NULL role 必须按 leaf 处理，不能被过滤掉。"""
    with Session(engine) as s:
        t = Task(id="legacy-null", title="legacy", state=TaskState.QUEUED,
                 stage="ready", role=None)
        s.add(t)
        s.commit()
        run = claim_for("legacy-agent", s)
        assert run is not None and run.task_id == "legacy-null"


# ── 父子关系 ──────────────────────────────────────────────────────────

def test_create_child_sets_parent_main(monkeypatch):
    pid, cid = _mk_pair(monkeypatch)
    p, c = _row(pid), _row(cid)
    assert p.role == "main"
    assert c.role == "child"
    assert c.parent_id == pid

    detail = client.get(f"/api/v1/tasks/{pid}").json()
    assert detail["role"] == "main"
    assert detail["children_total"] == 1
    assert detail["children"][0]["id"] == cid

    kids = client.get(f"/api/v1/tasks/{pid}/children").json()
    assert kids["parent_id"] == pid and kids["children_total"] == 1


def test_child_defaults_to_ready(monkeypatch):
    """P2a：带 parent_id 但未显式给 stage 时，子任务默认 ready（可领取）。"""
    _enable(monkeypatch)
    pid = _mk("default-ready-parent")
    r = client.post("/api/v1/tasks", json={"title": "default-ready-child", "parent_id": pid})
    assert r.status_code == 200, r.text
    c = _row(r.json()["id"])
    assert c.stage == "ready"


def test_parent_id_requires_hierarchy_flag(monkeypatch):
    monkeypatch.delenv("MIO_DECOMPOSE_STAGE", raising=False)
    pid = _mk("flag-parent")
    r = client.post("/api/v1/tasks", json={
        "title": "flag-child", "stage": "ready", "parent_id": pid})
    assert r.status_code == 400


def test_invalid_hierarchy_rejected(monkeypatch):
    _enable(monkeypatch)
    assert client.post("/api/v1/tasks", json={
        "title": "main-direct", "role": "main"}).status_code == 400
    assert client.post("/api/v1/tasks", json={
        "title": "missing-parent", "stage": "ready",
        "parent_id": "nope"}).status_code == 404

    pid = _mk("p")
    cid = _mk("c", parent_id=pid)
    assert client.post("/api/v1/tasks", json={
        "title": "grandchild", "stage": "ready",
        "parent_id": cid}).status_code == 422
    assert client.post("/api/v1/tasks", json={
        "title": "wrong-stage", "stage": "design",
        "parent_id": pid}).status_code == 422


def test_child_under_terminal_parent_rejected(monkeypatch):
    _enable(monkeypatch)
    pid = _mk("terminal-parent")
    with Session(engine) as s:
        p = s.get(Task, pid)
        p.state = TaskState.COMPLETED
        p.stage = "done"
        s.add(p)
        s.commit()
    r = client.post("/api/v1/tasks", json={
        "title": "late-child", "stage": "ready", "parent_id": pid})
    assert r.status_code == 409


# ── 领取过滤：REST / 候选 / CAS / 大厅 / 派单 / 看板 ─────────────────

def test_main_task_not_claimable_but_child_is(monkeypatch):
    pid, cid = _mk_pair(monkeypatch)
    r = client.post("/api/v1/tasks/claim", params={
        "agent": "w1", "task_id": pid})
    assert r.status_code == 409

    r = client.post("/api/v1/tasks/claim", params={"agent": "w2"})
    assert r.status_code == 200, r.text
    assert r.json()["task_id"] == cid
    assert _row(pid).state == TaskState.QUEUED


def test_pick_candidate_and_atomic_claim_exclude_main(monkeypatch):
    pid, _ = _mk_pair(monkeypatch)
    with Session(engine) as s:
        assert pick_candidate_task(s, None, pid) is None
        main = s.get(Task, pid)
        assert atomic_claim(s, "cas-agent", main) is None
        assert s.get(Task, pid).state == TaskState.QUEUED


def test_hall_excludes_main(monkeypatch):
    _enable(monkeypatch)
    pid = _mk("hall-parent", grab_mode=True)
    cid = _mk("hall-child", parent_id=pid, grab_mode=True)
    items = client.get("/api/v1/tasks/hall").json()["items"]
    ids = {x["id"] for x in items}
    assert cid in ids
    assert pid not in ids


def test_board_ready_queue_excludes_main(monkeypatch):
    pid, cid = _mk_pair(monkeypatch)
    queue = client.get("/api/v1/board/summary").json()["ready_queue"]
    ids = {x["id"] for x in queue}
    assert cid in ids
    assert pid not in ids


def test_dispatcher_never_claims_main(monkeypatch):
    pid, cid = _mk_pair(monkeypatch)
    _mk_online_agent("dispatch-agent")
    background._assign_to_idle_agents()

    assert _row(cid).state == TaskState.CLAIMED
    assert _row(pid).state == TaskState.QUEUED
    with Session(engine) as s:
        assert s.exec(select(Run).where(Run.task_id == pid)).first() is None


# ── 并发领取回归 ──────────────────────────────────────────────────────

def test_concurrent_claim_only_child_wins(monkeypatch):
    pid, cid = _mk_pair(monkeypatch)
    results = {}
    barrier = threading.Barrier(2)

    def _try(name):
        try:
            with Session(engine) as s:
                barrier.wait()
                run = claim_for(name, s)
                s.commit()
                results[name] = run.task_id if run else None
        except (sqlite3.OperationalError, SAOperationalError):
            results[name] = None

    threads = [threading.Thread(target=_try, args=(n,)) for n in ("r1", "r2")]
    [t.start() for t in threads]
    [t.join() for t in threads]

    won = [tid for tid in results.values() if tid is not None]
    assert won == [cid]
    with Session(engine) as s:
        assert s.exec(select(Run).where(Run.task_id == pid)).first() is None
        assert len(s.exec(select(Run).where(Run.task_id == cid)).all()) == 1
