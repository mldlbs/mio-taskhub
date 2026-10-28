# -*- coding: utf-8 -*-
"""素材库 P1：硬闸与质量评分。"""
from datetime import datetime, timedelta, timezone

from mio_taskhub.materials.quality import (
    Draft, density_score, evaluate_draft, expires_at, initial_status,
    score_material,
)


def _now():
    return datetime.now(timezone.utc)


def test_gate_accepts_verbatim():
    quote = "OpenLDK brings the Java world into Common Lisp."
    payload = {"content": quote}
    gate = evaluate_draft(
        Draft(kind="signal", quote=quote, field="content", evidence_tier="A"),
        payload, "hn", "", 1, 1,
    )
    assert gate.ok
    assert gate.char_start == 0
    assert gate.char_end == len(quote)
    assert gate.locator["raw_revision"] == 1


def test_gate_rejects_wrong_quote():
    payload = {"content": "The real content is here."}
    gate = evaluate_draft(
        Draft(kind="signal", quote="A different sentence"),
        payload, "hn", "", 1, 1,
    )
    assert not gate.ok
    assert gate.reason.startswith("HG-2")


def test_gate_rejects_short_and_invalid_kind():
    gate = evaluate_draft(
        Draft(kind="signal", quote="short"), {"content": "short"}, "hn", "", 1, 1,
    )
    assert not gate.ok and gate.reason.startswith("HG-5")
    gate = evaluate_draft(
        Draft(kind="unknown", quote="long enough sentence"),
        {"content": "long enough sentence"}, "hn", "", 1, 1,
    )
    assert not gate.ok and gate.reason.startswith("HG-4")


def test_gate_rejects_signal_with_project_scope():
    gate = evaluate_draft(
        Draft(kind="signal", quote="long enough sentence"),
        {"content": "long enough sentence"}, "hn", "proj", 1, 1,
    )
    assert not gate.ok and gate.reason.startswith("HG-7")


def test_gate_rejects_llm_product_source():
    gate = evaluate_draft(
        Draft(kind="signal", quote="long enough sentence"),
        {"content": "long enough sentence"}, "insights.daily", "", 1, 1,
    )
    assert not gate.ok and gate.reason.startswith("HG-6")


def test_density_and_status():
    quote = "OpenLDK brings the Java world into Common Lisp."
    assert density_score(quote) == 1.0
    assert density_score("点击这里") == 0.0
    quality, detail = score_material(
        "signal", "external", "A", quote, _now(), 1, now=_now(),
    )
    assert quality >= 0.65
    assert detail["evidence"] == 1.0
    assert initial_status(quality, "active")[0] == "active"
    assert initial_status(quality, "trial") == ("quarantine", "trial")


def test_expires_by_kind():
    now = _now()
    assert expires_at("signal", now) is not None
    assert expires_at("decision", now) is None
    assert expires_at("signal", now) > now + timedelta(days=29)
