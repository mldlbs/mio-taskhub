# -*- coding: utf-8 -*-
"""素材库常量：分类、TTL、质量阈值。"""
from __future__ import annotations

KINDS = frozenset({
    "signal", "fact", "result", "problem", "decision", "lesson",
})
ORIGINS = frozenset({"external", "internal", "task", "log"})

# LLM 产品来源不允许进入素材表（spec §3.6 HG-6）
LLM_SOURCE_MARKERS = frozenset({
    "insight", "insights", "topic", "topics", "creativity", "value-discovery",
})

TTL_DAYS = {
    "signal": 30,
    "problem": 180,
    "result": 180,
    "fact": 365,
    "decision": None,
    "lesson": None,
}

HALF_LIFE_DAYS = {
    "signal": 14,
    "problem": 30,
    "result": 90,
    "fact": 365,
    "decision": 3650,
    "lesson": 3650,
}

AUTHORITY_BASE = {
    "external": 0.5,
    "internal": 1.0,
    "task": 0.9,
    "log": 0.8,
}

EVIDENCE_SCORE = {
    "A": 1.0,
    "B": 0.95,
    "C": 0.90,
}

QUALITY_MODEL_VERSION = 1
ACTIVE_THRESHOLD = 0.65
QUARANTINE_FLOOR = 0.40

# 质量四维权重（spec §3.6）
W_EVIDENCE = 0.35
W_FRESHNESS = 0.20
W_AUTHORITY = 0.25
W_DENSITY = 0.20

# 硬闸长度（spec §3.6 HG-5）
MIN_QUOTE_CHARS = 8
MAX_QUOTE_CHARS = 400

# 单批上限（spec §3.9）
BATCH_MAX_ROWS = 200

# 重建锁（spec §3.7）
REBUILD_LOCK_SECONDS = 10 * 60
