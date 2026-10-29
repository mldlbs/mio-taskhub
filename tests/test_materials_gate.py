# -*- coding: utf-8 -*-
"""自动化数据质量门禁：干净库通过，缺陷库阻塞。"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from mio_taskhub.materials.bootstrap import init_materials_db
from scripts.materials_gate import gate

TS = "2026-10-28 12:00:00.000000"
QUOTE = "Picard 3.0 Released"
PAYLOAD = json.dumps({"content": QUOTE + " (12 points by u)"}, ensure_ascii=False)
LAST_SEEN = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
EXPIRES = (LAST_SEEN + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S.%f")


def _seed(path):
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    init_materials_db(eng)
    eng.dispose()
    conn = sqlite3.connect(path)
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
        "('m1','signal','external','',?,'fp1',?,?,?,?, 'active',0.66,1,1,1,0,1,1,'{}',?,?)",
        (QUOTE, TS, TS, TS, EXPIRES, TS, TS))
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


def _mutate(path, sql, params=()):
    conn = sqlite3.connect(path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def test_gate_passes_on_clean_db(tmp_path):
    db = tmp_path / "clean.db"
    _seed(db)
    report = gate(db)
    assert report["ok"] is True
    assert report["blocking_failures"] == []
    assert report["metrics"]["active"] == 1


@pytest.mark.parametrize("sql,params,expected", [
    ("update material_witness set locator=? where id=1",
     (json.dumps({"field": "content", "char_start": 0, "char_end": 3,
                  "raw_revision": 1}),),
     "structural:verbatim"),
    ("update material_witness set is_primary=0 where id=1", (),
     "audit:I2_primary_witness_count"),
    ("update material_source set independence_group='' where key='hn'", (),
     "isolation:source_group_empty"),
    ("update material set expires_at=? where id='m1'",
     ("2026-11-30 00:00:00.000000",),
     "freshness:expires_consistency"),
    ("update material_raw set extract_status='rejected', reject_reason='' where id=1", (),
     "audit:rejected_without_reason"),
])
def test_gate_blocks_on_known_defects(tmp_path, sql, params, expected):
    db = tmp_path / "bad.db"
    _seed(db)
    _mutate(db, sql, params)
    report = gate(db)
    assert report["ok"] is False
    assert expected in report["blocking_failures"]
