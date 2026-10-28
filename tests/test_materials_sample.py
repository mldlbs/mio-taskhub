# -*- coding: utf-8 -*-
"""P4 抽样：分层配额与可追溯标注模板。"""
import json
import sqlite3

from scripts.materials_sample import allocate, build_sample


def test_allocate_proportional_and_capped():
    q = allocate({"a": 100, "b": 50}, 30)
    assert sum(q.values()) == 30
    assert q["a"] == 15 and q["b"] == 15
    q2 = allocate({"a": 2, "b": 100}, 10)
    assert q2["a"] >= 1
    assert sum(q2.values()) == 10


def test_build_sample_shape_and_determinism(tmp_path):
    db = tmp_path / "sample.db"
    with sqlite3.connect(db) as c:
        c.execute("create table material (id text primary key, kind text, origin text,"
                  "status text, quality real, quote text, observed_at text)")
        c.execute("create table material_witness (material_id text, source_key text,"
                  "is_primary int)")
        c.executemany(
            "insert into material values (?,?,?,?,?,?,?)",
            [
                ("m1", "signal", "external", "active", 0.9, "External signal one", "2026-10-28"),
                ("m2", "signal", "external", "active", 0.8, "External signal two", "2026-10-28"),
                ("m3", "decision", "internal", "active", 0.9, "Internal decision", "2026-10-28"),
            ],
        )
        c.executemany(
            "insert into material_witness values (?,?,1)",
            [("m1", "hn"), ("m2", "hn"), ("m3", "doc.requirement")],
        )
    a, ma = build_sample(db, n=2, seed=7)
    b, mb = build_sample(db, n=2, seed=7)
    assert [x["material_id"] for x in a] == [x["material_id"] for x in b]
    assert len(a) == 2
    assert ma["sampled"] == 2
    assert len(ma["per_cell"]) == 24
    assert "fact|external" in ma["insufficient_cells"]
    assert all(x["label_kind"] == "" for x in a)
    assert all(x["evidence_insufficient"] is False for x in a)
