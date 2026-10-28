# -*- coding: utf-8 -*-
"""素材库公共工具：规范化、哈希、指纹。"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

_WS = re.compile(r"\s+")
_MASK_RUN = re.compile(r"\*+")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    """NFKC + 压缩空白 + 掩码折叠 + 去首尾空白。保留大小写。"""
    s = unicodedata.normalize("NFKC", text or "")
    s = _WS.sub(" ", s).strip()
    # 脱敏升级可能导致掩码长度变化；折叠后同一证据仍能合并（spec §3.4）
    s = _MASK_RUN.sub("<mask>", s)
    return s


def content_fingerprint(quote: str) -> str:
    return sha256_text(normalize_text(quote))


def draft_fingerprint(kind: str, quote: str, locator: dict) -> str:
    return sha256_text(canonical_json({
        "kind": kind,
        "quote": normalize_text(quote),
        "locator": {
            "field": locator.get("field"),
            "char_start": locator.get("char_start"),
            "char_end": locator.get("char_end"),
        },
    }))
