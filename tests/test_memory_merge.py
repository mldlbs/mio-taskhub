# -*- coding: utf-8 -*-
"""记忆视图合并 + 旧路径修复 回归守卫（task e4c3bc51）

FR-1 双 tab 单入口 / FR-2 前端无旧路径 / FR-3 docstring 与路由一致 /
FR-4 打包脚本与 spec COLLECT name 对齐（静态部分；实跑见 TC-3）
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "web" / "src"


def _web_sources():
    for pat in ("*.js", "*.jsx"):
        yield from sorted(WEB_SRC.rglob(pat))


def test_web_no_legacy_memory_paths():
    """FR-2: web/src 不得出现字面 /api/memory/（旧前缀恒 404）。"""
    offenders = []
    for f in _web_sources():
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "/api/memory/" in line:
                offenders.append(f"{f.relative_to(ROOT)}:{i}")
    assert not offenders, f"旧路径残留: {offenders}"


def test_api_memory_docstring_v1():
    """FR-3: api/memory.py docstring 端点清单与实际路由（/api/v1/memory/*）一致。"""
    src = (ROOT / "mio_taskhub" / "api" / "memory.py").read_text(encoding="utf-8")
    docstring = src.split('"""')[1]
    assert "/api/v1/memory/health" in docstring, "docstring 缺 v1 health 路径"
    bare = [
        i
        for i, line in enumerate(docstring.splitlines(), 1)
        if "/api/memory/" in line
    ]
    assert not bare, f"docstring 仍有裸 /api/memory/ 行: {bare}"


def test_build_script_dist_matches_spec():
    """FR-4: build.ps1 的 $distDir 必须指向 spec COLLECT name 对应的产物目录。"""
    spec = (ROOT / "mio-taskhub.spec").read_text(encoding="utf-8")
    m = re.search(r"COLLECT\s*\(.*?name\s*=\s*['\"]([^'\"]+)['\"]", spec, re.S)
    assert m, "mio-taskhub.spec 未找到 COLLECT name"
    spec_dir = m.group(1)

    ps1 = (ROOT / "packaging" / "build.ps1").read_text(encoding="utf-8")
    m2 = re.search(r"\$distDir\s*=\s*Join-Path\s+\$root\s+['\"]dist[\\/]([^'\"]+)['\"]", ps1)
    assert m2, "build.ps1 未找到 $distDir Join-Path 定义"
    script_dir = m2.group(1)

    assert script_dir == spec_dir, (
        f"产物目录失配: build.ps1={script_dir} vs spec COLLECT={spec_dir}"
    )


def test_rail_single_memory_entry():
    """FR-1: Rail 仅一个「记忆」入口；MemoryView 默认 observatory tab。"""
    rail = (ROOT / "web" / "src" / "components" / "Rail.jsx").read_text(encoding="utf-8")
    assert "id: 'observatory'" not in rail, "Rail 仍残留 observatory 独立入口"

    app = (ROOT / "web" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "view === 'observatory'" not in app, "App 仍残留 observatory 视图分支"
    assert "MemoryObservatoryView" not in app, "App 不应再直接引用 MemoryObservatoryView"

    mv = (ROOT / "web" / "src" / "components" / "MemoryView.jsx").read_text(encoding="utf-8")
    assert "useState('observatory')" in mv, "MemoryView 默认 tab 应为 observatory"
    assert "记忆观测" in mv and "记忆网关" in mv, "MemoryView 缺少双 tab 文案"
    assert "<MemoryObservatoryView />" in mv, "MemoryView 应渲染 MemoryObservatoryView"
