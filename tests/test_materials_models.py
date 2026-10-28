# -*- coding: utf-8 -*-
"""素材库 P1：表、种子、唯一约束。"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.materials.models import (
    MaterialRaw, MaterialSource, MaterialSourceAudit,
)


def _now():
    return datetime.now(timezone.utc)


def test_seed_sources():
    with Session(engine) as s:
        rows = s.exec(select(MaterialSource)).all()
        assert len(rows) == 10
        assert {r.key for r in rows if r.mode == "trial"} == {
            "hub.console", "observer.serve",
        }
        assert all(r.independence_group == r.key for r in rows)
        assert all(r.independence_version == 1 for r in rows)


def test_raw_revision_unique():
    with Session(engine) as s:
        raw = MaterialRaw(
            source_key="hn", external_id="x", raw_revision=1,
            observed_at=_now(), payload="{}",
            source_snapshot_hash="a", sanitized_payload_hash="b",
        )
        s.add(raw)
        s.commit()
        s.add(MaterialRaw(
            source_key="hn", external_id="x", raw_revision=1,
            observed_at=_now(), payload="{}",
            source_snapshot_hash="c", sanitized_payload_hash="d",
        ))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_raw_snapshot_unique():
    with Session(engine) as s:
        s.add(MaterialRaw(
            source_key="hn", external_id="y", raw_revision=1,
            observed_at=_now(), payload="{}",
            source_snapshot_hash="same", sanitized_payload_hash="b",
            sanitizer_version=1, sanitizer_config_hash="cfg",
        ))
        s.commit()
        s.add(MaterialRaw(
            source_key="hn", external_id="y", raw_revision=2,
            observed_at=_now(), payload="{}",
            source_snapshot_hash="same", sanitized_payload_hash="b",
            sanitizer_version=1, sanitizer_config_hash="cfg",
        ))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_primary_witness_partial_index_exists():
    with Session(engine) as s:
        row = s.execute(text(
            "select sql from sqlite_master where type='index' "
            "and name='uq_material_witness_primary'"
        )).first()
        assert row is not None
        assert "WHERE is_primary = 1" in (row[0] or "")


def test_source_audit_append_only_shape():
    with Session(engine) as s:
        s.add(MaterialSourceAudit(
            source_key="hn", field="independence_group",
            old_value="hn", new_value="mirror", actor="test",
            reason="unit test",
        ))
        s.add(MaterialSourceAudit(
            source_key="hn", field="mode",
            old_value="active", new_value="trial", actor="test",
            reason="unit test",
        ))
        s.commit()
        rows = s.exec(select(MaterialSourceAudit)).all()
        assert len(rows) == 2
