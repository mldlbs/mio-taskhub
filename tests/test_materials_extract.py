# -*- coding: utf-8 -*-
"""P2：确定性提炼、黄金样本与 50 条分类基线。"""
import json
from pathlib import Path

import pytest

from mio_taskhub.api.task_lessons import has_test_evidence, split_sentences
from mio_taskhub.materials.extract import EXTRACTORS, extract_for_source
from mio_taskhub.materials.quality import evaluate_draft
from mio_taskhub.materials.seed import SOURCE_DEFS

FIX = Path(__file__).resolve().parent / "fixtures" / "materials"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))["cases"]


def _signature(drafts):
    return [(d.kind, d.quote, d.char_start, d.char_end) for d in drafts]


def _gate_ok(source, payload, drafts):
    for d in drafts:
        gate = evaluate_draft(
            d, payload, source, d.scope_key or "", 1, 1,
        )
        assert gate.ok, f"{source}: {gate.reason} / {d.quote!r}"


def test_registry_covers_all_seeded_sources():
    for spec in SOURCE_DEFS:
        assert spec["key"] in EXTRACTORS


def test_unknown_source_raises():
    with pytest.raises(KeyError):
        extract_for_source("unknown.source", {})


def test_golden_exact():
    cases = _load("golden.json")
    assert len(cases) == 16
    for case in cases:
        drafts = extract_for_source(case["source"], case["payload"])
        assert [(d.kind, d.quote) for d in drafts] == [
            (e["kind"], e["quote"]) for e in case["expected"]
        ], case["source"]
        _gate_ok(case["source"], case["payload"], drafts)


def test_golden_is_deterministic():
    for case in _load("golden.json"):
        runs = [
            _signature(extract_for_source(case["source"], case["payload"]))
            for _ in range(3)
        ]
        assert runs[0] == runs[1] == runs[2]


def test_classification_baseline_50():
    cases = _load("classification.json")
    assert len(cases) == 50
    total = 0
    correct = 0
    for case in cases:
        drafts = extract_for_source(case["source"], case["payload"])
        assert drafts, case["source"]
        _gate_ok(case["source"], case["payload"], drafts)
        for d in drafts:
            total += 1
            correct += 1 if d.kind == case["expected_kind"] else 0
    accuracy = correct / total
    # P2 初始基线；P4 用真实标注集重做
    assert accuracy >= 0.90, accuracy


def test_public_lesson_helpers():
    assert has_test_evidence("pytest 1636 passed")
    assert not has_test_evidence("Implemented a local cache")
    parts = split_sentences("第一句。第二句；\n- 列表项")
    assert len(parts) >= 3
