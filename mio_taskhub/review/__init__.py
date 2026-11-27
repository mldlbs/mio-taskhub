# -*- coding: utf-8 -*-
"""研发评审会：文档评审点提取与 H5 命中判定。

模块：
- extract：rule-only 评审点提取（H2 → H3 → H4 → 表格行/列表项递归下钻）
- h5：H5 pilot 盲回放的真问题命中判定（词边界 / 数字上下文 / scope / 弱命中）
"""
from .extract import (
    document_version,
    extract_review_points,
    infer_kind,
    infer_risk,
    task8_of,
)
from .h5 import FINAL_WEAK, find_hit, in_scope, replay, safe_pattern
from .llm import enhance as enhance_with_llm, parse_enhancement

__all__ = [
    'document_version', 'extract_review_points', 'infer_kind', 'infer_risk',
    'task8_of', 'FINAL_WEAK', 'find_hit', 'in_scope', 'replay', 'safe_pattern',
    'enhance_with_llm', 'parse_enhancement',
]
