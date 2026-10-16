# -*- coding: utf-8 -*-
"""P1-OBS 重观测报告单测（8820176b）。"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "taskhub" / "p1-obs-reobservation-20261016.md"
AUDIT = REPO / "docs" / "taskhub" / "lifecycle-failure-audit-20261002.md"


def test_report_exists():
    assert DOC.exists()
    text = DOC.read_text(encoding="utf-8")
    assert "P1-OBS" in text
    assert "8820176b" in text


def test_references_source_audit():
    text = DOC.read_text(encoding="utf-8")
    assert "lifecycle-failure-audit-20261002" in text
    assert AUDIT.exists()


def test_answers_q2_misreclaim():
    """证实源审计 Q2『误回收』存疑。"""
    text = DOC.read_text(encoding="utf-8")
    assert "误回收" in text
    assert "307.4" in text or "margin" in text
    assert "360" in text  # T3.1 修复


def test_has_current_metrics():
    text = DOC.read_text(encoding="utf-8")
    assert "86.5%" in text
    assert "70.7%" in text
    assert "239" in text


def test_has_reobservation_plan():
    text = DOC.read_text(encoding="utf-8")
    assert "后续观测计划" in text
    assert "待验证" in text


def test_no_evidence_free_conclusion():
    """不做无证据结论：不得出现『已解决/生产级』。"""
    text = DOC.read_text(encoding="utf-8")
    for banned in ["已解决", "生产级", "完全修复"]:
        assert banned not in text, f"引入无证据结论: {banned}"
