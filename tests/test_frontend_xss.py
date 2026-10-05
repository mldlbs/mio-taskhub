# -*- coding: utf-8 -*-
"""前端 XSS 防护回归（task b7a55b74 / 评估 round3 P1-B）。

没有 JS 测试框架，用源码断言：任何 `dangerouslySetInnerHTML` 的 __html
都必须来自 DOMPurify.sanitize（或已净化的变量），禁止裸 marked() 直出。

这类"契约测试"能防止未来有人再次写出裸 marked→innerHTML 的路径。
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "web" / "src"


def _jsx_files():
    return list(SRC.rglob("*.jsx")) + list(SRC.rglob("*.js"))


def test_no_bare_marked_into_innerhtml():
    """禁止 `__html: marked(...)` 直出（必须经 DOMPurify）。"""
    offenders = []
    for f in _jsx_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"__html:\s*marked\(", text):
            offenders.append(f"{f.name}:{text[:m.start()].count(chr(10))+1}")
    assert not offenders, f"裸 marked→innerHTML 未净化：{offenders}"


def test_ideasview_adr_render_is_sanitized():
    text = (SRC / "components" / "IdeasView.jsx").read_text(encoding="utf-8")
    assert "DOMPurify.sanitize(marked(adrMd.content)" in text
    assert "import DOMPurify from 'dompurify'" in text


def test_docpanel_sanitizes():
    text = (SRC / "components" / "DocPanel.jsx").read_text(encoding="utf-8")
    assert "DOMPurify.sanitize(marked.parse(content)" in text


def test_all_innerhtml_sites_sanitized():
    """所有 dangerouslySetInnerHTML 的 __html 表达式须含 DOMPurify 或引用已净化 html 变量。"""
    bad = []
    for f in _jsx_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"__html:\s*([^}]+)\}", text):
            expr = m.group(1)
            ok = ("DOMPurify" in expr) or expr.strip() in ("html", "{html}")
            if not ok:
                bad.append(f"{f.name}: {expr.strip()[:60]}")
    assert not bad, f"未净化的 innerHTML 渲染点：{bad}"
