# -*- coding: utf-8 -*-
"""T4.3 release_checklist.py 单测。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


def test_script_imports():
    import release_checklist as rc
    for fn in ("check_ci", "check_no_red", "check_license", "check_readme",
               "check_first_run", "check_release", "check_known_limits"):
        assert hasattr(rc, fn)


def test_license_check_green():
    import release_checklist as rc
    r = rc.check_license()
    assert r["status"] == "是"


def test_readme_check_green():
    import release_checklist as rc
    r = rc.check_readme()
    assert r["status"] == "是", r["detail"]


def test_known_limits_green():
    import release_checklist as rc
    r = rc.check_known_limits()
    assert r["status"] == "是", r["detail"]


def test_release_check_green():
    import release_checklist as rc
    r = rc.check_release()
    assert r["status"] == "是", r["detail"]


def test_first_run_partial_or_green():
    """首跑：本机 PASS；外部未取证时为「部分」。"""
    import release_checklist as rc
    r = rc.check_first_run()
    assert r["status"] in ("是", "部分"), r["detail"]


def test_report_doc_exists():
    doc = REPO / "docs" / "roadmap" / "t43-release-checklist-report.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert "7" in text or "七项" in text
    assert "CI" in text
