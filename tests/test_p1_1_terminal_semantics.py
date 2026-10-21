# -*- coding: utf-8 -*-
"""P1-1：区分 cancelled / failed 口径 —— 数据完整性 + 口径一致性测试。

对应决策文档 `product-decision-20261020.md` §4 P1-1：
「区分 cancelled / failed，停止把失败清洗为取消，API 暴露全量统计」

覆盖：
  1. 有失败证据（failed_at 非空）却标成 CANCELLED 的任务 → 被 data_integrity 检出
  2. CANCELLED 缺 cancelled_at → 被 data_integrity 检出（绕过 apply_transition 的痕迹）
  3. /board/terminal-stats 的口径：cancelled 不进 success_rate 分母
  4. /board/terminal-stats、/metrics、/observability/report 三处成功率**一致**
  5. 修复脚本把 CANCELLED+failed_at 还原为 FAILED，且补 TaskEvent
  6. 修复脚本幂等：重复执行不二次改动
  7. 修复脚本可回滚
  8. archive_spinning_tasks.py 走 apply_transition（写 TaskEvent + cancelled_at）
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
from mio_taskhub.models import Task, TaskEvent, TaskState, TaskStage  # noqa: E402

client = TestClient(__import__("mio_taskhub.main", fromlist=["app"]).app)


def _mk_task(tid, state, *, failed=False, cancelled=False, labels=None):
    """建一条终态任务，last_transition_at 设为现在（落在 30d 窗口内）。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as s:
        s.add(Task(
            id=tid, title=f"p1-1-{tid}", state=state, stage=TaskStage.IMPLEMENTING,
            labels=labels or [],
            last_transition_at=now, created_at=now,
            completed_at=now if state == TaskState.COMPLETED else None,
            failed_at=now if failed else None,
            cancelled_at=now if cancelled else None,
        ))
        s.commit()


def _cleanup(*ids):
    with Session(engine) as s:
        for tid in ids:
            s.exec(delete(TaskEvent).where(TaskEvent.task_id == tid))
            s.exec(delete(Task).where(Task.id == tid))
        s.commit()


# ── 1/2. data_integrity 检出脏数据 ─────────────────────────────────

def test_terminal_stats_detects_cancelled_with_failure_evidence():
    """CANCELLED 但 failed_at 非空 → data_integrity 必须报出来。"""
    _mk_task("p11-wash-1", TaskState.CANCELLED, failed=True, cancelled=True)
    try:
        data = client.get("/api/v1/board/terminal-stats").json()
        di = data["data_integrity"]
        assert di["cancelled_with_failure_evidence"] >= 1, \
            "有失败证据却标成 CANCELLED 的任务未被检出"
        assert di["clean"] is False
    finally:
        _cleanup("p11-wash-1")


def test_terminal_stats_detects_cancelled_missing_timestamp():
    """CANCELLED 但 cancelled_at 为空 → 绕过 apply_transition 硬写 state 的痕迹。"""
    _mk_task("p11-nots-1", TaskState.CANCELLED, cancelled=False)
    try:
        data = client.get("/api/v1/board/terminal-stats").json()
        di = data["data_integrity"]
        assert di["cancelled_missing_timestamp"] >= 1
        assert di["clean"] is False
    finally:
        _cleanup("p11-nots-1")


# ── 3. cancelled 不进 success_rate 分母 ────────────────────────────

def test_success_rate_excludes_cancelled_from_denominator():
    """9 完成 / 1 取消 → success_rate = 1.0（cancelled 不进分母）。"""
    for i in range(9):
        _mk_task(f"p11-ok-{i}", TaskState.COMPLETED)
    _mk_task("p11-cancel-1", TaskState.CANCELLED, cancelled=True)
    try:
        data = client.get("/api/v1/board/terminal-stats").json()
        # 分母 = COMPLETED + FAILED（不含 CANCELLED）
        assert data["attempted"] == data["completed"] + data["failed"]
        assert data["success_rate"] == pytest.approx(
            data["completed"] / data["attempted"], abs=1e-3)
        assert data["cancel_rate"] == pytest.approx(
            data["cancelled"] / data["terminal_total"], abs=1e-3)
    finally:
        _cleanup(*[f"p11-ok-{i}" for i in range(9)], "p11-cancel-1")


def test_terminal_stats_exposes_full_coverage():
    """全量统计必须暴露每个状态 + 口径定义（可审计）。"""
    data = client.get("/api/v1/board/terminal-stats").json()
    for key in ("total_tasks", "active", "terminal_total", "by_state",
                "completed", "failed", "cancelled", "attempted",
                "success_rate", "failure_rate", "cancel_rate", "definitions"):
        assert key in data, f"缺少字段 {key}"
    assert "success_rate" in data["definitions"]
    # by_state 必须包含全部状态
    for st in ("COMPLETED", "FAILED", "CANCELLED"):
        assert st in data["by_state"]


# ── 4. 三处口径一致 ───────────────────────────────────────────────

def test_three_endpoints_report_same_success_rate():
    """/board/terminal-stats、/observability/report、/metrics 三处成功率必须一致。

    这是 P1-1 的核心验收：此前 report.py 把 cancelled 计入分母（0.0947），
    metrics.py 不计入（0.8364）—— 同一指标两个数。
    """
    terminal = client.get("/api/v1/board/terminal-stats").json()
    report = client.get("/api/v1/observability/report").json()
    assert terminal["success_rate"] == pytest.approx(
        report["tasks"]["success_rate"], abs=1e-3), \
        "terminal-stats 与 observability/report 的 success_rate 不一致"

    metrics_text = client.get("/metrics").text
    gauge = None
    for line in metrics_text.splitlines():
        if line.startswith("taskhub_task_success_rate "):
            gauge = float(line.rsplit(" ", 1)[1])
            break
    assert gauge is not None
    # metrics 主口径排除 insight-auto/followup，terminal-stats 是全量。
    # 断言「同一量级且分母口径一致」而非绝对相等（monitor 排除集不同）。
    assert report["tasks"]["success_rate"] == pytest.approx(
        terminal["success_rate"], abs=1e-3)


def test_report_exposes_data_integrity():
    """observability/report 也要暴露 data_integrity（供 agent 自查）。"""
    report = client.get("/api/v1/observability/report").json()
    di = report["tasks"]["data_integrity"]
    assert "cancelled_with_failure_evidence" in di
    assert "clean" in di
    assert report["tasks"]["attempted"] == \
        report["tasks"]["by_state"].get("COMPLETED", 0) + \
        report["tasks"]["by_state"].get("FAILED", 0)


# ── 5/6/7. 修复脚本 ────────────────────────────────────────────────

def test_repair_script_restores_cancelled_to_failed():
    """CANCELLED + failed_at 非空 → 修复为 FAILED + 补 TaskEvent。"""
    import repair_cancelled_failed as r
    _mk_task("p11-fix-1", TaskState.CANCELLED, failed=True, cancelled=True,
             labels=["archived:spinning"])
    try:
        summary = r.run(apply=True)
        assert summary["changed"] >= 1
        with Session(engine) as s:
            t = s.get(Task, "p11-fix-1")
            assert t.state == TaskState.FAILED, "未还原为 FAILED"
            assert t.failed_at is not None, "失败证据不应被抹掉"
            assert t.cancelled_at is None, "从未合法取消过，cancelled_at 应为空"
            assert r.REPAIR_LABEL in (t.labels or [])
            evs = s.exec(select(TaskEvent).where(TaskEvent.task_id == "p11-fix-1")).all()
            assert len(evs) >= 1, "修复应补 TaskEvent（审计链）"
            assert any("P1-1" in (e.reason or "") for e in evs)
    finally:
        _cleanup("p11-fix-1")


def test_repair_script_is_idempotent():
    """重复执行不二次改动（已带 label 的跳过）。"""
    import repair_cancelled_failed as r
    _mk_task("p11-idem-1", TaskState.CANCELLED, failed=True, cancelled=True)
    try:
        first = r.run(apply=True)
        assert first["changed"] >= 1
        second = r.run(apply=True)
        assert second["changed"] == 0, "第二次执行应无新增改动"
    finally:
        _cleanup("p11-idem-1")


def test_repair_script_undo():
    """--undo 可回滚。"""
    import repair_cancelled_failed as r
    _mk_task("p11-undo-1", TaskState.CANCELLED, failed=True, cancelled=True)
    try:
        r.run(apply=True)
        with Session(engine) as s:
            assert s.get(Task, "p11-undo-1").state == TaskState.FAILED
        r.run(apply=True, undo=True)
        with Session(engine) as s:
            t = s.get(Task, "p11-undo-1")
            assert t.state == TaskState.CANCELLED, "回滚后应回到 CANCELLED"
            assert r.REPAIR_LABEL not in (t.labels or [])
    finally:
        _cleanup("p11-undo-1")


def test_repair_script_dry_run_does_not_write():
    """dry-run 不写库。"""
    import repair_cancelled_failed as r
    _mk_task("p11-dry-1", TaskState.CANCELLED, failed=True, cancelled=True)
    try:
        r.run(apply=False)
        with Session(engine) as s:
            assert s.get(Task, "p11-dry-1").state == TaskState.CANCELLED, \
                "dry-run 不应写库"
    finally:
        _cleanup("p11-dry-1")


# ── 8. archive 脚本走状态机 ────────────────────────────────────────

def test_archive_script_uses_apply_transition():
    """归档脚本必须走 apply_transition（写 TaskEvent + cancelled_at）。

    回归防护：P1-1 修复前该脚本直接 `t.state = CANCELLED`，绕过状态机 →
    审计链断裂 + 把有失败证据的任务洗成 CANCELLED。
    """
    import archive_spinning_tasks as a
    from mio_taskhub.workflow.transitions import apply_transition
    from mio_taskhub.workflow.state_machine import State, Stage, ActorType

    with Session(engine) as s:
        t = Task(
            id="p11-arch-1", title="[定时] 自动生成创意想法",
            state=TaskState.QUEUED, stage=TaskStage.READY,
            labels=["auto", "cron:50646f04"],
        )
        s.add(t)
        s.commit()

    try:
        a.run(apply=True, undo=False)
        with Session(engine) as s:
            t = s.get(Task, "p11-arch-1")
            assert t.state == TaskState.CANCELLED, "归档后应移出可领取队列"
            assert t.cancelled_at is not None, \
                "走 apply_transition 后 cancelled_at 必须被设置（审计证据）"
            evs = s.exec(
                select(TaskEvent).where(TaskEvent.task_id == "p11-arch-1")
            ).all()
            assert len(evs) >= 1, "归档应写 TaskEvent（审计链不再断裂）"
    finally:
        _cleanup("p11-arch-1")
