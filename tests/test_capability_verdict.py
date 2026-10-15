# -*- coding: utf-8 -*-
"""T3.6 能力地图定档 + 技术债取舍单测。"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CAPMAP = REPO / "docs" / "taskhub" / "capability-map-20261001.md"
GAP = REPO / "docs" / "roadmap" / "02-current-state-audit.md"
POS = REPO / "docs" / "roadmap" / "01-positioning.md"


def test_capmap_has_full_decision_table():
    text = CAPMAP.read_text(encoding="utf-8")
    assert "逐项定档" in text
    # 三选一关键字
    assert "保留" in text and "延后" in text and "删除" in text
    # 无"尚未定义"
    assert "尚未定义" not in text


def test_capmap_all_items_have_verdict():
    text = CAPMAP.read_text(encoding="utf-8")
    section = text[text.find("逐项定档"):]
    # 至少覆盖活跃 + 3 争议 + 孤儿
    for item in ["task lifecycle", "evidence", "custom alertrule", "taskreview", "Plan", "outboxevent"]:
        assert item in section, f"能力地图缺项: {item}"


def test_tech_debt_has_decision_table():
    text = GAP.read_text(encoding="utf-8")
    assert "逐项取舍" in text
    # F8-F12 各有处置
    for f in ["F8", "F9", "F10", "F11", "F12"]:
        assert f in text
    assert "P0/P1" in text
    assert "登记（延后）" in text


def test_p0p1_all_cleared():
    """T3.6：P0/P1 债必须清零。"""
    text = GAP.read_text(encoding="utf-8")
    section = text[text.find("逐项取舍"):]
    # 每个 P0/P1 项状态应为"已修"
    assert "P0/P1 全部清零" in section or "P0/P1 全部清零" in text


def test_positioning_m5_no_undefined():
    text = POS.read_text(encoding="utf-8")
    # M5 行不得再含"尚未定义"或"仍留"
    for line in text.splitlines():
        if line.startswith("| M5 "):
            assert "尚未定义" not in line
            assert "仍留" not in line
            break
    else:
        raise AssertionError("未找到 M5 行")
