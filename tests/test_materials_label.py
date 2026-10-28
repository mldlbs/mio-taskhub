# -*- coding: utf-8 -*-
"""P4 标注规则：边界案例回归。"""
from scripts.materials_label import classify_kind, usable


def _row(source, quote):
    return {"source_key": source, "quote": quote, "kind": "x"}


def test_review_acceptance_is_decision_not_problem():
    r = _row("doc.review", "验收通过：AC-1~AC-6 全通过，零失败。")
    assert classify_kind(r) == "decision"
    assert usable(r, 2)[0] is True


def test_review_rejection_is_problem():
    r = _row("doc.review", "不通过：缺少逐字校验与回滚用例。")
    assert classify_kind(r) == "problem"


def test_generic_failure_is_not_usable():
    r = _row("run.failure", "agent offline")
    assert usable(r, 2) == (False, "too_short")


def test_short_release_title_is_usable():
    r = _row("hn", "Picard 3.0 Released")
    assert usable(r, 2)[0] is True


def test_three_numbered_items_is_not_atomic():
    r = _row("doc.review", "验收通过：① 一项 ② 二项 ③ 三项，全部通过。")
    assert usable(r, 2) == (False, "not_atomic_items")


def test_boilerplate_word_boundary():
    r = _row("hn", "Example.com Just Launched the Biggest Redesign in Decades")
    assert usable(r, 2)[0] is True
