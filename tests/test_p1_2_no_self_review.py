# -*- coding: utf-8 -*-
"""P1-2：禁自审通道 —— 同一执行者不得 approve 自己的任务。

对应决策文档 `product-decision-20261020.md` §4 P1-2：
「删 self_review 通道 / 禁空 reviewer；同一 agent 自审恒 409，无绕过」

覆盖 6 条实测确认的绕过路径 + 4 条合法路径：
  TC-1  reviewer 传空 → 409 review.reviewer_required
  TC-2  不传 reviewer 字段 → 409 review.reviewer_required
  TC-3  self_review=true → 409 review.self_approval
  TC-4  advance + review_result 文本直达 done → 422 review.evidence_missing
  TC-5  move + review_result 文本直达 done → 422 review.evidence_missing
  TC-6  只登记 review 路径（文件不存在）→ 422 review.evidence_missing
  TC-7  他人 approve → 放行
  TC-8  纯人工任务（无执行者）→ 放行
  TC-9  reject / comment 不受自审限制
  TC-10 force=true 仍可跳过（留痕）
  TC-11 拦截后不留 TaskReview 假记录
"""
import os
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from sqlalchemy import delete

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from mio_taskhub.db import engine  # noqa: E402
from mio_taskhub.models import Task, TaskEvent, TaskReview, TaskState, TaskStage, Run, RunState  # noqa: E402

client = TestClient(__import__("mio_taskhub.main", fromlist=["app"]).app)


def _mk(tid, *, with_run=True, agent="opencode"):
    """建一个 COMPLETED/REVIEW 任务（submit_result 成功后的真实状态）。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as s:
        s.add(Task(
            id=tid, title=f"p12-{tid}", state=TaskState.COMPLETED, stage=TaskStage.REVIEW,
            priority=0, schedule_type="once", max_retries=3, attempt=1, retry_count=0,
            acceptance_criteria="", project="p12", workspace="",
            labels=[], files=[], deliverables=[], created_at=now,
        ))
        s.commit()
        if with_run:
            s.add(Run(
                id=f"{tid}r", task_id=tid, agent_name=agent, state=RunState.FINISHED,
                attempt=1, progress=100, result="done",
            ))
            s.commit()


def _reviews(tid):
    with Session(engine) as s:
        return s.exec(select(TaskReview).where(TaskReview.task_id == tid)).all()


def _cleanup(*ids):
    with Session(engine) as s:
        for tid in ids:
            s.exec(delete(TaskReview).where(TaskReview.task_id == tid))
            s.exec(delete(TaskEvent).where(TaskEvent.task_id == tid))
            s.exec(delete(Run).where(Run.task_id == tid))
            s.exec(delete(Task).where(Task.id == tid))
        s.commit()


# ── TC-1 / TC-2：空 reviewer ─────────────────────────────────────

def test_empty_reviewer_rejected():
    """TC-1：reviewer 传空 → 409 review.reviewer_required。

    修复前：`if reviewer and not body.get("self_review")` 短路，空 reviewer
    **完全跳过**自审检查 → 200 放行。
    """
    _mk("tc1")
    try:
        r = client.post("/api/v1/tasks/tc1/reviews", json={
            "decision": "approve", "reviewer": "", "summary": "空审阅人也想通过",
        })
        assert r.status_code == 409, f"应被拒，实际 {r.status_code}"
        assert r.json()["detail"]["code"] == "review.reviewer_required"
    finally:
        _cleanup("tc1")


def test_missing_reviewer_field_rejected():
    """TC-2：完全不传 reviewer 字段 → 同样 409。"""
    _mk("tc2")
    try:
        r = client.post("/api/v1/tasks/tc2/reviews", json={
            "decision": "approve", "summary": "没写审阅人也想通过",
        })
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "review.reviewer_required"
    finally:
        _cleanup("tc2")


# ── TC-3：self_review 绕过通道 ───────────────────────────────────

def test_self_review_bypass_channel_removed():
    """TC-3：`self_review=true` 不再是绕过通道 → 409 review.self_approval。"""
    _mk("tc3")
    try:
        r = client.post("/api/v1/tasks/tc3/reviews", json={
            "decision": "approve", "reviewer": "opencode",
            "summary": "显式声明自审也应该被拒", "self_review": True,
        })
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "review.self_approval"
        assert r.json()["detail"]["executor"] == "opencode"
    finally:
        _cleanup("tc3")


# ── TC-4 / TC-5：review_result 文本直达 done ──────────────────────

def test_advance_to_done_requires_review_evidence():
    """TC-4：advance + 纯文本 review_result 直达 done → 422 evidence_missing。

    修复前：200 放行且 **TaskReview 记录数 = 0** —— 完全不经过 reviews.py，
    自审检查根本不参与，approved 在 DB 里无痕。
    """
    _mk("tc4")
    try:
        r = client.post("/api/v1/tasks/tc4/stage", json={
            "target_stage": "done", "review_result": "我自己批个通过",
        })
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "review.evidence_missing"
        assert len(_reviews("tc4")) == 0
    finally:
        _cleanup("tc4")


def test_move_to_done_requires_review_evidence():
    """TC-5：move（拖拽路径）+ 纯文本 review_result → 同样 422。"""
    _mk("tc5")
    try:
        r = client.post("/api/v1/tasks/tc5/stage/move", json={
            "target_stage": "done", "review_result": "拖拽也能批个通过",
        })
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "review.evidence_missing"
        assert len(_reviews("tc5")) == 0
    finally:
        _cleanup("tc5")


# ── TC-6：只登记 review 路径但文件不存在 ──────────────────────────

def test_done_requires_review_doc_actually_written(tmp_path, monkeypatch):
    """TC-6：只登记 review 文档路径（文件根本没写）→ 422。

    修复前：review_result 被自动填成「见审查文档 xxx.md」—— 一条看起来像
    审查结论的**假记录**，解锁 done 门控，TaskReview 记录数 = 0。
    """
    _mk("tc6")
    try:
        r = client.post("/api/v1/tasks/tc6/stage/move", json={
            "target_stage": "done",
            "doc_paths": {"review": "docs/never-written.md"},
        })
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "review.evidence_missing"
        assert len(_reviews("tc6")) == 0
    finally:
        _cleanup("tc6")


# ── TC-7 / TC-8：合法路径不受影响 ────────────────────────────────

def test_other_reviewer_approve_allowed():
    """TC-7：他人 approve → 放行，且能进 done。"""
    _mk("tc7")
    try:
        r1 = client.post("/api/v1/tasks/tc7/reviews", json={
            "decision": "approve", "reviewer": "someone-else",
            "summary": "他人独立审阅，验收通过",
        })
        assert r1.status_code == 200
        r2 = client.post("/api/v1/tasks/tc7/stage/move", json={
            "target_stage": "done", "review_result": "他人独立审阅，验收通过",
        })
        assert r2.status_code == 200
        assert r2.json()["stage"] == "done"
    finally:
        _cleanup("tc7")


def test_pure_human_task_allowed():
    """TC-8：任务无执行者记录（纯人工任务）→ 无自审概念，放行。

    fail-open 原则：不能因为「没有 agent 参与」就堵住人工流程。
    """
    _mk("tc8", with_run=False)
    try:
        r1 = client.post("/api/v1/tasks/tc8/reviews", json={
            "decision": "approve", "reviewer": "human-user",
            "summary": "人工独立验收通过，代码符合需求",
        })
        assert r1.status_code == 200
        r2 = client.post("/api/v1/tasks/tc8/stage/move", json={"target_stage": "done"})
        assert r2.status_code == 200
        assert r2.json()["stage"] == "done"
    finally:
        _cleanup("tc8")


# ── TC-9：reject / comment 不受自审限制 ──────────────────────────

@pytest.mark.parametrize("decision", ["reject", "comment"])
def test_reject_and_comment_not_blocked(decision):
    """TC-9：reject / comment 不构成「独立验收」主张，不应被拦。

    否则会出现「无法拒绝自己写的垃圾」这种荒谬约束。
    """
    _mk(f"tc9-{decision}")
    try:
        r = client.post(f"/api/v1/tasks/tc9-{decision}/reviews", json={
            "decision": decision, "reviewer": "opencode",
            "summary": "自己发现自己写错了，记录一下",
        })
        assert r.status_code == 200, f"{decision} 不该被拦，实际 {r.status_code}"
    finally:
        _cleanup(f"tc9-{decision}")


# ── TC-10：force 仍可跳过 ────────────────────────────────────────

def test_force_still_skips_gate():
    """TC-10：force=true 仍可跳过（留痕），与既有门控语义一致。"""
    _mk("tc10")
    try:
        r = client.post("/api/v1/tasks/tc10/stage/move", json={
            "target_stage": "done", "review_result": "紧急放行", "force": True,
        })
        assert r.status_code == 200
        assert r.json()["stage"] == "done"
    finally:
        _cleanup("tc10")


# ── TC-11：拦截后不留假记录 ──────────────────────────────────────

def test_blocked_submit_leaves_no_review_record():
    """TC-11：被拒的 approve 不得留下 TaskReview 记录（不产生假证据）。"""
    _mk("tc11")
    try:
        client.post("/api/v1/tasks/tc11/reviews", json={
            "decision": "approve", "reviewer": "opencode", "summary": "自审想通过",
        })
        assert len(_reviews("tc11")) == 0
    finally:
        _cleanup("tc11")


# ── review_guard 单元测试（判据本身） ───────────────────────────

def test_review_guard_last_executor_none_for_no_run():
    """无 run 记录 → last_executor 为 None（无自审概念）。"""
    from mio_taskhub.review_guard import last_executor
    _mk("tc12", with_run=False)
    try:
        with Session(engine) as s:
            assert last_executor(s, "tc12") is None
    finally:
        _cleanup("tc12")


def test_review_guard_is_self_approval():
    """is_self_approval：reviewer == 最后执行者 → True。"""
    from mio_taskhub.review_guard import is_self_approval
    _mk("tc13")
    try:
        with Session(engine) as s:
            assert is_self_approval(s, "tc13", "opencode") is True
            assert is_self_approval(s, "tc13", "other") is False
            assert is_self_approval(s, "tc13", "") is False
    finally:
        _cleanup("tc13")
