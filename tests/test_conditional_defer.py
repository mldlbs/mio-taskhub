# -*- coding: utf-8 -*-
"""T3.2/T3.3 条件性任务 defer 决策单测。"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "roadmap" / "t32-t33-defer.md"


def test_defer_doc_exists():
    assert DOC.exists()
    text = DOC.read_text(encoding="utf-8")
    assert "T3.2" in text and "T3.3" in text
    assert "defer" in text.lower()


def test_t32_precondition_v1_insufficient():
    text = DOC.read_text(encoding="utf-8")
    assert "V1" in text
    assert "证据不足" in text
    # defer 非 cancel
    assert "defer" in text and "cancel" in text


def test_t33_precondition_no_reviewer():
    text = DOC.read_text(encoding="utf-8")
    assert "外部" in text
    assert "0" in text
    assert "恢复条件" in text
