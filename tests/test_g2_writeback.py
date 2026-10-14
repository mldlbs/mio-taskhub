# -*- coding: utf-8 -*-
"""T2.5 实验结论回写定位文档单测。"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_positioning_v1_v2_v3_updated():
    text = (REPO / "docs" / "roadmap" / "01-positioning.md").read_text(encoding="utf-8")
    # G2 回写列存在
    assert "G2 实验回写" in text
    # V1 证据不足
    assert "证据不足" in text
    # V2 观测支持
    assert "观测支持" in text
    # V3 部分成立
    assert "部分成立" in text
    # 81.9% 数据点
    assert "81.9%" in text


def test_gap_e1_e6_updated():
    text = (REPO / "docs" / "roadmap" / "03-technical-gap.md").read_text(encoding="utf-8")
    assert "G2 回写" in text
    assert "E3" in text
    assert "部分成立" in text


def test_gap_d1_verdict_partially_met():
    text = (REPO / "docs" / "roadmap" / "03-technical-gap.md").read_text(encoding="utf-8")
    assert "命中此档" in text
    assert "81.9%" in text
    assert "n=234" in text or "234" in text
    # 部分成立档被标记
    assert "✅" in text or "命中" in text


def test_no_prohibited_claims_added():
    """回写不得引入禁止表述（§4.3）。"""
    text = (REPO / "docs" / "roadmap" / "01-positioning.md").read_text(encoding="utf-8")
    # G2 回写区域不得出现"生产级可靠""首创""突破"
    g2_section = text[text.find("G2 实验回写"):text.find("G2 收口结论") + 200]
    for banned in ["生产级可靠", "首创", "突破", "已验证提升"]:
        assert banned not in g2_section, f"G2 回写引入禁止表述: {banned}"
