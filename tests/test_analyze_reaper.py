# -*- coding: utf-8 -*-
"""analyze_reaper 三分分类与门槛的纯逻辑测试（不触生产 DB）。"""
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import analyze_reaper as ar  # noqa: E402


def test_classify_probe_overrides_status():
    probes = {"r1": False, "r2": True}
    b, reason = ar.classify({"run_id": "r1", "agent_status": "online"}, probes)
    assert b == ar.BUCKET_TRULY_DEAD and "probe" in reason
    b, reason = ar.classify({"run_id": "r2", "agent_status": "offline"}, probes)
    assert b == ar.BUCKET_LINK_BROKEN and "probe" in reason


def test_classify_status_heuristic():
    b, _ = ar.classify({"run_id": "x", "agent_status": "offline"}, {})
    assert b == ar.BUCKET_TRULY_DEAD
    b, _ = ar.classify({"run_id": "x", "agent_status": "online"}, {})
    assert b == ar.BUCKET_LINK_BROKEN
    b, reason = ar.classify({"run_id": "x", "agent_status": None}, {})
    assert b == ar.BUCKET_INSUFFICIENT


def test_load_process_probes(tmp_path):
    d = tmp_path / "1234"
    d.mkdir()
    f = d / "run-a.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "agent_lifecycle.process_alive", "run_id": "run-a", "alive": False}),
        json.dumps({"type": "agent_lifecycle.process_alive", "run_id": "run-b", "alive": True}),
        json.dumps({"type": "agent_lifecycle.agent_ready", "run_id": "run-a"}),
        "not-json",
    ]), encoding="utf-8")
    probes = ar.load_process_probes(tmp_path)
    assert probes == {"run-a": False, "run-b": True}


def _seed_db(path, n_offline, n_online, n_missing):
    con = sqlite3.connect(str(path))
    con.execute("create table if not exists event (id integer primary key, type text, at text, payload text)")
    eid = (con.execute("select coalesce(max(id),0)+1 from event").fetchone()[0])
    for status, count in [("offline", n_offline), ("online", n_online),
                          (None, n_missing)]:
        for _ in range(count):
            p = {"run_id": f"r{eid}", "task_id": "t1", "agent_id": "a1",
                 "agent_status": status, "reaper_kind": "x", "outcome": "requeue",
                 "heartbeat_lag_seconds": 320.0, "effective_timeout_seconds": 300}
            con.execute("insert into event values (?,?,?,?)",
                        (eid, "reaper_decision", "2026-10-13 00:00:00", json.dumps(p)))
            eid += 1
    con.commit()
    con.close()


def test_gate_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "_lifecycle_root", lambda: tmp_path / "nope")
    db = tmp_path / "db.sqlite"
    _seed_db(db, 10, 10, 9)  # n=29
    assert ar.main(["--db", str(db)]) == 2
    _seed_db(db, 10, 10, 10)  # n=30（同表再插 1 条）
    assert ar.main(["--db", str(db)]) == 0


def test_csv_export_and_buckets(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "_lifecycle_root", lambda: tmp_path / "nope")
    db = tmp_path / "db.sqlite"
    _seed_db(db, 5, 5, 2)
    out = tmp_path / "out.csv"
    assert ar.main(["--db", str(db), "--csv", str(out)]) == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("event_id,")
    buckets = {ln.split(",")[-2] for ln in lines[1:]}
    assert buckets == {ar.BUCKET_TRULY_DEAD, ar.BUCKET_LINK_BROKEN,
                       ar.BUCKET_INSUFFICIENT}


def test_scatter_histogram_smoke():
    text = ar.scatter_histogram([(320.0, 300), (250.0, 120), (None, None)])
    assert "lag" in text and "300" in text
