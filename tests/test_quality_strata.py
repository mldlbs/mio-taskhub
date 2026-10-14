# -*- coding: utf-8 -*-
"""quality_strata.py 单测（T2.3 P 系列观测分层 V2）。"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import quality_strata as qs  # noqa: E402


def _mk_db(tmp_path, events=None, tasks=None):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE event (
        id INTEGER PRIMARY KEY, entity TEXT, entity_id TEXT,
        type TEXT, payload TEXT)""")
    conn.execute("""CREATE TABLE task (
        id TEXT PRIMARY KEY, doc_statuses TEXT)""")
    for e in (events or []):
        conn.execute("INSERT INTO event (entity, entity_id, type, payload) VALUES (?,?,?,?)",
                     ("task", e["task_id"], e["type"], json.dumps(e["payload"])))
    for t in (tasks or []):
        conn.execute("INSERT INTO task (id, doc_statuses) VALUES (?,?)",
                     (t["id"], json.dumps(t.get("doc_statuses") or {})))
    conn.commit()
    conn.close()
    return db


def test_fisher_exact_symmetric():
    # 对称表 p=1
    assert qs.fisher_exact_2x2(5, 5, 5, 5) == pytest.approx(1.0)
    # 极端表 p 小
    p = qs.fisher_exact_2x2(10, 0, 0, 10)
    assert p < 0.001


def test_collect_three_state_strata(tmp_path):
    events = [
        {"task_id": "a1", "type": "task_doc_status",
         "payload": {"kind": "api", "quality_score": 100}},
        {"task_id": "a2", "type": "task_doc_status",
         "payload": {"kind": "api", "quality_score": 45}},  # <80 incomplete
        {"task_id": "a3", "type": "task_doc_status",
         "payload": {"kind": "requirement", "quality_score": 90}},
    ]
    tasks = [
        {"id": "a1", "doc_statuses": {"api": {"state": "approved"}}},
        {"id": "a2", "doc_statuses": {"api": {"state": "review"}}},
        {"id": "a3", "doc_statuses": {"requirement": {"state": "approved"}}},
        {"id": "a4", "doc_statuses": {}},  # 全 missing
    ]
    db = _mk_db(tmp_path, events, tasks)
    data = qs.collect(db)
    api = data["by_kind"]["api"]
    assert api["n_with_status"] == 2
    assert api["n_complete"] == 1  # score 100
    assert api["n_incomplete"] == 1  # score 45 < 80
    assert api["n_missing"] == 2  # a3, a4
    req = data["by_kind"]["requirement"]
    assert req["n_complete"] == 1
    assert req["n_missing"] == 3


def test_g6b_score_distribution(tmp_path):
    events = [
        {"task_id": "x", "type": "task_doc_status",
         "payload": {"kind": "api", "quality_score": 100}},
        {"task_id": "y", "type": "task_doc_status",
         "payload": {"kind": "api", "quality_score": 45}},
        {"task_id": "z", "type": "task_doc_status",
         "payload": {"kind": "spec", "quality_score": 30}},
    ]
    db = _mk_db(tmp_path, events, [])
    data = qs.collect(db)
    g = data["g6b_score_distribution"]
    assert g["n_scored"] == 3
    assert g["n_lt_80"] == 2
    assert g["n_lt_45"] == 1


def test_f16_api_missing(tmp_path):
    events = []
    tasks = [{"id": f"t{i}", "doc_statuses": {}} for i in range(5)]
    db = _mk_db(tmp_path, events, tasks)
    data = qs.collect(db)
    f16 = data["f16_api_26_sections"]
    assert f16["total_sections"] == 26
    assert f16["n_tasks_api_missing"] == 5
    assert f16["n_tasks_api_with_status"] == 0


def test_render_summary_contains_sections(tmp_path):
    db = _mk_db(tmp_path, [], [])
    data = qs.collect(db)
    text = qs.render_summary(data)
    assert "三态分层" in text
    assert "F16" in text
    assert "G6b" in text
    assert "V2" in text


def test_cli_json_stdout(tmp_path, capsys):
    db = _mk_db(tmp_path, [], [])
    rc = qs.main(["--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "by_kind" in data


def test_cli_missing_db(tmp_path):
    rc = qs.main(["--db", str(tmp_path / "nope.sqlite")])
    assert rc == 2
