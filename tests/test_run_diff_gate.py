# -*- coding: utf-8 -*-
"""窄门禁测试：拦截「声明成功但零改动」的提交（任务 d468e633）。

对应 `docs/taskhub/d468e633/test.md` 的 TC-1 ~ TC-10。

| TC | 覆盖 FR | 场景 |
|----|---------|------|
| TC-1 | FR-1 FR-2 FR-7 | 零改动 + success → 422 run.empty_diff，run 状态/exit_code/attempt 均不变 |
| TC-2 | FR-1 FR-2 | 改了没 commit → 200，changed=true |
| TC-3 | FR-1 FR-2 | 改了并 commit → 200，changed=true |
| TC-4 | FR-3 | 非 git 目录 → 200，标注 collect_failed |
| TC-5 | FR-3 | diff_gate_exempt=true → 200，标注 task_exempt |
| TC-6 | FR-2 FR-3 FR-10 FR-11 | workspace 空 / task_kind=idea_review → 200；runs[] 含 3 个新字段 |
| TC-7 | FR-4 | 连续被拒达上限 → run 判失败 |
| TC-8 | FR-5 | 拦截后查库确认 Event + HistoryEvent 都有记录 |
| TC-9 | FR-6 FR-9 | warn 档 → 200 + warn 标注（含「不影响放行」），不产生 toast 事件 |
| TC-10 | FR-8 | off / warn / block 三档生效 |
"""
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from sqlalchemy import delete

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import (
    Event, HistoryEvent, Run, RunState, Task, TaskKind, TaskState, TaskStage,
)

client = TestClient(app)


# ── 临时 git 仓库辅助 ──────────────────────────────────────────────

def _git(ws, *args):
    return subprocess.run(
        ["git", *args], cwd=ws, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _make_repo(tmp_path):
    """建一个带一次 commit 的 git 仓库。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(str(ws), "init")
    _git(str(ws), "config", "user.email", "t@t.com")
    _git(str(ws), "config", "user.name", "t")
    (ws / "README.md").write_text("base\n", encoding="utf-8")
    _git(str(ws), "add", ".")
    _git(str(ws), "commit", "-m", "init")
    return ws


def _mk_task(tid, workspace="", *, task_kind=TaskKind.NORMAL,
             diff_gate_exempt=False, state=TaskState.RUNNING):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as s:
        s.add(Task(
            id=tid, title=f"p12-{tid}", state=state, stage=TaskStage.REVIEW,
            priority=0, schedule_type="once", max_retries=3, attempt=1,
            retry_count=0, acceptance_criteria="", project="p12",
            workspace=workspace, labels=[], files=[], deliverables=[],
            task_kind=task_kind, diff_gate_exempt=diff_gate_exempt,
            created_at=now,
        ))
        s.commit()


def _mk_run(rid, tid, *, agent="worker-a", state=RunState.RUNNING, with_before=None):
    """建一个 run；with_before 为 None 时不拍前快照（模拟「run 创建时未采集」）。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as s:
        r = Run(id=rid, task_id=tid, agent_name=agent, state=state,
                attempt=1, progress=50, started_at=now, last_heartbeat=now)
        if with_before is not None:
            r.diff_summary = {"before": with_before}
        s.add(r)
        s.commit()


def _cleanup(*ids):
    with Session(engine) as s:
        for tid in ids:
            s.exec(delete(HistoryEvent).where(HistoryEvent.task_id == tid))
            s.exec(delete(Event).where(Event.entity_id == tid))
            s.exec(delete(Run).where(Run.task_id == tid))
            s.exec(delete(Task).where(Task.id == tid))
        s.commit()


def _before_of(ws):
    """跑前快照（与 diff_gate.snapshot 同一实现）。"""
    from mio_taskhub.diff_gate import snapshot
    return snapshot(str(ws)).brief()


# ── TC-1：零改动 + success → 422，run 状态不变 ──────────────────────

def test_tc1_empty_diff_blocked(tmp_path, monkeypatch):
    """TC-1：零改动声明 success → 422 run.empty_diff。

    关键断言（不止状态码）：
      - detail.code == run.empty_diff
      - detail 含 message/hint/files_changed/rejections/max_rejections
      - **run 状态不变**（仍 running）、**无 exit_code**、**attempt 未增**
    """
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    ws = _make_repo(tmp_path)
    _mk_task("tc1", workspace=str(ws))
    _mk_run("tc1r", "tc1", with_before=_before_of(ws))
    try:
        r = client.post("/api/v1/runs/tc1r/result",
                        json={"success": True, "result": "我干完了"})
        assert r.status_code == 422, r.text
        d = r.json()["detail"]
        assert d["code"] == "run.empty_diff"
        assert d["files_changed"] == 0
        assert d["rejections"] == 1
        assert d["max_rejections"] == 2
        assert "message" in d and "hint" in d
        # 不复用通用 422 文案
        assert "数据校验失败" not in d["message"]

        with Session(engine) as s:
            run = s.get(Run, "tc1r")
            assert run.state == RunState.RUNNING, "拒绝不得改变 run 状态"
            assert run.exit_code is None, "拒绝不得写 exit_code"
            assert run.finished_at is None, "拒绝不得写 finished_at"
            task = s.get(Task, "tc1")
            assert task.state == TaskState.RUNNING, "拒绝不得触发任务状态流转"
            assert task.attempt == 1, "拒绝不得消耗 attempt"
    finally:
        _cleanup("tc1")


# ── TC-2：改了没 commit → 放行 ────────────────────────────────────

def test_tc2_modified_not_committed_allowed(tmp_path, monkeypatch):
    """TC-2：改了但没提交 → 200，changed=true，reason=has_changes。

    只比 HEAD 会漏这一种 —— 所以必须双快照。
    """
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    ws = _make_repo(tmp_path)
    _mk_task("tc2", workspace=str(ws))
    _mk_run("tc2r", "tc2", with_before=_before_of(ws))
    try:
        (ws / "src.py").write_text("print('x')\n", encoding="utf-8")
        r = client.post("/api/v1/runs/tc2r/result",
                        json={"success": True, "result": "改好了"})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            run = s.get(Run, "tc2r")
            assert run.diff_summary["changed"] is True
            assert run.diff_summary["reason"] == "has_changes"
    finally:
        _cleanup("tc2")


# ── TC-3：改了并 commit → 放行 ────────────────────────────────────

def test_tc3_committed_allowed(tmp_path, monkeypatch):
    """TC-3：改动已 commit → 200，HEAD 变化路径。"""
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    ws = _make_repo(tmp_path)
    _mk_task("tc3", workspace=str(ws))
    _mk_run("tc3r", "tc3", with_before=_before_of(ws))
    try:
        (ws / "src.py").write_text("print('y')\n", encoding="utf-8")
        _git(str(ws), "add", ".")
        _git(str(ws), "commit", "-m", "work")
        r = client.post("/api/v1/runs/tc3r/result",
                        json={"success": True, "result": "改好并提交"})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            run = s.get(Run, "tc3r")
            assert run.diff_summary["changed"] is True
    finally:
        _cleanup("tc3")


# ── TC-4：非 git 目录 → fail-open 放行 ────────────────────────────

def test_tc4_non_git_workspace_allowed(tmp_path, monkeypatch):
    """TC-4：workspace 非 git 目录 → 200，标注 collect_failed，不拦。

    采集失败**绝不**导致拦截 —— fail-open 优先。
    """
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    plain = tmp_path / "plain"
    plain.mkdir()
    _mk_task("tc4", workspace=str(plain))
    _mk_run("tc4r", "tc4", with_before=None)
    try:
        r = client.post("/api/v1/runs/tc4r/result",
                        json={"success": True, "result": "做完了"})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            run = s.get(Run, "tc4r")
            anns = run.annotations or []
            codes = [a.get("code") for a in anns]
            assert "collect_failed" in codes, f"标注应写明跳过原因，实际 {codes}"
    finally:
        _cleanup("tc4")


# ── TC-5：显式豁免 → 放行 ──────────────────────────────────────────

def test_tc5_task_exempt_allowed(tmp_path, monkeypatch):
    """TC-5：diff_gate_exempt=true → 200，标注 task_exempt。"""
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    ws = _make_repo(tmp_path)
    _mk_task("tc5", workspace=str(ws), diff_gate_exempt=True)
    _mk_run("tc5r", "tc5", with_before=_before_of(ws))
    try:
        r = client.post("/api/v1/runs/tc5r/result",
                        json={"success": True, "result": "无代码产出"})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            run = s.get(Run, "tc5r")
            codes = [a.get("code") for a in (run.annotations or [])]
            assert "task_exempt" in codes, f"实际 {codes}"
    finally:
        _cleanup("tc5")


# ── TC-6：workspace 空 / task_kind 豁免 / runs[] 新字段 ─────────────

def test_tc6_no_workspace_allowed(monkeypatch):
    """TC-6a：workspace 为空 → 200，标注 no_workspace。"""
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    _mk_task("tc6a", workspace="")
    _mk_run("tc6ar", "tc6a", with_before=None)
    try:
        r = client.post("/api/v1/runs/tc6ar/result",
                        json={"success": True, "result": "无仓库任务"})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            run = s.get(Run, "tc6ar")
            codes = [a.get("code") for a in (run.annotations or [])]
            assert "no_workspace" in codes, f"实际 {codes}"
    finally:
        _cleanup("tc6a")


def test_tc6_task_kind_exempt_allowed(tmp_path, monkeypatch):
    """TC-6b：task_kind=idea_review → 200，标注 task_kind_exempt。"""
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    ws = _make_repo(tmp_path)
    _mk_task("tc6b", workspace=str(ws), task_kind=TaskKind.REVIEW)
    _mk_run("tc6br", "tc6b", with_before=_before_of(ws))
    try:
        r = client.post("/api/v1/runs/tc6br/result",
                        json={"success": True, "result": "评审完成"})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            run = s.get(Run, "tc6br")
            codes = [a.get("code") for a in (run.annotations or [])]
            assert "task_kind_exempt" in codes, f"实际 {codes}"
    finally:
        _cleanup("tc6b")


def test_tc6_task_detail_has_new_fields():
    """TC-6c：GET /tasks/{id} 的 runs[] 含 diff_summary/annotations/rejections 三字段。"""
    _mk_task("tc6c", workspace="")
    _mk_run("tc6cr", "tc6c", with_before=None)
    try:
        d = client.get("/api/v1/tasks/tc6c").json()
        assert "diff_gate_exempt" in d
        runs = d["runs"]
        assert runs, "runs 不应为空"
        r0 = runs[0]
        for k in ("diff_summary", "annotations", "rejections"):
            assert k in r0, f"runs[] 缺字段 {k}"
        assert isinstance(r0["annotations"], list)
        assert isinstance(r0["rejections"], int)
    finally:
        _cleanup("tc6c")


# ── TC-7：连续被拒达上限 → run 判失败 ─────────────────────────────

def test_tc7_rejection_limit_reaches_failure(tmp_path, monkeypatch):
    """TC-7：连续被拒 2 次后，第 3 次达上限 → run 判失败。

    前 2 次 422（不消耗 attempt），第 3 次 run 转 failed（exit_code=1）。
    """
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    ws = _make_repo(tmp_path)
    _mk_task("tc7", workspace=str(ws))
    _mk_run("tc7r", "tc7", with_before=_before_of(ws))
    try:
        for i in (1, 2):
            r = client.post("/api/v1/runs/tc7r/result",
                            json={"success": True, "result": f"第{i}次"})
            assert r.status_code == 422, f"第{i}次应 422，实际 {r.status_code}"
            assert r.json()["detail"]["rejections"] == i

        # 第 3 次：达上限 → 不再 422（已不是「请重试」，而是「本次执行失败」）
        r3 = client.post("/api/v1/runs/tc7r/result",
                         json={"success": True, "result": "第3次"})
        assert r3.status_code == 409, f"达上限应 409，实际 {r3.status_code}"
        assert r3.json()["detail"]["code"] == "run.empty_diff_repeated"
        with Session(engine) as s:
            run = s.get(Run, "tc7r")
            assert run.state == RunState.FINISHED
            assert run.exit_code == 1
            # 第 3 次被拒时 rejections 累计到 3（判定在自增之后执行）
            assert run.rejections == 3
    finally:
        _cleanup("tc7")


# ── TC-8：拦截后查库确认可见性三链路 ──────────────────────────────

def test_tc8_rejection_visible_in_db(tmp_path, monkeypatch):
    """TC-8：拦截后 Event + HistoryEvent 两表都有记录（查库确认，不看界面）。

    验证「先 commit 再抛」的时序 —— 否则 get_session 退出不 commit，
    拦截事件随会话丢弃，人永远看不到。
    """
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    ws = _make_repo(tmp_path)
    _mk_task("tc8", workspace=str(ws))
    _mk_run("tc8r", "tc8", with_before=_before_of(ws))
    try:
        r = client.post("/api/v1/runs/tc8r/result",
                        json={"success": True, "result": "什么都没干"})
        assert r.status_code == 422

        with Session(engine) as s:
            # ① WS 事件：type=run.result_rejected，entity 必须是 task
            evs = s.exec(select(Event).where(
                Event.entity_id == "tc8",
                Event.type == "run.result_rejected",
            )).all()
            assert evs, "Event 表应有 run.result_rejected 记录"
            assert evs[-1].entity == "task", "entity 必须是 task，否则前端不弹 toast"
            assert evs[-1].run_id == "tc8r"

            # ② 执行历史：HistoryEvent type=rejected
            hs = s.exec(select(HistoryEvent).where(
                HistoryEvent.task_id == "tc8",
                HistoryEvent.type == "rejected",
            )).all()
            assert hs, "HistoryEvent 表应有 type=rejected 记录"
            assert "零改动" in (hs[-1].payload or "")

            # ③ run 标注：annotations 含 level=block
            run = s.get(Run, "tc8r")
            anns = run.annotations or []
            assert any(a.get("code") == "run.empty_diff"
                       and a.get("level") == "block" for a in anns), f"实际 {anns}"
    finally:
        _cleanup("tc8")


# ── TC-9：warn 档只标注不拦 ────────────────────────────────────────

def test_tc9_warn_mode_only_annotates(tmp_path, monkeypatch):
    """TC-9：warn 档 → 200，warn 标注含「不影响放行」，**不产生** toast 事件。

    标注项只提示不拦、不弹 toast（FR-6）。
    """
    monkeypatch.setenv("MIO_DIFF_GATE", "warn")
    ws = _make_repo(tmp_path)
    _mk_task("tc9", workspace=str(ws))
    _mk_run("tc9r", "tc9", with_before=_before_of(ws))
    try:
        r = client.post("/api/v1/runs/tc9r/result",
                        json={"success": True, "result": "可能没改"})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            run = s.get(Run, "tc9r")
            anns = run.annotations or []
            hit = [a for a in anns if a.get("code") == "empty_diff_warn"]
            assert hit, f"应有 empty_diff_warn 标注，实际 {anns}"
            assert hit[0]["level"] == "warn"
            assert "不影响放行" in hit[0]["message"], \
                f"文案须含「不影响放行」，实际 {hit[0]['message']!r}"
            # 不产生 toast 事件
            evs = s.exec(select(Event).where(
                Event.entity_id == "tc9",
                Event.type == "run.result_rejected",
            )).all()
            assert not evs, "warn 档不应产生拦截事件"
    finally:
        _cleanup("tc9")


# ── TC-10：off / warn / block 三档 ────────────────────────────────

def test_tc10_gate_modes(tmp_path, monkeypatch):
    """TC-10：off → 200 无标注；warn → 200 有标注；block → 422。"""
    ws = _make_repo(tmp_path)

    # off
    monkeypatch.setenv("MIO_DIFF_GATE", "off")
    _mk_task("tc10a", workspace=str(ws))
    _mk_run("tc10ar", "tc10a", with_before=_before_of(ws))
    try:
        r = client.post("/api/v1/runs/tc10ar/result", json={"success": True})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            run = s.get(Run, "tc10ar")
            codes = [a.get("code") for a in (run.annotations or [])]
            assert "gate_off" not in codes or True   # off 档不产生标注
            assert not codes, f"off 档不应产生标注，实际 {codes}"
    finally:
        _cleanup("tc10a")

    # warn
    monkeypatch.setenv("MIO_DIFF_GATE", "warn")
    _mk_task("tc10b", workspace=str(ws))
    _mk_run("tc10br", "tc10b", with_before=_before_of(ws))
    try:
        r = client.post("/api/v1/runs/tc10br/result", json={"success": True})
        assert r.status_code == 200
        with Session(engine) as s:
            run = s.get(Run, "tc10br")
            codes = [a.get("code") for a in (run.annotations or [])]
            assert "empty_diff_warn" in codes, f"实际 {codes}"
    finally:
        _cleanup("tc10b")

    # block
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    _mk_task("tc10c", workspace=str(ws))
    _mk_run("tc10cr", "tc10c", with_before=_before_of(ws))
    try:
        r = client.post("/api/v1/runs/tc10cr/result", json={"success": True})
        assert r.status_code == 422, r.text
    finally:
        _cleanup("tc10c")


# ── 单元：diff_gate 纯函数 ─────────────────────────────────────────

def test_differs_semantics():
    """differs：采不到信息一律视为「有改动」（fail-open 方向）。"""
    from mio_taskhub.diff_gate import Snapshot, differs
    ok = Snapshot(ok=True, head="a", porcelain="")
    bad = Snapshot(ok=False, error="x")
    assert differs(ok, ok) is False
    assert differs(ok, Snapshot(ok=True, head="b", porcelain="")) is True
    assert differs(ok, Snapshot(ok=True, head="a", porcelain=" M x")) is True
    assert differs(ok, Snapshot(ok=True, head="a", porcelain="", insertions=1)) is True
    # 采集失败 → 视为有改动（不拦）
    assert differs(bad, ok) is True
    assert differs(ok, bad) is True


def test_merge_annotations_dedupes_by_code():
    """merge_annotations：同 code 只保留最新一条。"""
    from mio_taskhub.diff_gate import merge_annotations
    a1 = [{"code": "x", "message": "old", "level": "warn"}]
    a2 = merge_annotations(a1, [{"code": "x", "message": "new", "level": "warn"}])
    assert len(a2) == 1
    assert a2[0]["message"] == "new"
    a3 = merge_annotations(a1, [{"code": "y", "message": "other", "level": "block"}])
    assert len(a3) == 2


def test_gate_mode_fallback_to_warn(monkeypatch):
    """MIO_DIFF_GATE 未设或非法值 → 按 warn 处理。"""
    from mio_taskhub.diff_gate import gate_mode
    monkeypatch.delenv("MIO_DIFF_GATE", raising=False)
    assert gate_mode() == "warn"
    monkeypatch.setenv("MIO_DIFF_GATE", "bogus")
    assert gate_mode() == "warn"
    monkeypatch.setenv("MIO_DIFF_GATE", "BLOCK")
    assert gate_mode() == "block"


def test_snapshot_on_non_git_dir(tmp_path):
    """snapshot：非 git 目录 → ok=False + error，不抛异常。"""
    from mio_taskhub.diff_gate import snapshot
    plain = tmp_path / "p"
    plain.mkdir()
    s = snapshot(str(plain))
    assert s.ok is False
    assert s.error
    # 空路径
    s2 = snapshot("")
    assert s2.ok is False


# ── 只读自查端点 ───────────────────────────────────────────────────

def test_diff_evidence_endpoint(tmp_path, monkeypatch):
    """GET /runs/{id}/diff-evidence：只读返回判定，供 Agent 提交前自查。"""
    monkeypatch.setenv("MIO_DIFF_GATE", "block")
    ws = _make_repo(tmp_path)
    _mk_task("dgev", workspace=str(ws))
    _mk_run("dgevr", "dgev", with_before=_before_of(ws))
    try:
        r = client.get("/api/v1/runs/dgevr/diff-evidence")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["run_id"] == "dgevr"
        assert d["task_id"] == "dgev"
        assert d["mode"] == "block"
        assert d["would_block"] is True, "零改动时 should_block"
        assert d["reason"] == "empty_diff"
        assert d["before"] is not None
        # 只读：不改 run 任何字段
        with Session(engine) as s:
            run = s.get(Run, "dgevr")
            assert run.state == RunState.RUNNING
            assert run.rejections == 0
            assert (run.annotations or []) == []
    finally:
        _cleanup("dgev")


def test_diff_evidence_404():
    assert client.get("/api/v1/runs/nope/diff-evidence").status_code == 404
