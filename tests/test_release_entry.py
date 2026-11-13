# -*- coding: utf-8 -*-
"""发布入口静态审计：构建脚本不得直接发布；门禁报告为可选路径（2026-11-13 解绑）。

-GateReport 改可选的依据：发布包是纯软件产物（zip 实证 0 个 material/db 文件），
素材质量与发布内容无关；素材线已于 2026-10-08 暂停。传入时仍必须走校验，
不得绕过 verify_gate_report.py。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_gate_report_is_optional_but_verified_when_given():
    text = (ROOT / "packaging" / "release.ps1").read_text(encoding="utf-8")
    assert "GateReport" in text
    # 传入报告时必须校验（no bypass 保留在「传了」路径上）
    assert "verify_gate_report.py" in text
    assert "Gate report verification failed; release blocked." in text
    # 旧强制文案应已移除（throw 改为 warning）
    assert "Real release requires -GateReport" not in text
    # 校验必须是「传了才跑」，且不传路径不得静默——要有显式警告
    assert "Write-Warning" in text
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
