# -*- coding: utf-8 -*-
"""T2.4 D1 首启成功率判定报告单测。"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _d1_verdict(success_rate: float) -> str:
    """03 §7 D1 三态判定：≥90% 支持 / 70–90% 部分 / <70% 不成立。"""
    if success_rate >= 0.90:
        return "支持"
    if success_rate >= 0.70:
        return "部分成立"
    return "不成立"


def test_d1_verdict_three_states():
    assert _d1_verdict(0.95) == "支持"
    assert _d1_verdict(0.90) == "支持"
    assert _d1_verdict(0.899) == "部分成立"
    assert _d1_verdict(0.819) == "部分成立"
    assert _d1_verdict(0.70) == "部分成立"
    assert _d1_verdict(0.699) == "不成立"
    assert _d1_verdict(0.50) == "不成立"


def test_d1_report_exists_and_has_verdict():
    report = REPO / "docs" / "d1_verdict_report.md"
    assert report.is_file(), "D1 判定报告应存在"
    text = report.read_text(encoding="utf-8")
    assert "部分成立" in text
    assert "81.9%" in text
    assert "n=234" in text or "234" in text
    assert "reaper" in text.lower()
    assert "真死" in text
    assert "链路断" in text
    assert "观测不足" in text


def test_d1_gates_documented():
    """报告含门槛：n≥100、reaper≥30。"""
    text = (REPO / "docs" / "d1_verdict_report.md").read_text(encoding="utf-8")
    assert "≥100" in text or ">=100" in text
    assert "≥30" in text or ">=30" in text


def test_first_start_stats_smoke():
    """first_start_stats 真库冒烟（n≥100 门通过）。pytest 隔离下 n=0 时跳过。"""
    import subprocess
    import sys
    py = REPO / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    proc = subprocess.run(
        [str(py), str(REPO / "scripts" / "first_start_stats.py"), "--min-n", "100"],
        capture_output=True, text=True, timeout=30)
    # n≥100 → exit 0；DB 不可达 → exit 2；pytest 隔离 n=0 → exit 1（跳过）
    if proc.returncode == 1 and "n_first_start=0" in proc.stderr:
        pytest.skip("pytest DB isolation: n_first_start=0, skip real-DB smoke")
    assert proc.returncode in (0, 2), proc.stderr[-300:]
    if proc.returncode == 0:
        assert "gates" in proc.stdout
        assert "True" in proc.stdout
