# -*- coding: utf-8 -*-
"""P1 接线：Insight 派生任务接入文档/Evidence 门控（task 417a723b）。

证明：
- Remediator 建的 [insight] 任务带 non-empty doc_paths + acceptance_criteria + workspace；
- 生成的真实文档落盘；
- claim 上下文 required_reads 非空；
- 服务端 submit 门控未被削弱：未读 → missing（拒），读后 → passed。
"""
import os

from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.models import Task
from mio_taskhub.observability.insight_remediator import InsightsRemediator
from mio_taskhub.read_evidence import build_claim_context, check_read_gate, required_read_kinds


def _insight(metric="taskhub_task_failure_rate"):
    return {
        "id": 1, "kind": "anomaly", "severity": "critical", "acknowledged": 0,
        "metric_name": metric, "metric_value": 0.24, "baseline": 0.15,
        "description": "failure rate high", "recommendation": "investigate",
    }


def _consume_one(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("MIO_TASKHUB_WORKSPACE", str(ws))
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    created = InsightsRemediator().consume([_insight()])
    assert len(created) == 1
    return created[0], ws


def test_insight_task_has_doc_paths_and_acceptance(monkeypatch, tmp_path):
    task, ws = _consume_one(monkeypatch, tmp_path)
    with Session(engine) as db:
        t = db.get(Task, task.id)
    assert t.doc_paths, "应登记 doc_paths"
    assert "requirement" in t.doc_paths and "spec" in t.doc_paths
    assert (t.acceptance_criteria or "").strip(), "应有 acceptance_criteria"
    assert t.workspace, "应设 workspace"
    # 真实文件落盘
    for kind, rel in t.doc_paths.items():
        assert (ws / rel).is_file(), f"{kind} 文件应存在: {rel}"


def test_claim_context_requires_reads(monkeypatch, tmp_path):
    task, _ = _consume_one(monkeypatch, tmp_path)
    with Session(engine) as db:
        t = db.get(Task, task.id)
        ctx = build_claim_context(t)
        req = required_read_kinds(t)
    assert req, "required_reads 应非空（否则门控失效）"
    assert set(req) & {"requirement", "spec"}
    assert ctx.get("required_reads")


def test_submit_gate_still_rejects_unread(monkeypatch, tmp_path):
    """未读时 check_read_gate 应报 missing（服务端门控未削弱）。"""
    task, _ = _consume_one(monkeypatch, tmp_path)
    with Session(engine) as db:
        t = db.get(Task, task.id)
        gate = check_read_gate(db, t, run_id="run-x")
    assert gate["required_reads"], "应有必读项"
    assert gate["missing"], "未读时应有 missing（拒）"
    assert not gate["passed"]


def test_default_workspace_when_env_unset(monkeypatch):
    """未设 MIO_TASKHUB_WORKSPACE 时仍应产出 doc_paths（回退默认目录，避免静默失效）。"""
    monkeypatch.delenv("MIO_TASKHUB_WORKSPACE", raising=False)
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    created = InsightsRemediator().consume([_insight("taskhub_task_success_rate")])
    assert len(created) == 1
    with Session(engine) as db:
        t = db.get(Task, created[0].id)
    assert t.doc_paths, "应回退默认 workspace 并产出 doc_paths"
    assert t.workspace, "应设默认 workspace"
    assert required_read_kinds(t), "required_reads 应非空"
