# -*- coding: utf-8 -*-
"""等长脱敏：密钥值逐字符替换为 *，保留换行与字符数。

设计：docs/taskhub/materials/spec.md §3.3
- source_snapshot 不落库，只算 hash；
- sanitized_payload 是校验基准；
- 等长掩码保证字符偏移与行号不变。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from mio_taskhub.materials.common import canonical_json, sha256_text

SANITIZER_VERSION = 1
# 规则代码版本：改动规则时必须同步 bump SANITIZER_VERSION
SANITIZER_RULE_VERSION = 1

# (compiled pattern, group index to mask)；group 0 表示整个匹配
_RULES: List[Tuple[re.Pattern, int]] = [
    (re.compile(r"(sk-[A-Za-z0-9]{16,})"), 1),
    (re.compile(r"(ghp_[A-Za-z0-9]{20,})"), 1),
    (re.compile(r"(github_pat_[A-Za-z0-9_]{20,})"), 1),
    (re.compile(r"(AKIA[0-9A-Z]{16})"), 1),
    (re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9._\-]{12,})"), 2),
    (re.compile(
        r"(?i)((?:api[_-]?key|token|password|passwd|secret)\s*[:=]\s*[\"']?)"
        r"([^\s\"',}]{6,})"
    ), 2),
    (re.compile(r"(://[^/\s:@]+:)([^/\s@]+)(@)"), 2),
    (re.compile(
        r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(.*?)(-----END [A-Z ]*PRIVATE KEY-----)",
        re.S,
    ), 0),
]


def sanitizer_config_hash() -> str:
    """规则代码 + 配置的稳定 hash；用于 raw revision 判定。"""
    rules = [(p.pattern, group) for p, group in _RULES]
    payload = {
        "version": SANITIZER_VERSION,
        "rule_version": SANITIZER_RULE_VERSION,
        "rules": rules,
    }
    return sha256_text(canonical_json(payload))


SANITIZER_CONFIG_HASH = sanitizer_config_hash()


def _mask_segment(segment: str) -> str:
    return "".join(ch if ch in "\r\n" else "*" for ch in segment)


def mask_text(text: str) -> str:
    """对单段文本做等长脱敏；从后往前替换，保持偏移稳定。"""
    if not text:
        return text
    out = text
    for pattern, group in _RULES:
        matches = list(pattern.finditer(out))
        for match in reversed(matches):
            start, end = match.span(group)
            if start < 0 or end <= start:
                continue
            out = out[:start] + _mask_segment(out[start:end]) + out[end:]
    return out


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    return value


@dataclass
class Sanitized:
    payload: Dict[str, Any]
    source_hash: str
    sanitized_hash: str
    config_hash: str
    version: int


def sanitize_payload(payload: Dict[str, Any]) -> Sanitized:
    source_json = canonical_json(payload)
    clean = _mask_value(payload)
    sanitized_json = canonical_json(clean)
    return Sanitized(
        payload=clean,
        source_hash=sha256_text(source_json),
        sanitized_hash=sha256_text(sanitized_json),
        config_hash=SANITIZER_CONFIG_HASH,
        version=SANITIZER_VERSION,
    )


def scan_text(text: str) -> List[str]:
    """复扫密钥；已掩码的纯 * 值不算命中。"""
    if not text:
        return []
    hits: List[str] = []
    for idx, (pattern, group) in enumerate(_RULES):
        for match in pattern.finditer(text):
            value = match.group(group)
            if not value:
                continue
            if set(value.replace("\r", "").replace("\n", "")) <= {"*"}:
                continue
            hits.append(f"rule-{idx}")
            break
    return hits
