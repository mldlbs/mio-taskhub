# -*- coding: utf-8 -*-
"""T3.4 gen_readme_stats.py 单测。"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "gen_readme_stats.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=REPO,
    )


def test_license_tracked():
    """LICENSE 入库且为 MIT。"""
    r = subprocess.run(
        ["git", "ls-files", "LICENSE"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert r.stdout.strip() == "LICENSE"
    content = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in content
    assert "2026" in content


def test_collect_stats_returns_dict():
    sys.path.insert(0, str(REPO / "scripts"))
    from gen_readme_stats import collect_stats
    stats = collect_stats()
    assert stats["version"] == "0.5.5"
    assert stats["rest_endpoints"] > 0
    assert stats["mcp_tools"] > 0
    assert stats["test_functions"] > 0
    assert stats["loc"] > 0


def test_check_passes():
    """--check 应通过（README 与实际值一致）。"""
    r = _run("--check")
    assert r.returncode == 0, f"check failed: {r.stdout}"


def test_check_detects_drift():
    """人为制造漂移后 --check 应失败。"""
    readme = REPO / "README.md"
    original = readme.read_text(encoding="utf-8")
    try:
        # 人为改大版本号制造漂移
        drifted = original.replace("v0.5.5", "v9.9.9", 1)
        readme.write_text(drifted, encoding="utf-8")
        r = _run("--check")
        assert r.returncode == 1
        assert "drift" in r.stdout.lower() or "漂移" in r.stdout
    finally:
        readme.write_text(original, encoding="utf-8")


def test_write_roundtrip():
    """--write 后再 --check 应通过。"""
    r1 = _run("--write")
    assert r1.returncode == 0
    r2 = _run("--check")
    assert r2.returncode == 0
