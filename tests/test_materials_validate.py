# -*- coding: utf-8 -*-
"""独立结构校验与漂移对比的合成数据测试。"""
import json
import sqlite3
from pathlib import Path

from scripts.materials_drift import compare
from scripts.materials_validate import validate

SCHEMA = [
    "create table material (id text primary key, kind text, quote text, "
    "status text, status_reason text, quality text, content_fingerprint text)",
    "create table material_witness (id integer primary key, material_id text, "
    "is_primary integer, source_key text, raw_id text, quote_exact text, locator text)",
    "create table material_raw (id text primary key, payload text)",
]


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    for sql in SCHEMA:
        conn.execute(sql)
    return conn


def _add(conn, mid, kind, quote, status="active", source="doc.review",
         payload_quote=None, slice_quote=None, fp=None):
    payload_quote = quote if payload_quote is None else payload_quote
    slice_quote = quote if slice_quote is None else slice_quote
    conn.execute("insert or replace into material_raw values (?,?)",
                 (f"raw_{mid}", json.dumps({"content": payload_quote}, ensure_ascii=False)))
    locator = json.dumps({"field": "content", "char_start": 0,
                          "char_end": len(slice_quote)})
    conn.execute(
        "insert into material_witness (material_id,is_primary,source_key,raw_id,"
        "quote_exact,locator) values (?,1,?,?,?,?)",
        (mid, source, f"raw_{mid}", slice_quote, locator))
    conn.execute(
        "insert into material values (?,?,?,?,?,?,?)",
        (mid, kind, quote, status, None, "0.6", fp or f"fp_{mid}"))


def test_validate_detects_structural_defects(tmp_path):
    db = tmp_path / "v.db"
    conn = _db(db)
    _add(conn, "m1", "decision", "验收通过：AC-1 全通过")
    _add(conn, "m2", "decision", "验收通过：")
    _add(conn, "m3", "result", "failed=25", source="run.result")
    _add(conn, "m4", "signal", "②可选恢复：先回滚再重试", source="hn")
    _add(conn, "m5", "signal", "A" * 250, source="hn")
    _add(conn, "m6", "decision", "验收通过：AC-2 全通过",
         payload_quote="验收通过：AC-3 全通过", slice_quote="验收通过：AC-3 全通过")
    _add(conn, "m7", "signal", "Picard 3.0 Released", status="quarantine",
         source="hn")
    _add(conn, "m8", "decision", "验收通过：AC-1 全通过", fp="fp_m1")
    conn.commit()
    conn.close()

    report = validate(db)
    assert report["population"]["active"] == 7
    assert report["population"]["quarantine"] == 1
    assert report["checks"]["active_with_any_defect"] == 5
    assert report["checks"]["by_check"] == {
        "decision_shape": 1, "bare_count": 1, "marker_leak": 1,
        "atomic": 1, "verbatim": 1,
    }
    assert report["checks"]["duplicate_groups"] == 1
    assert report["checks"]["duplicate_materials"] == 2
    assert report["quarantine_possible_miss"]["n"] == 1
    assert report["structural_misaccept"]["examples"]["verbatim"][0]["id"] == "m6"


def test_validate_excludes_held_out_ids(tmp_path):
    db = tmp_path / "v.db"
    conn = _db(db)
    _add(conn, "m1", "decision", "验收通过：AC-1 全通过")
    _add(conn, "m2", "decision", "验收通过：")
    conn.commit()
    conn.close()
    excluded = tmp_path / "labels.jsonl"
    excluded.write_text(
        json.dumps({"sample_id": 1, "material_id": "m1"}) + "\n" +
        json.dumps({"sample_id": 2, "material_id": "other", "kind": "decision",
                    "quote": "验收通过："}, ensure_ascii=False),
        encoding="utf-8")
    report = validate(db, [excluded])
    assert report["population"]["evaluated"] == 0
    assert report["population"]["excluded_held_out"] == 2
    assert report["structural_misaccept"]["n"] == 0


def test_drift_detects_add_remove_kind_and_status(tmp_path):
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"
    ca, cb = _db(a), _db(b)
    _add(ca, "m1", "decision", "Q1", fp="f1")
    _add(ca, "m2", "signal", "Q2", source="hn", fp="f2")
    _add(ca, "m3", "result", "Q3", source="run.result", fp="f3")
    _add(cb, "m1", "result", "Q1", source="run.result", fp="f1")
    _add(cb, "m3", "result", "Q3", status="quarantine", source="run.result", fp="f3")
    _add(cb, "m4", "signal", "Q4", source="hn", fp="f4")
    ca.commit(); cb.commit(); ca.close(); cb.close()

    report = compare(a, b)
    assert report["counts"]["removed"] == 1
    assert report["counts"]["added"] == 1
    assert report["counts"]["kind_changed"] == 1
    assert report["counts"]["status_changed"] == 1
    assert report["counts"]["union"] == 4
    assert report["drift_rate"] == 0.75
