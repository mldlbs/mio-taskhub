# -*- coding: utf-8 -*-
"""硬闸与质量评分（spec §3.6）。规则指标，不做语义判断。"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from mio_taskhub.materials.constants import (
    ACTIVE_THRESHOLD, AUTHORITY_BASE, EVIDENCE_SCORE, HALF_LIFE_DAYS,
    KINDS, LLM_SOURCE_MARKERS, MAX_QUOTE_CHARS, MIN_QUOTE_CHARS,
    QUARANTINE_FLOOR, QUALITY_MODEL_VERSION, TTL_DAYS,
    W_AUTHORITY, W_DENSITY, W_EVIDENCE, W_FRESHNESS,
)

_BOILERPLATE = (
    "点击这里", "阅读全文", "read more", "sign in", "log in",
    "登录后", "注册", "subscribe", "share this", "comments",
)
_TERMINATORS = "。！？.!?"
_ATOMIC_SOURCES = {
    "doc.review", "discussion.closed", "run.result",
    "hn", "github-trending",
}
_DECISION_SOURCES = {"doc.requirement", "doc.review", "discussion.closed"}
_TERM_RE = re.compile(r"[。！？]|[.!?](?=\s|$)")
_ITEM_RE = re.compile(r"[①-⑩]|(?<!\d)\d+[)）、]")
_CLAIM_RE = re.compile(
    r"是|为|必须|禁止|应|需|可|返回|新增|删除|保留|采用|通过|失败|错误|"
    r"支持|实现|走|落|改|加|减|校验|拦截|超时|发布|推出|宣布|加入|泄露|"
    r"起诉|裁员|收购|上线|修复|移除|打破|证实|发现|建议|要求|规定|决定|"
    r"确认|同意|拒绝|默认|优先|需求|规则|验收|完成|保证|确保|approved|passed|failed|"
    r"errors=|is|are|has|can", re.I)
_BARE_COUNT_RE = re.compile(
    r"^\s*(?:\d+\s*(?:passed|failed|skipped|errors?)?|"
    r"(?:passed|failed|skipped|errors?)\s*[=:：]\s*\d+)\s*$", re.I)


@dataclass
class Draft:
    kind: str
    quote: str
    field: str = "content"
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    title: Optional[str] = None
    url: Optional[str] = None
    scope_key: Optional[str] = None
    occurrence_count: int = 1
    evidence_tier: str = "C"
    source_ref: Optional[dict] = None
    line: Optional[int] = None


@dataclass
class GateResult:
    ok: bool
    reason: str = ""
    field: str = "content"
    char_start: int = 0
    char_end: int = 0
    fragment: str = ""
    locator: Dict[str, Any] = dc_field(default_factory=dict)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_locator(raw_revision: int, field: str, start: int, end: int,
                  line: Optional[int], sanitizer_version: int) -> Dict[str, Any]:
    return {
        "v": 1,
        "type": "json",
        "raw_revision": raw_revision,
        "field": field,
        "char_start": start,
        "char_end": end,
        "line": line,
        "sanitizer_version": sanitizer_version,
    }


def evaluate_draft(draft: Draft, payload: Dict[str, Any], source_key: str,
                   scope_key: str, raw_revision: int,
                   sanitizer_version: int) -> GateResult:
    """HG-1~HG-7（HG-8 在 witness 写入时校验）。"""
    if draft.kind not in KINDS:
        return GateResult(False, "HG-4:invalid_kind")

    n = len(draft.quote or "")
    if n < MIN_QUOTE_CHARS or n > MAX_QUOTE_CHARS:
        return GateResult(False, "HG-5:length")
    if source_key in _ATOMIC_SOURCES:
        if n > 240 or len(_TERM_RE.findall(draft.quote)) > 2 \
                or len(_ITEM_RE.findall(draft.quote)) >= 3:
            return GateResult(False, "HG-9:not_atomic")
    if source_key in _DECISION_SOURCES:
        q = (draft.quote or "").strip()
        if n < 10 or q.endswith(("：", ":")) or not _CLAIM_RE.search(q):
            return GateResult(False, "HG-12:not_a_conclusion")
    if draft.kind == "result" and _BARE_COUNT_RE.match((draft.quote or "").strip()):
        return GateResult(False, "HG-13:bare_count")

    low_source = (source_key or "").lower()
    if any(marker in low_source for marker in LLM_SOURCE_MARKERS):
        return GateResult(False, "HG-6:llm_product")

    if draft.kind == "signal" and scope_key:
        return GateResult(False, "HG-7:signal_scope")

    raw_value = payload.get(draft.field)
    if not isinstance(raw_value, str):
        return GateResult(False, "HG-1:field_missing")

    start = draft.char_start
    end = draft.char_end
    if start is None or end is None:
        idx = raw_value.find(draft.quote)
        if idx < 0:
            return GateResult(False, "HG-2:quote_not_found")
        start, end = idx, idx + len(draft.quote)

    if start < 0 or end > len(raw_value) or end <= start:
        return GateResult(False, "HG-1:offset_out_of_range")

    fragment = raw_value[start:end]
    if fragment != draft.quote:
        return GateResult(False, "HG-3:not_verbatim")

    locator = build_locator(raw_revision, draft.field, start, end,
                            draft.line, sanitizer_version)
    return GateResult(True, "", draft.field, start, end, fragment, locator)


def density_score(quote: str) -> float:
    q = quote or ""
    n = len(q)
    if n < MIN_QUOTE_CHARS or n > MAX_QUOTE_CHARS:
        return 0.0
    low = q.lower()
    if any(b in low for b in _BOILERPLATE):
        return 0.0
    if 20 <= n <= 160:
        base = 1.0
    elif n <= 240:
        base = 0.8
    elif n <= 400:
        base = 0.6
    else:
        base = 0.4
    terminators = sum(q.count(ch) for ch in _TERMINATORS)
    if terminators > 2:
        base = max(0.2, base - 0.2)
    return base


def score_material(kind: str, origin: str, evidence_tier: str, quote: str,
                   last_seen_at: datetime, corroboration: int,
                   now: Optional[datetime] = None) -> Tuple[float, Dict[str, Any]]:
    now = _utc(now or datetime.now(timezone.utc))
    last_seen = _utc(last_seen_at)
    age_days = max(0.0, (now - last_seen).total_seconds() / 86400.0)

    evidence = EVIDENCE_SCORE.get(evidence_tier, EVIDENCE_SCORE["C"])
    half_life = HALF_LIFE_DAYS.get(kind, 365)
    freshness = math.exp(-age_days / float(half_life))
    base = AUTHORITY_BASE.get(origin, 0.5)
    authority = min(1.0, base + 0.15 * max(0, corroboration - 1))
    density = density_score(quote)

    quality = (
        W_EVIDENCE * evidence
        + W_FRESHNESS * freshness
        + W_AUTHORITY * authority
        + W_DENSITY * density
    )
    detail = {
        "model_version": QUALITY_MODEL_VERSION,
        "evidence": round(evidence, 4),
        "freshness": round(freshness, 4),
        "authority": round(authority, 4),
        "density": round(density, 4),
        "age_days": round(age_days, 2),
        "corroboration": corroboration,
        "evidence_tier": evidence_tier,
    }
    return round(quality, 4), detail


def initial_status(quality: float, source_mode: str) -> Tuple[str, Optional[str]]:
    if source_mode == "trial":
        return "quarantine", "trial"
    if quality >= ACTIVE_THRESHOLD:
        return "active", None
    if quality < QUARANTINE_FLOOR:
        return "quarantine", "low_quality"
    return "quarantine", "below_threshold"


def expires_at(kind: str, last_seen_at: datetime) -> Optional[datetime]:
    days = TTL_DAYS.get(kind)
    if days is None:
        return None
    return _utc(last_seen_at) + timedelta(days=days)
