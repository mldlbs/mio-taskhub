# -*- coding: utf-8 -*-
"""T3.5 coldstart_timing.py 单测（不依赖运行中的 hub）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


def test_script_imports():
    import coldstart_timing as ct
    assert hasattr(ct, "wait_healthz")
    assert hasattr(ct, "measure_closed_loop")
    assert hasattr(ct, "run_once")
    assert hasattr(ct, "kill_hub")


def test_default_base():
    import coldstart_timing as ct
    assert "48620" in ct.DEFAULT_BASE or "127.0.0.1" in ct.DEFAULT_BASE


def test_terminate_handles_dead_proc():
    """_terminate 对已结束进程不抛异常。"""
    import coldstart_timing as ct
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    ct._terminate(p)  # 不应抛异常


def test_kill_hub_no_crash_when_absent():
    """kill_hub 对不存在的进程名返回 0 且不抛异常。"""
    import coldstart_timing as ct
    n = ct.kill_hub("definitely-not-a-real-process-xyz.exe")
    assert n == 0


def test_report_doc_exists():
    """T3.5 报告文档存在且含 M3/M4 判定。"""
    doc = REPO / "docs" / "roadmap" / "t35-coldstart-report.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert "M3" in text and "M4" in text
    assert "秒坑清单" in text
