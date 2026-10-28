# -*- coding: utf-8 -*-
"""P5.1 独立复核分析：一致率、kappa、分歧清单。"""
import json
from pathlib import Path

import pytest

from scripts.materials_review_analysis import analyze


def _kit(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    rows = [
        {"sample_id": 1, "source_key": "doc.review", "quote": "A"},
        {"sample_id": 2, "source_key": "hn", "quote": "B"},
        {"sample_id": 3, "source_key": "run.result", "quote": "C"},
    ]
    (kit / "review-pack.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return kit


def _sheet(path, rows):
    header = "sample_id\tsource_key\tquote\tlabel_kind\tlabel_usable\tconfidence\treason\treviewer_id\treviewed_at\tround\n"
    body = []
    for r in rows:
        body.append(f'{r["sample_id"]}\t{r["source_key"]}\t{r["quote"]}\t{r["kind"]}\t{r["usable"]}\t{r["confidence"]}\t{r["reason"]}\tA\t2026-10-28\t1')
    path.write_text(header + "\n".join(body) + "\n", encoding="utf-8")


def test_review_analysis_agreement_and_disagreement(tmp_path):
    kit = _kit(tmp_path)
    a = tmp_path / "a.tsv"
    b = tmp_path / "b.tsv"
    _sheet(a, [
        {"sample_id": 1, "source_key": "doc.review", "quote": "A", "kind": "decision", "usable": "yes", "confidence": "high", "reason": "U1"},
        {"sample_id": 2, "source_key": "hn", "quote": "B", "kind": "signal", "usable": "yes", "confidence": "medium", "reason": "U3"},
        {"sample_id": 3, "source_key": "run.result", "quote": "C", "kind": "result", "usable": "no", "confidence": "high", "reason": "U2"},
    ])
    _sheet(b, [
        {"sample_id": 1, "source_key": "doc.review", "quote": "A", "kind": "result", "usable": "yes", "confidence": "high", "reason": "test count"},
        {"sample_id": 2, "source_key": "hn", "quote": "B", "kind": "signal", "usable": "yes", "confidence": "high", "reason": "event"},
        {"sample_id": 3, "source_key": "run.result", "quote": "C", "kind": "result", "usable": "no", "confidence": "high", "reason": "fragment"},
    ])
    r = analyze(kit, [a, b])
    assert r["samples"] == 3
    pair = r["pairs"][0]
    assert pair["kind_agreement"] == 0.6667
    assert pair["usable_agreement"] == 1.0
    assert len(r["disagreements"]) == 1
    assert r["disagreements"][0]["sample_id"] == "1"


def test_review_analysis_requires_reason(tmp_path):
    kit = _kit(tmp_path)
    a = tmp_path / "a.tsv"
    b = tmp_path / "b.tsv"
    rows = [
        {"sample_id": i, "source_key": "hn", "quote": str(i), "kind": "signal",
         "usable": "yes", "confidence": "high", "reason": ""}
        for i in (1, 2, 3)
    ]
    _sheet(a, rows)
    _sheet(b, rows)
    with pytest.raises(ValueError):
        analyze(kit, [a, b])


def _label_rows():
    rows = []
    sid = 1
    for _ in range(3):
        rows.append({"sample_id": sid, "source_key": "run.result", "quote": f"q{sid}",
                     "cell": "problem|task", "status": "quarantine",
                     "label_usable": "yes"})
        sid += 1
    for cell in ("decision|internal", "result|task", "signal|external"):
        for usable in ("yes", "no"):
            for _ in range(4):
                rows.append({"sample_id": sid, "source_key": cell.split("|")[0],
                             "quote": f"q{sid}", "cell": cell, "status": "active",
                             "label_usable": usable})
                sid += 1
    for usable, count in (("yes", 2), ("no", 1)):
        for _ in range(count):
            rows.append({"sample_id": sid, "source_key": "run.result",
                         "quote": f"q{sid}", "cell": "problem|task",
                         "status": "active", "label_usable": usable})
            sid += 1
    return rows


def test_review_kit_builds_blind_pack_and_verifies(tmp_path):
    from scripts.materials_review_kit import build, verify

    src = tmp_path / "src"
    src.mkdir()
    labels = src / "labels.jsonl"
    labels.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                for r in _label_rows()), encoding="utf-8")
    sample = src / "sample.jsonl"
    sample.write_text("{}", encoding="utf-8")
    extra = {}
    for name in ("RULES-20261028.md", "REVIEW-PROTOCOL-P51.md", "README.md",
                 "REPORT-TEMPLATE.md"):
        p = src / name
        p.write_text(name, encoding="utf-8")
        extra[name] = p

    kit = tmp_path / "kit"
    build(sample, labels, kit, extra["RULES-20261028.md"],
          extra["REVIEW-PROTOCOL-P51.md"], extra["README.md"],
          extra["REPORT-TEMPLATE.md"])
    assert verify(kit) == []

    pack = [json.loads(x) for x in (kit / "review-pack.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    assert len(pack) == 30
    assert set(pack[0]) == {"sample_id", "source_key", "quote"}
    assert not (kit / "review-pack.jsonl").read_text(encoding="utf-8").count("label_usable")

    (kit / "review-pack.jsonl").write_text("tampered", encoding="utf-8")
    problems = verify(kit)
    assert any("review-pack.jsonl" in p for p in problems)

