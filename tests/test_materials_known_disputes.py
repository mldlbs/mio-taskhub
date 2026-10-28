# -*- coding: utf-8 -*-
"""已知争议的确定性测试（类型、短结论、名词短语信号、无谓词片段、混合内容）。

fixture 由人工按 RULES v1.1 / 协议冻结口径编写，不随提取器输出自动更新。
"""
import json
from pathlib import Path

import pytest

from mio_taskhub.materials.extract import extract_for_source
from mio_taskhub.materials.pipeline import draft_reject_reason

FIXTURE = Path(__file__).parent / "fixtures" / "materials" / "known_disputes.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = DATA["cases"]
IDS = [c["id"] for c in CASES]
MARKERS = "①②③④⑤⑥⑦⑧⑨⑩"


def _reasons(case, drafts):
    return [
        draft_reject_reason(d, case["payload"], case["source"], "", 1, 1)
        for d in drafts
    ]


def test_fixture_covers_all_known_categories():
    categories = {c["category"] for c in CASES}
    assert categories == {
        "kind_attribution", "short_conclusion", "noun_phrase_signal",
        "no_predicate_fragment", "mixed_content",
    }
    assert len(IDS) == len(set(IDS))


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_known_dispute(case):
    drafts = extract_for_source(case["source"], case["payload"])
    expected = case["expected"]

    if "drafts" in expected:
        assert [[d.kind, d.quote] for d in drafts] == expected["drafts"]
    if "reject_reasons" in expected:
        assert _reasons(case, drafts) == expected["reject_reasons"]
    if expected.get("no_leading_marker"):
        for d in drafts:
            assert d.quote[:1] not in MARKERS, d.quote
            assert not d.quote.startswith("- "), d.quote
    if expected.get("max_len"):
        for d in drafts:
            assert len(d.quote) <= expected["max_len"], d.quote


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_known_dispute_is_deterministic(case):
    first = extract_for_source(case["source"], case["payload"])
    second = extract_for_source(case["source"], case["payload"])
    assert [(d.kind, d.quote, d.char_start, d.char_end) for d in first] == \
        [(d.kind, d.quote, d.char_start, d.char_end) for d in second]
