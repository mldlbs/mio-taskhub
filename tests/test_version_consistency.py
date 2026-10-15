# -*- coding: utf-8 -*-
"""T4.1 版本号 / tag / CHANGELOG 一致性单测。"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _version_py() -> str:
    text = (REPO / "mio_taskhub" / "version.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1)


def test_version_py_matches_pyproject():
    v = _version_py()
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m and m.group(1) == v, f"pyproject={m.group(1) if m else None} version.py={v}"


def test_version_py_matches_otel():
    v = _version_py()
    text = (REPO / "mio_taskhub" / "observability" / "otel.py").read_text(encoding="utf-8")
    m = re.search(r'service_version:\s*str\s*=\s*"([^"]+)"', text)
    assert m and m.group(1) == v, f"otel={m.group(1) if m else None} version.py={v}"


def test_changelog_has_current_version_section():
    v = _version_py()
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## v{v}" in text, f"CHANGELOG 缺 v{v} 章节"


def test_changelog_version_ge_previous():
    """版本号必须递增（≥ 0.5.5）。"""
    v = _version_py()
    parts = tuple(int(x) for x in v.split("."))
    assert parts >= (0, 5, 5)


def test_git_tag_exists_for_version():
    """T4.1：当前版本号应有对应 tag（在发布提交上打）。"""
    v = _version_py()
    r = subprocess.run(["git", "tag", "-l"], capture_output=True, text=True, cwd=REPO)
    tags = r.stdout.split()
    if not tags:
        import pytest
        pytest.skip("仓库无 tag（浅克隆/CI 未取 tag）")
    assert f"v{v}" in tags, f"缺 tag v{v}（tags: {tags}）"
