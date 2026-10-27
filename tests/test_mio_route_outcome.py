# -*- coding: utf-8 -*-
"""TH-01 历史决策闭环测试（FR-1 ~ FR-11）。

覆盖：适配层降级与参数、claim 决策记录、submit 幂等投递、退避与重投、
pending/confirm 语义、决策与投递查询端点、以及「route 不改变派发顺序」。
runtime 一律用 monkeypatch 伪实现，不依赖本机 Mio 安装。
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mio_taskhub import mio_runtime as mio
from mio_taskhub import mio_bridge as bridge
from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import MioOutbox, RouteDecision, Run, Task, TaskStage, TaskState

client = TestClient(app)

ROUTE_OK = {
    "task": "q",
    "count": 1,
    "routes": [{"experienceId": "exp_9", "score": 6.5, "memory": {"id": "m1"}}],
    "relatedMemories": [],
    "agentHealth": {"best": {"agent": "opencode", "successRate": 88, "total": 7},
                    "degraded": [], "stale": False},
    "summary": {"suggestion": "apply the top match first"},
}


def _register(name="opencode"):
    client.post("/api/v1/agents/register", json={"name": name, "agent_type": "code"})


def _task(db, **kw):
    t = Task(id=kw.pop("id", str(uuid.uuid4())[:8]),
             title=kw.pop("title", "TH-01 task"),
             project=kw.pop("project", "mio-taskhub"),
             state=TaskState.QUEUED, stage=TaskStage.READY, **kw)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _claim(agent="opencode", project="mio-taskhub"):
    return client.post(f"/api/v1/tasks/claim?agent={agent}&project={project}")


# ── FR-2：适配层降级与参数 ────────────────────────────────────────────────

def test_route_degraded_without_runtime(monkeypatch):
    monkeypatch.setattr(mio, "available", lambda: False)
    data, reason = mio.task_route_status("q", "opencode", "proj")
    assert data is None and reason == "runtime_unavailable"


def test_route_empty_project_skips_runtime(monkeypatch):
    monkeypatch.setattr(mio, "available", lambda: True)

    def _boom(*a, **k):
        raise AssertionError("run_mio must not be called when project is empty")

    monkeypatch.setattr(mio, "run_mio", _boom)
    data, reason = mio.task_route_status("q", "opencode", "   ")
    assert data is None and reason == "empty_project"


def test_route_passes_identity_and_persist_flags(monkeypatch):
    captured = {}
    monkeypatch.setattr(mio, "available", lambda: True)
    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])

    def fake_run(args, timeout=0):
        captured["args"] = list(args)
        return {"ok": True, "code": 0, "stdout": json.dumps(ROUTE_OK), "stderr": ""}

    monkeypatch.setattr(mio, "run_mio", fake_run)
    data, reason = mio.task_route_status("fix bug", "opencode", "proj")
    assert reason == "none" and data["count"] == 1
    args = captured["args"]
    assert "--persist-query" in args
    assert args[args.index("--agent-id") + 1] == "opencode"
    assert args[args.index("--project") + 1] == "proj"
    assert args[args.index("--scope") + 1] == "all"


def test_route_timeout_reported(monkeypatch):
    monkeypatch.setattr(mio, "available", lambda: True)
    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(mio, "run_mio", lambda args, timeout=0: {
        "ok": False, "code": None, "stdout": "", "stderr": "timeout"})
    data, reason = mio.task_route_status("q", "a", "p")
    assert data is None and reason == "timeout"


def test_route_bad_schema_reported(monkeypatch):
    monkeypatch.setattr(mio, "available", lambda: True)
    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(mio, "run_mio", lambda args, timeout=0: {
        "ok": True, "code": 0, "stdout": json.dumps({"unexpected": 1}), "stderr": ""})
    data, reason = mio.task_route_status("q", "a", "p")
    assert data is None and reason == "schema_error"


# ── FR-5：写回参数与三态 ──────────────────────────────────────────────────

def test_outcome_requires_yes_and_identity(monkeypatch):
    captured = {}
    monkeypatch.setattr(mio, "available", lambda: True)
    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])

    def fake_json(args, timeout=0):
        captured["args"] = list(args)
        return True, {"recorded": True}

    monkeypatch.setattr(mio, "_json_stdout", fake_json)
    assert mio.task_outcome("success", "t", "opencode", "proj") == {"recorded": True}
    args = captured["args"]
    assert "--yes" in args and "--outcome" in args
    assert args[args.index("--agent-id") + 1] == "opencode"


def test_outcome_rejects_unknown_state(monkeypatch):
    monkeypatch.setattr(mio, "available", lambda: True)
    assert mio.task_outcome("weird", "t", "a", "p") is None


def test_outcome_rejects_empty_project(monkeypatch):
    monkeypatch.setattr(mio, "available", lambda: True)
    assert mio.task_outcome("success", "t", "a", "") is None


# ── FR-3 / FR-4：claim 注入与决策记录 ─────────────────────────────────────

def test_claim_writes_route_decision(monkeypatch):
    _register()
    monkeypatch.setattr(mio, "available", lambda: True)
    monkeypatch.setattr(mio, "task_route_status", lambda *a, **k: (ROUTE_OK, "none"))
    with Session(engine) as db:
        task = _task(db)
    r = _claim()
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == task.id
    route = body["route"]
    assert route["count"] == 1
    assert route["top_experience_id"] == "exp_9"
    assert route["agent_health_best"] == "opencode:88:7"
    assert route["degraded_reason"] == "none"
    assert route["decision_id"] == "rd-%s" % body["id"]
    with Session(engine) as db:
        row = db.exec(select(RouteDecision)).first()
        assert row.run_id == body["id"]
        assert row.project == "mio-taskhub"
        assert row.route_count == 1


def test_claim_fail_open_without_runtime(monkeypatch):
    _register()
    monkeypatch.setattr(mio, "available", lambda: False)
    with Session(engine) as db:
        _task(db)
    r = _claim()
    assert r.status_code == 200
    assert r.json()["route"] is None
    with Session(engine) as db:
        row = db.exec(select(RouteDecision)).first()
        assert row.degraded_reason == "runtime_unavailable"


def test_claim_empty_project_never_calls_runtime(monkeypatch):
    _register()
    monkeypatch.setattr(mio, "available", lambda: True)

    def _boom(*a, **k):
        raise AssertionError("run_mio must not be called for project-less task")

    monkeypatch.setattr(mio, "run_mio", _boom)
    with Session(engine) as db:
        _task(db, project="")
    # 不带 project 查询参数：claim_task 若收到 project 会回填到空 project 的任务
    r = client.post("/api/v1/tasks/claim?agent=opencode")
    assert r.status_code == 200
    assert r.json()["route"] is None
    with Session(engine) as db:
        row = db.exec(select(RouteDecision)).first()
        assert row.degraded_reason == "empty_project"


def test_route_does_not_change_candidate_order(monkeypatch):
    """AC-3：route 命中与否不得改变被领取的任务（本地规则优先）。"""
    _register()
    monkeypatch.setattr(mio, "available", lambda: True)
    monkeypatch.setattr(mio, "task_route_status", lambda *a, **k: (ROUTE_OK, "none"))
    with Session(engine) as db:
        first_id = _task(db, title="first").id
        second_id = _task(db, title="second").id
    r = _claim()
    assert r.status_code == 200
    assert r.json()["task_id"] == first_id, "route must not reorder candidates"
    assert second_id != first_id


def test_claim_existing_run_reuses_decision(monkeypatch):
    _register()
    monkeypatch.setattr(mio, "available", lambda: True)
    calls = {"n": 0}

    def fake_route(*a, **k):
        calls["n"] += 1
        return ROUTE_OK, "none"

    monkeypatch.setattr(mio, "task_route_status", fake_route)
    with Session(engine) as db:
        _task(db)
    first = _claim().json()
    second = _claim().json()
    assert second["id"] == first["id"]
    assert second["route"]["decision_id"] == first["route"]["decision_id"]
    assert calls["n"] == 1, "existing run must not call route again"
    with Session(engine) as db:
        assert len(db.exec(select(RouteDecision)).all()) == 1


# ── FR-6 / FR-7：submit 入队、幂等、退避 ──────────────────────────────────

def _claimed_run(monkeypatch, project="mio-taskhub"):
    _register()
    monkeypatch.setattr(mio, "available", lambda: False)
    with Session(engine) as db:
        task = _task(db, project=project)
    body = _claim().json()
    return task, body["id"]


def test_submit_success_enqueues_once(monkeypatch):
    task, run_id = _claimed_run(monkeypatch)
    r = client.post(f"/api/v1/runs/{run_id}/result?agent=opencode",
                    json={"success": True, "result": "all green"})
    assert r.status_code == 200
    with Session(engine) as db:
        rows = db.exec(select(MioOutbox)).all()
        assert len(rows) == 1
        payload = json.loads(rows[0].payload)
        assert payload["outcome"] == "success"
        assert payload["project"] == "mio-taskhub"
        assert payload["traceId"] == "taskhub:%s:%s" % (task.id, run_id)
        assert rows[0].status == "pending"


def test_submit_failure_maps_failure(monkeypatch):
    _, run_id = _claimed_run(monkeypatch)
    r = client.post(f"/api/v1/runs/{run_id}/result?agent=opencode",
                    json={"success": False, "result": "boom", "exit_code": 1})
    assert r.status_code == 200
    with Session(engine) as db:
        row = db.exec(select(MioOutbox)).first()
        payload = json.loads(row.payload)
        assert payload["outcome"] == "failure"
        assert payload["summary"].startswith("[execution_error]")


def test_outbox_enqueue_is_idempotent(monkeypatch):
    _, run_id = _claimed_run(monkeypatch)
    client.post(f"/api/v1/runs/{run_id}/result?agent=opencode",
                json={"success": True, "result": "ok"})
    with Session(engine) as db:
        run = db.get(Run, run_id)
        task = db.get(Task, run.task_id)
        assert bridge.enqueue_outcome(db, task, run, "success", "again") is False
        assert len(db.exec(select(MioOutbox)).all()) == 1


def test_empty_project_enqueues_dead(monkeypatch):
    _register()
    with Session(engine) as db:
        task = _task(db, project="")
        run = Run(id="runempty", task_id=task.id, agent_name="opencode")
        db.add(run)
        db.commit()
        assert bridge.enqueue_outcome(db, task, run, "success", "ok") is True
        row = db.exec(select(MioOutbox)).first()
        assert row.status == "dead"


def test_deliver_success_marks_sent(monkeypatch):
    monkeypatch.setattr(bridge.mio, "task_outcome", lambda **k: {"recorded": True})
    with Session(engine) as db:
        task = _task(db)
        run = Run(id="r1", task_id=task.id, agent_name="opencode")
        db.add(run)
        db.commit()
        bridge.enqueue_outcome(db, task, run, "success", "ok")
        row = db.exec(select(MioOutbox)).first()
        assert bridge.deliver_row(db, row) == "sent"
        assert row.attempts == 1 and row.last_error is None


def test_deliver_failure_sets_backoff(monkeypatch):
    monkeypatch.setattr(bridge.mio, "task_outcome", lambda **k: None)
    with Session(engine) as db:
        task = _task(db)
        run = Run(id="r2", task_id=task.id, agent_name="opencode")
        db.add(run)
        db.commit()
        bridge.enqueue_outcome(db, task, run, "failure", "bad")
        row = db.exec(select(MioOutbox)).first()
        assert bridge.deliver_row(db, row) == "failed"
        assert row.attempts == 1
        due = row.next_at if row.next_at.tzinfo else row.next_at.replace(tzinfo=timezone.utc)
        assert due > datetime.now(timezone.utc)


def test_deliver_dead_after_max_attempts(monkeypatch):
    monkeypatch.setattr(bridge.mio, "task_outcome", lambda **k: None)
    with Session(engine) as db:
        task = _task(db)
        run = Run(id="r3", task_id=task.id, agent_name="opencode")
        db.add(run)
        db.commit()
        bridge.enqueue_outcome(db, task, run, "failure", "bad")
        row = db.exec(select(MioOutbox)).first()
        row.attempts = bridge.MAX_ATTEMPTS - 1
        db.add(row)
        db.commit()
        assert bridge.deliver_row(db, row) == "dead"


def test_drain_skips_when_runtime_unavailable(monkeypatch):
    monkeypatch.setattr(bridge.mio, "available", lambda: False)
    out = bridge.drain_outbox()
    assert out["ok"] is False and out["sent"] == 0


# ── FR-8：pending 与人工确认 ──────────────────────────────────────────────

PENDING_REC = {
    "id": "xfer_1", "experienceId": "exp_9", "source": "auto_claim",
    "sourceAgent": "cli", "targetAgent": "opencode", "project": "mio-taskhub",
    "outcomeImproved": True, "confirmed": False, "confirmable": True,
    "timestamp": "2026-10-27T03:20:00.000Z",
}


def test_pending_lists_confirmable(monkeypatch):
    monkeypatch.setattr(mio, "experience_pending",
                        lambda project="", limit=20, timeout=0: {
                            "count": 1, "total": 1, "project": project,
                            "records": [PENDING_REC]})
    r = client.get("/api/v1/mio/experience/pending?project=mio-taskhub")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["records"][0]["confirmable"] is True


def test_pending_degrades_without_runtime(monkeypatch):
    monkeypatch.setattr(mio, "experience_pending",
                        lambda project="", limit=20, timeout=0: None)
    r = client.get("/api/v1/mio/experience/pending")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["records"] == []


def _fake_experience_json(rec, confirm_payload=None):
    def _fake(args, timeout=0):
        if args[1] == "experience" and args[2] == "list":
            return True, {"count": 1, "total": 1, "records": [rec]}
        if args[1] == "experience" and args[2] == "confirm":
            return True, confirm_payload or {}
        return False, None
    return _fake


def test_confirm_rejects_failed_outcome(monkeypatch):
    rec = dict(PENDING_REC, outcomeImproved=False)
    monkeypatch.setattr(mio, "_json_stdout", _fake_experience_json(rec))
    r = client.post("/api/v1/mio/experience/xfer_1/confirm", json={"by": "human"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "experience.not_confirmable"


def test_confirm_already_confirmed_is_idempotent(monkeypatch):
    rec = dict(PENDING_REC, confirmed=True, confirmedBy="taskhub")
    monkeypatch.setattr(mio, "_json_stdout", _fake_experience_json(rec))
    r = client.post("/api/v1/mio/experience/xfer_1/confirm", json={})
    assert r.status_code == 200
    assert r.json()["state"] == "already"


def test_confirm_not_found(monkeypatch):
    monkeypatch.setattr(mio, "_json_stdout",
                        lambda args, timeout=0: (True, {"records": []}))
    r = client.post("/api/v1/mio/experience/nope/confirm", json={})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "experience.not_found"


def test_confirm_success_calls_runtime(monkeypatch):
    payload = {"confirmed": True, "confirmedCount": 1, "confirmedIds": ["xfer_1"],
               "alreadyConfirmed": [], "rejected": [], "notFound": [],
               "evidence": {"outcomeImproved": True, "confirmedBy": "human",
                            "confirmedAt": "2026-10-27T04:00:00.000Z"}}
    monkeypatch.setattr(mio, "_json_stdout", _fake_experience_json(PENDING_REC, payload))
    r = client.post("/api/v1/mio/experience/xfer_1/confirm", json={"by": "human"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "confirmed" and body["confirmedBy"] == "human"


def test_confirm_rejects_non_auto_claim(monkeypatch):
    rec = dict(PENDING_REC, source="agent_report")
    monkeypatch.setattr(mio, "_json_stdout", _fake_experience_json(rec))
    r = client.post("/api/v1/mio/experience/xfer_1/confirm", json={})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "experience.not_auto_claim"


# ── 查询端点 ─────────────────────────────────────────────────────────────

def test_route_decision_endpoint(monkeypatch):
    _register()
    monkeypatch.setattr(mio, "available", lambda: True)
    monkeypatch.setattr(mio, "task_route_status", lambda *a, **k: (ROUTE_OK, "none"))
    with Session(engine) as db:
        _task(db)
    run_id = _claim().json()["id"]
    r = client.get(f"/api/v1/mio/route-decisions/{run_id}")
    assert r.status_code == 200
    assert r.json()["decision_id"] == "rd-%s" % run_id


def test_route_decision_endpoint_404():
    r = client.get("/api/v1/mio/route-decisions/none")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "route_decision.not_found"


def test_outbox_endpoint_filters(monkeypatch):
    _, run_id = _claimed_run(monkeypatch)
    client.post(f"/api/v1/runs/{run_id}/result?agent=opencode",
                json={"success": True, "result": "ok"})
    r = client.get(f"/api/v1/mio/outbox?run_id={run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1 and body["items"][0]["status"] == "pending"
    assert "payload" not in body["items"][0]
    with_payload = client.get(
        f"/api/v1/mio/outbox?run_id={run_id}&include_payload=true").json()
    assert with_payload["items"][0]["payload"]["outcome"] == "success"
