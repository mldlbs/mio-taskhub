# -*- coding: utf-8 -*-
"""评审门控测试（2026-10-10 审计 P0-4）。

修复的两个缺口：
1. 评审人身份隔离 —— 原先 `reviewer` 是自由字符串，执行该任务的 agent 可以自己
   approve 自己，「独立验收」名存实亡。现默认拒绝自审，`self_review=true` 可显式放行。
2. approve 结论内容下限 —— 原先 `summary` 是自由字符串，写 "x" 也会写入
   `review_result` 并满足 done 门控。现未登记 review 文档时长度不足即 422。
"""
from fastapi.testclient import TestClient
from sqlmodel import Session
import uuid

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage, Run, RunState

client = TestClient(app)

GOOD_SUMMARY = "已核对变更范围与测试结果，无阻断问题"


def _mk(stage="review"):
    with Session(engine) as s:
        t = Task(title="review-target", stage=TaskStage(stage))
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _add_run(tid, agent):
    with Session(engine) as s:
        r = Run(id=f"run-{uuid.uuid4().hex[:8]}", task_id=tid,
                agent_name=agent, state=RunState.FINISHED)
        s.add(r)
        s.commit()


# ── 身份隔离 ──────────────────────────────────────────────────────────────

def test_executor_cannot_approve_own_work():
    tid = _mk()
    _add_run(tid, "worker-a")
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "reviewer": "worker-a", "summary": GOOD_SUMMARY,
    })
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "review.self_approval"
    # 不得写入 review_result（否则等于放行）
    assert client.get(f"/api/v1/tasks/{tid}").json()["review_result"] == ""


def test_other_reviewer_can_approve():
    tid = _mk()
    _add_run(tid, "worker-a")
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "reviewer": "worker-b", "summary": GOOD_SUMMARY,
    })
    assert r.status_code == 200
    t = client.get(f"/api/v1/tasks/{tid}").json()
    assert t["review_result"] == GOOD_SUMMARY


def test_self_review_allowed_with_explicit_flag():
    """确有需要时显式声明自审——放行，但记录里 reviewer 仍是真实执行者，可审计。"""
    tid = _mk()
    _add_run(tid, "worker-a")
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "reviewer": "worker-a",
        "summary": GOOD_SUMMARY, "self_review": True,
    })
    assert r.status_code == 200
    revs = client.get(f"/api/v1/tasks/{tid}/reviews").json()
    assert revs[-1]["reviewer"] == "worker-a"


def test_reject_by_executor_is_allowed():
    """自审阻断只针对 approve —— 自己发现自己做得不行，应当允许。"""
    tid = _mk()
    _add_run(tid, "worker-a")
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "reject", "reviewer": "worker-a", "summary": "自评不通过",
    })
    assert r.status_code == 200


# ── 结论内容下限 ──────────────────────────────────────────────────────────

def test_approve_with_trivial_summary_rejected():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "reviewer": "human", "summary": "x",
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "review.summary_too_short"
    assert client.get(f"/api/v1/tasks/{tid}").json()["review_result"] == ""


def test_approve_ok_when_review_document_registered():
    """已登记 review 文档时，文本结论可以短——文档本身就是审查证据。"""
    tid = _mk()
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.doc_paths = {**(t.doc_paths or {}), "review": "docs/review.md"}
        s.add(t)
        s.commit()
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "reviewer": "human", "summary": "见文档",
    })
    assert r.status_code == 200
