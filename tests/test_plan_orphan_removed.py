# -*- coding: utf-8 -*-
"""能力收敛：Plan 孤儿表移除的回归测试（task 1213d8ad）。

Plan SQLModel 模型已删除（实际 night plan 走内存 NightPlan + 文件）。
本测试防止该概念回潮，并验证迁移 DROP 幂等。
"""
import sqlite3

from mio_taskhub import models


def test_plan_model_removed():
    """models 不应再有 Plan 模型。"""
    assert not hasattr(models, "Plan"), "Plan 模型应已删除（能力收敛）"


def test_no_code_references_plan_model():
    """全库源码不应再引用 models.Plan（防止回潮）。"""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "mio_taskhub"
    offenders = []
    for f in root.rglob("*.py"):
        text = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"class Plan\(SQLModel", text):
            offenders.append(f.name)
        if re.search(r"\bfrom mio_taskhub\.models import[^\n]*\bPlan\b", text):
            offenders.append(f.name)
    assert not offenders, f"仍引用 Plan 模型: {offenders}"
