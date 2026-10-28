# -*- coding: utf-8 -*-
"""素材库 P1：流水线幂等、raw revision、trial、引用。"""
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.materials.models import (
    Material, MaterialExtract, MaterialRaw, MaterialSource, MaterialWitness,
)
from mio_taskhub.materials.pipeline import (
    RawRecord, add_link, ingest_records, rebuild_source,
)
from mio_taskhub.materials.quality import Draft


@pytest.fixture(autouse=True)
def _materials_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_MATERIALS_DIR", str(tmp_path / "materials"))


def _extractor(payload, source):
    return [Draft(
        kind="signal",
        quote=payload["content"],
        field="content",
        evidence_tier="A",
        url="https://example.com/item",
    )]


def _record(external_id, content, project=None):
    return RawRecord(
        source_key="hn",
        external_id=external_id,
        observed_at=datetime.now(timezone.utc),
        payload={"content": content},
        project=project,
    )


def test_replay_is_idempotent():
    rec = _record("hn-1", "OpenLDK brings the Java world into Common Lisp.")
    a = ingest_records("hn", [rec], _extractor)
    b = ingest_records("hn", [rec], _extractor)
    assert a.raw_new == 1 and a.materials_new == 1
    assert b.raw_reused == 1 and b.skipped == 1
    with Session(engine) as s:
        assert len(s.exec(select(Material)).all()) == 1
        assert len(s.exec(select(MaterialRaw)).all()) == 1
        assert len(s.exec(select(MaterialWitness)).all()) == 1
        assert len(s.exec(select(MaterialExtract)).all()) == 1


def test_raw_revision_does_not_inflate_occurrences():
    quote = "OpenLDK brings the Java world into Common Lisp."
    ingest_records("hn", [_record("hn-2", quote)], _extractor)
    # 同一来源记录，内容快照不同（额外字段），quote 相同
    rec2 = RawRecord(
        source_key="hn", external_id="hn-2",
        observed_at=datetime.now(timezone.utc),
        payload={"content": quote, "extra": "v2"},
    )
    ingest_records("hn", [rec2], _extractor)
    with Session(engine) as s:
        assert len(s.exec(select(MaterialRaw)).all()) == 2
        m = s.exec(select(Material)).one()
        assert m.occurrences == 1
        assert m.corroboration == 1
        assert len(s.exec(select(MaterialWitness)).all()) == 2


def test_trial_source_never_active():
    rec = RawRecord(
        source_key="hub.console",
        external_id="log-1",
        observed_at=datetime.now(timezone.utc),
        payload={"content": "ERROR database is locked after 5000ms"},
    )
    ingest_records("hub.console", [rec], lambda p, s: [
        Draft(kind="problem", quote=p["content"], field="content",
              evidence_tier="C"),
    ])
    with Session(engine) as s:
        m = s.exec(select(Material)).one()
        assert m.status == "quarantine"
        assert m.status_reason == "trial"


def test_link_requires_active_material():
    rec = _record("hn-3", "OpenLDK brings the Java world into Common Lisp.")
    ingest_records("hn", [rec], _extractor)
    with Session(engine) as s:
        m = s.exec(select(Material)).one()
        assert m.status == "active"
        link = add_link(s, m.id, "task", "task-1", "context", created_by="test")
        s.commit()
        assert link.id is not None
        assert len(s.exec(select(Material)).all()) == 1


def test_link_rejects_quarantine_material():
    rec = RawRecord(
        source_key="hub.console", external_id="log-2",
        observed_at=datetime.now(timezone.utc),
        payload={"content": "ERROR database is locked after 5000ms"},
    )
    ingest_records("hub.console", [rec], lambda p, s: [
        Draft(kind="problem", quote=p["content"], field="content",
              evidence_tier="C"),
    ])
    with Session(engine) as s:
        m = s.exec(select(Material)).one()
        with pytest.raises(ValueError):
            add_link(s, m.id, "task", "task-1", "context")


def test_rebuild_rejects_config_drift_without_version_bump():
    rec = _record("hn-4", "OpenLDK brings the Java world into Common Lisp.")
    ingest_records("hn", [rec], _extractor)
    with Session(engine) as s:
        src = s.get(MaterialSource, "hn")
        src.extract_version = 2
        src.extract_config_hash = "v2"
        s.commit()
    with pytest.raises(ValueError, match="config_version_mismatch"):
        rebuild_source("hn", _extractor, to_version=2, to_config_hash="v3")


def test_rejected_raw_is_not_reprocessed_same_version():
    rec = _record("hn-rej", "A sufficiently long content for the payload.")
    bad = lambda p, s: [Draft(
        kind="signal", quote="short", field="content", evidence_tier="A")]
    first = ingest_records("hn", [rec], bad)
    second = ingest_records("hn", [rec], bad)
    assert first.rejected == 1
    assert second.skipped == 1
    assert second.rejected == 0
