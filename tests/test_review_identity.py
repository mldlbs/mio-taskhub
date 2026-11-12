# -*- coding: utf-8 -*-
"""评审人身份验证（F-004 收尾，2026-11-12）回归。

契约：
- `POST /tasks/{id}/reviews` 可带 per-run token（`X-Run-Token` 头或 body.run_token）
  证明「评审人 = 本任务某次 run 的 agent」：
  - 匹配 → reviewer_verified=True + verified_run_id 落库；
  - 不匹配本任务任何 run → 403 review.token_mismatch；
  - body.reviewer 与验证出的 agent 不一致 → 422 review.identity_mismatch（冒充拒绝）；
  - 未填 reviewer → 默认取验证出的 agent 名。
- 不带 token 走人审路径，reviewer_verified=False（未验证声明，与既往一致）。
- 自审门（409）语义不变；token 验证后名字是可信的，409 detail 带 reviewer_verified。

迁移幂等另见 test_migration_adds_review_identity_columns。
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mio_taskhub.api.runs import issue_run_token, run_token_hash
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage, Run, RunState

client = TestClient(app)

GOOD_SUMMARY = "已核对变更范围与测试结果，无阻断问题"


def _mk(stage="review") -> str:
    with Session(engine) as s:
        t = Task(title="review-identity", stage=TaskStage(stage))
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _add_run(tid, agent, token: str = None) -> str:
    with Session(engine) as s:
        rid = f"run-{uuid.uuid4().hex[:8]}"
        r = Run(id=rid, task_id=tid, agent_name=agent, state=RunState.FINISHED)
        if token is not None:
            r.token_hash = run_token_hash(token)
        s.add(r)
        s.commit()
        return rid


# ── token 证明路径 ────────────────────────────────────────────────────────────

def test_verified_review_with_body_token():
    tid = _mk()
    tok = issue_run_token()
    rid = _add_run(tid, "worker-a", tok)
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "comment", "reviewer": "worker-a", "run_token": tok,
    })
    assert r.status_code == 200, r.text
    rev = client.get(f"/api/v1/tasks/{tid}/reviews").json()[-1]
    assert rev["reviewer"] == "worker-a"
    assert rev["reviewer_verified"] is True
    assert rev["verified_run_id"] == rid


def test_token_via_header():
    tid = _mk()
    tok = issue_run_token()
    _add_run(tid, "worker-a", tok)
    r = client.post(f"/api/v1/tasks/{tid}/reviews",
                    headers={"X-Run-Token": tok},
                    json={"decision": "comment", "reviewer": "worker-a"})
    assert r.status_code == 200, r.text
    rev = client.get(f"/api/v1/tasks/{tid}/reviews").json()[-1]
    assert rev["reviewer_verified"] is True


def test_reviewer_defaults_to_verified_agent():
    tid = _mk()
    tok = issue_run_token()
    _add_run(tid, "worker-a", tok)
    r = client.post(f"/api/v1/tasks/{tid}/reviews",
                    json={"decision": "comment", "run_token": tok})
    assert r.status_code == 200, r.text
    rev = client.get(f"/api/v1/tasks/{tid}/reviews").json()[-1]
    assert rev["reviewer"] == "worker-a"
    assert rev["reviewer_verified"] is True


def test_impersonation_rejected_422():
    """持 worker-a 的 token 却声称是 worker-b —— 冒充他人评审，422。"""
    tid = _mk()
    tok = issue_run_token()
    _add_run(tid, "worker-a", tok)
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "comment", "reviewer": "worker-b", "run_token": tok,
    })
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "review.identity_mismatch"
    assert r.json()["detail"]["verified_agent"] == "worker-a"
    # 不落库
    assert client.get(f"/api/v1/tasks/{tid}/reviews").json() == []


def test_wrong_token_403():
    """token 与本任务任何 run 都不匹配 → 403（不区分错 token / 别的任务的 token）。"""
    tid = _mk()
    _add_run(tid, "worker-a", issue_run_token())
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "comment", "reviewer": "worker-a",
        "run_token": issue_run_token(),
    })
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "review.token_mismatch"


# ── 自审门与验证的交互 ────────────────────────────────────────────────────────

def test_verified_self_approval_still_409():
    """token 证明了「我就是执行者」→ approve 仍触发自审门，detail 带验证标志。"""
    tid = _mk()
    tok = issue_run_token()
    _add_run(tid, "worker-a", tok)
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "run_token": tok, "summary": GOOD_SUMMARY,
    })
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "review.self_approval"
    assert r.json()["detail"]["reviewer_verified"] is True


def test_verified_self_review_flag_ok():
    tid = _mk()
    tok = issue_run_token()
    _add_run(tid, "worker-a", tok)
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "run_token": tok,
        "summary": GOOD_SUMMARY, "self_review": True,
    })
    assert r.status_code == 200, r.text
    rev = client.get(f"/api/v1/tasks/{tid}/reviews").json()[-1]
    assert rev["reviewer"] == "worker-a" and rev["reviewer_verified"] is True


def test_unverified_claim_has_no_proof():
    """不带 token 的人审路径：reviewer_verified=False，信任级别与既往一致。"""
    tid = _mk()
    _add_run(tid, "worker-a")
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "comment", "reviewer": "human-reviewer",
    })
    assert r.status_code == 200, r.text
    rev = client.get(f"/api/v1/tasks/{tid}/reviews").json()[-1]
    assert rev["reviewer"] == "human-reviewer"
    assert rev["reviewer_verified"] is False
    assert rev["verified_run_id"] is None


# ── 迁移幂等 ─────────────────────────────────────────────────────────────────

def test_migration_adds_review_identity_columns(tmp_path, monkeypatch):
    from sqlalchemy import inspect as sa_inspect, text
    from sqlmodel import SQLModel
    from mio_taskhub.migrations import run_migrations, _migrate_task_review_identity

    db_path = tmp_path / "mig_review.db"
    url = f"sqlite:///{db_path}"
    from sqlalchemy import create_engine as sa_create
    eng = sa_create(url)
    SQLModel.metadata.create_all(eng)  # 新库已含新列
    run_migrations(eng)  # 重复跑一遍，必须幂等
    cols = {c["name"] for c in sa_inspect(eng).get_columns("taskreview")}
    assert {"reviewer_verified", "verified_run_id"} <= cols

    # 旧库（无新列）迁移补列，跑两遍验证幂等
    eng2 = sa_create(f"sqlite:///{tmp_path / 'mig_review_old.db'}")
    with eng2.connect() as conn:
        conn.execute(text(
            "CREATE TABLE taskreview (id VARCHAR(32) PRIMARY KEY, task_id VARCHAR(32), "
            "decision VARCHAR(16), summary TEXT, comments TEXT, reviewer VARCHAR(64))"))
    _migrate_task_review_identity(eng2.connect())
    _migrate_task_review_identity(eng2.connect())
    cols2 = {c["name"] for c in sa_inspect(eng2).get_columns("taskreview")}
    assert {"reviewer_verified", "verified_run_id"} <= cols2
