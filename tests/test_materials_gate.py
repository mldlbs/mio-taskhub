# -*- coding: utf-8 -*-
"""自动化数据质量门禁：I1–I18 逐项、边界与缺失数据。

每个用例都在一个干净库上只注入一类缺陷，断言门禁返回非零/阻塞，
并保留一个“合法数据不误报”的基线。
"""
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from mio_taskhub.materials.bootstrap import init_materials_db
from mio_taskhub.materials.common import content_fingerprint
from mio_taskhub.materials.constants import TTL_DAYS
from scripts.materials_gate import gate, run_gate

ROOT = Path(__file__).resolve().parents[1]
TS = "2026-10-28 12:00:00.000000"
QUOTE = "Picard 3.0 Released"
PAYLOAD = json.dumps({"content": QUOTE + " (12 points by u)"}, ensure_ascii=False)
LAST_SEEN = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
EXPIRES = (LAST_SEEN + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S.%f")


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _seed(path):
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    init_materials_db(eng)
    eng.dispose()
    conn = _connect(path)
    conn.execute(
        "insert into material_raw (id, source_key, external_id, raw_revision, "
        "observed_at, observed_at_estimated, fetched_at, payload, "
        "source_snapshot_hash, sanitized_payload_hash, sanitizer_version, "
        "sanitizer_config_hash, extract_version, extract_status, "
        "payload_truncated, created_at) values "
        "(1,'hn','e1',1,?,0,?,?, 'snap1','h1',1,'scfg',1,'extracted',0,?)",
        (TS, TS, PAYLOAD, TS))
    conn.execute(
        "insert into material_extract (id, material_id, raw_id, extract_version, "
        "extract_config_hash, draft_fingerprint, kind, quote_exact, locator, "
        "produced_at) values "
        "(1,'m1',1,1,'cfg','d1','signal',?,?,?)",
        (QUOTE, json.dumps({"field": "content", "char_start": 0,
                            "char_end": len(QUOTE)}), TS))
    conn.execute(
        "insert into material (id, kind, origin, scope_key, quote, "
        "content_fingerprint, observed_at, first_seen_at, last_seen_at, "
        "expires_at, status, quality, quality_model_version, occurrences, "
        "corroboration, needs_review, first_extract_version, last_extract_version, "
        "quality_detail, created_at, updated_at) values "
        "('m1','signal','external','',?,?,?,?,?,?, 'active',0.66,1,1,1,0,1,1,'{}',?,?)",
        (QUOTE, content_fingerprint(QUOTE), TS, TS, TS, EXPIRES, TS, TS))
    conn.execute(
        "insert into material_witness (id, material_id, raw_id, source_key, "
        "external_id, raw_revision, sanitizer_version, sanitized_payload_hash, "
        "quote_exact, locator, is_primary, occurrence_count, independence_group, "
        "independence_version, current_extract_id, observed_at, created_at) values "
        "(1,'m1',1,'hn','e1',1,1,'h1',?,?,1,1,'hn',1,1,?,?)",
        (QUOTE, json.dumps({"field": "content", "char_start": 0,
                            "char_end": len(QUOTE), "raw_revision": 1}), TS, TS))
    conn.commit()
    conn.close()


def _add(path, mid, kind, quote, source="hn", text=None, status="active"):
    """追加一条内部自洽的素材（raw/witness/extract/material 全齐）。"""
    text = quote if text is None else text
    conn = _connect(path)
    raw_id = (conn.execute("select coalesce(max(id),0)+1 from material_raw")
              .fetchone()[0])
    wid = (conn.execute("select coalesce(max(id),0)+1 from material_witness")
           .fetchone()[0])
    eid = (conn.execute("select coalesce(max(id),0)+1 from material_extract")
           .fetchone()[0])
    group = conn.execute("select independence_group from material_source where key=?",
                         (source,)).fetchone()[0]
    payload = json.dumps({"content": text}, ensure_ascii=False)
    locator = json.dumps({"field": "content", "char_start": 0,
                          "char_end": len(quote), "raw_revision": 1})
    days = TTL_DAYS.get(kind)
    expires = ((LAST_SEEN + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.%f")
               if days else None)
    conn.execute(
        "insert into material_raw (id, source_key, external_id, raw_revision, "
        "observed_at, observed_at_estimated, fetched_at, payload, "
        "source_snapshot_hash, sanitized_payload_hash, sanitizer_version, "
        "sanitizer_config_hash, extract_version, extract_status, "
        "payload_truncated, created_at) values "
        "(?,?,?,1,?,0,?,?, 'snap','h',1,'scfg',1,'extracted',0,?)",
        (raw_id, source, f"e{mid}", TS, TS, payload, TS))
    conn.execute(
        "insert into material_extract (id, material_id, raw_id, extract_version, "
        "extract_config_hash, draft_fingerprint, kind, quote_exact, locator, "
        "produced_at) values (?,?,?,1,'cfg',?,?,?,?,?)",
        (eid, mid, raw_id, f"d{mid}", kind, quote, locator, TS))
    conn.execute(
        "insert into material (id, kind, origin, scope_key, quote, "
        "content_fingerprint, observed_at, first_seen_at, last_seen_at, "
        "expires_at, status, quality, quality_model_version, occurrences, "
        "corroboration, needs_review, first_extract_version, last_extract_version, "
        "quality_detail, created_at, updated_at) values "
        "(?,?,'external','',?,?,?,?,?,?,?,0.66,1,1,1,0,1,1,'{}',?,?)",
        (mid, kind, quote, content_fingerprint(quote), TS, TS, TS, expires,
         status, TS, TS))
    conn.execute(
        "insert into material_witness (id, material_id, raw_id, source_key, "
        "external_id, raw_revision, sanitizer_version, sanitized_payload_hash, "
        "quote_exact, locator, is_primary, occurrence_count, independence_group, "
        "independence_version, current_extract_id, observed_at, created_at) values "
        "(?,?,?,?,?,1,1,'h',?,?,1,1,?,1,?,?,?)",
        (wid, mid, raw_id, source, f"e{mid}", quote, locator, group, eid, TS, TS))
    conn.commit()
    conn.close()


def _mutate(path, sql, params=()):
    conn = sqlite3.connect(path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _drop_extract_unique(path):
    """模拟“写入门禁失效”后的状态，用于验证 I15 审计能发现重复键。"""
    conn = sqlite3.connect(path)
    conn.execute("alter table material_extract rename to material_extract_old")
    conn.execute(
        "create table material_extract ("
        "id integer primary key, material_id varchar not null, "
        "raw_id integer not null, extract_version integer not null, "
        "extract_config_hash varchar not null, draft_fingerprint varchar not null, "
        "kind varchar not null, quote_exact varchar not null, "
        "locator varchar not null, produced_at datetime not null)")
    conn.execute("insert into material_extract select * from material_extract_old")
    conn.execute("drop table material_extract_old")
    conn.commit()
    conn.close()


def _failures(path):
    report = gate(path)
    return report, set(report["blocking_failures"])


def test_clean_db_passes(tmp_path):
    db = tmp_path / "clean.db"
    _seed(db)
    report = gate(db)
    assert report["ok"] is True
    assert report["blocking_failures"] == []
    assert report["metrics"]["active"] == 1


@pytest.mark.parametrize("mutate,expected", [
    (lambda p: _mutate(p, "delete from material_witness where id=1"),
     "audit:I1_material_without_witness"),
    (lambda p: _mutate(p, "update material_witness set is_primary=0 where id=1"),
     "audit:I2_primary_witness_count"),
    (lambda p: _mutate(p, "update material set quote='Other quote' where id='m1'"),
     "audit:I3_quote_matches_primary"),
    (lambda p: _mutate(p, "update material set content_fingerprint='bad' where id='m1'"),
     "audit:I4_fingerprint"),
    (lambda p: _mutate(p, "update material set occurrences=9 where id='m1'"),
     "audit:I5_occurrences"),
    (lambda p: _mutate(p, "update material set corroboration=9 where id='m1'"),
     "audit:I6_corroboration"),
    (lambda p: _mutate(
        p, "update material_witness set sanitizer_version=9 where id=1"),
     "audit:I12_witness_raw_version"),
    (lambda p: _mutate(
        p, "update material_witness set locator=? where id=1",
        (json.dumps({"field": "content", "char_start": 0,
                     "char_end": len(QUOTE), "raw_revision": 9}),)),
     "audit:I13_locator_revision"),
    (lambda p: _mutate(
        p, "update material_witness set current_extract_id=999 where id=1"),
     "audit:I14_current_extract"),
    (lambda p: _mutate(
        p, "update material set first_extract_version=9 where id='m1'"),
     "audit:I16_extract_versions"),
    (lambda p: _mutate(p, "delete from material_raw where id=1"),
     "audit:I18_raw_missing"),
    (lambda p: _mutate(p, "update material set scope_key='proj' where id='m1'"),
     "audit:I9_scope"),
])
def test_invariant_mutations_block(tmp_path, mutate, expected):
    db = tmp_path / "bad.db"
    _seed(db)
    mutate(db)
    report, failures = _failures(db)
    assert report["ok"] is False
    assert expected in failures, failures


def test_i7_active_gate_blocks_uncitable_signal(tmp_path):
    db = tmp_path / "i7.db"
    _seed(db)
    _add(db, "m2", "signal", "OpenSSH 10.6")
    report, failures = _failures(db)
    assert report["ok"] is False
    assert "audit:I7_active_gates" in failures


def test_i8_i11_links_to_archived_block(tmp_path):
    db = tmp_path / "i8.db"
    _seed(db)
    _mutate(db, "update material set status='archived' where id='m1'")
    _mutate(db, "insert into material_link (material_id, ref_type, ref_id, "
                "relation, created_by, created_at) values "
                "('m1','task','t1','evidence','test',?)", (TS,))
    report, failures = _failures(db)
    assert report["ok"] is False
    assert {"audit:I8_linked_not_archived",
            "audit:I11_link_target_status"} <= failures


def test_i10_llm_source_blocks(tmp_path):
    db = tmp_path / "i10.db"
    _seed(db)
    _mutate(db, "update material_source set key='mio-insight' where key='hub.console'")
    report, failures = _failures(db)
    assert report["ok"] is False
    assert "audit:I10_llm_source" in failures


def test_i17_group_change_requires_audit(tmp_path):
    db = tmp_path / "i17.db"
    _seed(db)
    _mutate(db, "update material_witness set independence_group='other' where id=1")
    report, failures = _failures(db)
    assert report["ok"] is False
    assert "isolation:group_change_audited" in failures


def test_i15_duplicate_extract_fingerprint_blocks(tmp_path):
    db = tmp_path / "i15.db"
    _seed(db)
    _drop_extract_unique(db)
    _mutate(db,
            "insert into material_extract (id, material_id, raw_id, extract_version, "
            "extract_config_hash, draft_fingerprint, kind, quote_exact, locator, "
            "produced_at) values (2,'m1',1,1,'cfg','d2','signal',?,?,?)",
            (QUOTE, json.dumps({"field": "content", "char_start": 0,
                                "char_end": len(QUOTE)}), TS))
    report, failures = _failures(db)
    assert report["ok"] is False
    assert "audit:I15_extract_append_only" in failures
    assert "duplicates:extract" in failures


def test_i15_same_fingerprint_is_not_blocked(tmp_path):
    db = tmp_path / "i15ok.db"
    _seed(db)
    _drop_extract_unique(db)
    _mutate(db,
            "insert into material_extract (id, material_id, raw_id, extract_version, "
            "extract_config_hash, draft_fingerprint, kind, quote_exact, locator, "
            "produced_at) values (2,'m1',1,1,'cfg','d1','signal',?,?,?)",
            (QUOTE, json.dumps({"field": "content", "char_start": 0,
                                "char_end": len(QUOTE)}), TS))
    report = gate(db)
    assert report["ok"] is True, report["blocking_failures"]


def test_boundary_atomic_240_passes_241_blocks(tmp_path):
    db = tmp_path / "atomic.db"
    _seed(db)
    _add(db, "m2", "result", "A" * 240, source="run.result")
    assert gate(db)["ok"] is True
    _add(db, "m3", "result", "B" * 241, source="run.result")
    report, failures = _failures(db)
    assert report["ok"] is False
    assert "structural:atomic" in failures


def test_boundary_decision_10_passes_9_blocks(tmp_path):
    db = tmp_path / "decision.db"
    _seed(db)
    _add(db, "m2", "decision", "验收通过：AC-1通", source="doc.review")
    assert gate(db)["ok"] is True
    _add(db, "m3", "decision", "验收通过：AC-1", source="doc.review")
    report, failures = _failures(db)
    assert report["ok"] is False
    assert "structural:decision_shape" in failures


def test_boundary_ttl_exact_passes_plus_one_second_blocks(tmp_path):
    db = tmp_path / "ttl.db"
    _seed(db)
    later = (LAST_SEEN + timedelta(days=30, seconds=1)).strftime(
        "%Y-%m-%d %H:%M:%S.%f")
    _mutate(db, "update material set expires_at=? where id='m1'", (later,))
    report, failures = _failures(db)
    assert report["ok"] is False
    assert "freshness:expires_consistency" in failures


def test_missing_database_returns_gate_error(tmp_path):
    report = run_gate(tmp_path / "missing.db")
    assert report["ok"] is False
    assert report["blocking_failures"] == ["gate:error"]


def test_missing_tables_returns_gate_error(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    report = run_gate(db)
    assert report["ok"] is False
    assert report["blocking_failures"] == ["gate:error"]


def test_cli_exit_code_nonzero_on_missing_db(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "materials_gate.py"),
         "--db", str(tmp_path / "missing.db")],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 1
    assert "gate:error" in proc.stdout
