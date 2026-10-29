# -*- coding: utf-8 -*-
"""发布入口静态审计：真实发布必须校验门禁报告，构建脚本不得直接发布。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_requires_gate_report():
    text = (ROOT / "packaging" / "release.ps1").read_text(encoding="utf-8")
    assert "GateReport" in text
    assert "verify_gate_report.py" in text
    assert "Real release requires -GateReport" in text
    assert "SkipGate" not in text


def test_build_script_does_not_publish():
    text = (ROOT / "packaging" / "build.ps1").read_text(encoding="utf-8")
    assert "api.github.com" not in text
    assert "/releases" not in text


def test_ci_runs_gate_and_attestation():
    text = (ROOT / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8")
    assert "tests/test_materials_gate.py" in text
    assert "tests/test_gate_attestation.py" in text
    assert "materials-gate:" in text


def test_branch_protection_audit_is_automated():
    text = (ROOT / ".github" / "workflows" / "branch-protection.yml").read_text(
        encoding="utf-8")
    assert "check_required_checks.py" in text
    assert "materials-gate" in text
    assert "test (3.12)" in text
