# -*- coding: utf-8 -*-
"""窄门禁灰度观测端点测试（任务 d468e633 的配套观测）。

对应 `docs/taskhub/diff-gate-grayscale-log.md` 的指标 M1/M3/M5。
端点是**纯只读统计**，不参与任何流程判定 —— 与 `_delivery_quality` 同性质。
"""
import os
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from sqlalchemy import delete

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Run, RunState, Task, TaskState, TaskStage

client = TestClient(app)


def _mk(tid, rid, *, diff_summary=None, rejections=0, exit_code=None,
        state=RunState.FINISHED, agent="probe"):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as s:
        s.add(Task(
            id=tid, title=f"dg-{tid}", state=TaskState.COMPLETED, stage=TaskStage.REVIEW,
            priority=0, schedule_type="once", max_retries=3, attempt=1, retry_count=0,
            acceptance_criteria="", project="dg", workspace="", labels=[],
            files=[], deliverables=[], created_at=now,
        ))
        s.add(Run(
            id=rid, task_id=tid, agent_name=agent, state=state,
            attempt=1, progress=100, started_at=now, finished_at=now,
            exit_code=exit_code, diff_summary=diff_summary,
            annotations=([{"code": "run.empty_diff", "level": "block",
                           "message": "零改动", "at": now.isoformat()}]
                         if rejections > 0 else []),
            rejections=rejections,
        ))
        s.commit()


def _cleanup(*ids):
    with Session(engine) as s:
        for tid in ids:
            s.exec(delete(Run).where(Run.task_id == tid))
            s.exec(delete(Task).where(Task.id == tid))
        s.commit()


def test_diff_gate_stats_empty_is_safe():
    """无任何 run 记录时端点仍返回 200（不因空数据报错）。"""
    r = client.get("/api/v1/board/diff-gate-stats")
    assert r.status_code == 200, r.text
    d = r.json()
    for k in ("mode", "scanned_runs", "by_reason", "blocked_run_count",
              "warned_run_count", "skipped_run_count",
              "suspected_false_positive_count", "note"):
        assert k in d, f"缺字段 {k}"


def test_diff_gate_stats_counts_reasons(monkeypatch):
    """M5：判定原因分布按 reason 聚合。"""
    monkeypatch.setenv("MIO_DIFF_GATE", "warn")
    _mk("dgs1", "dgs1r", diff_summary={"reason": "has_changes", "changed": True})
    _mk("dgs2", "dgs2r", diff_summary={"reason": "empty_diff_warn", "changed": False})
    _mk("dgs3", "dgs3r", diff_summary={"reason": "no_workspace"})
    _mk("dgs4", "dgs4r", diff_summary=None)   # 未判定
    try:
        r = client.get("/api/v1/board/diff-gate-stats")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mode"] == "warn"
        assert d["by_reason"].get("has_changes") == 1
        assert d["by_reason"].get("empty_diff_warn") == 1
        assert d["by_reason"].get("no_workspace") == 1
        assert d["warned_run_count"] >= 1
        assert d["skipped_run_count"] >= 1
        assert d["unjudged_runs"] >= 1
    finally:
        _cleanup("dgs1", "dgs2", "dgs3", "dgs4")


def test_diff_gate_stats_lists_blocked_runs():
    """M1：被拦的 run 进 blocked_runs 清单，带回看用的上下文。"""
    _mk("dgs5", "dgs5r", diff_summary={"reason": "empty_diff", "changed": False},
        rejections=2, exit_code=1, state=RunState.FINISHED)
    try:
        d = client.get("/api/v1/board/diff-gate-stats").json()
        assert d["blocked_run_count"] >= 1
        hit = next(b for b in d["blocked_runs"] if b["run_id"] == "dgs5r")
        assert hit["rejections"] == 2
        assert hit["exit_code"] == 1
        assert hit["annotation"]["level"] == "block"
        assert hit["annotation"]["code"] == "run.empty_diff"
    finally:
        _cleanup("dgs5")


def test_diff_gate_stats_flags_suspected_false_positive():
    """M3 疑似误拒信号：被拒过但最终 exit_code=0（后来真做完了）。"""
    _mk("dgs6", "dgs6r", diff_summary={"reason": "empty_diff", "changed": False},
        rejections=1, exit_code=0)
    try:
        d = client.get("/api/v1/board/diff-gate-stats").json()
        assert d["suspected_false_positive_count"] >= 1
        assert any(x["run_id"] == "dgs6r" for x in d["suspected_false_positive"])
    finally:
        _cleanup("dgs6")


def test_diff_gate_stats_readonly():
    """端点是纯只读：调用前后 run 记录无任何变化。"""
    _mk("dgs7", "dgs7r", diff_summary={"reason": "has_changes", "changed": True})
    try:
        with Session(engine) as s:
            before = s.get(Run, "dgs7r")
            snap = (before.diff_summary, before.rejections, before.exit_code)
        client.get("/api/v1/board/diff-gate-stats")
        with Session(engine) as s:
            after = s.get(Run, "dgs7r")
            assert (after.diff_summary, after.rejections, after.exit_code) == snap
    finally:
        _cleanup("dgs7")
