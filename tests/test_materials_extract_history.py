# -*- coding: utf-8 -*-
"""T-EXTRACT：两个提取版本结果不同，历史与引用都保留。"""
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.materials.models import (
    Material, MaterialExtract, MaterialLink, MaterialSource,
)
from mio_taskhub.materials.pipeline import RawRecord, add_link, ingest_records
from mio_taskhub.materials.quality import Draft

FIRST = "First evidence sentence is here."
SECOND = "Second evidence sentence is there."
PAYLOAD = {"content": FIRST + " " + SECOND}


@pytest.fixture(autouse=True)
def _materials_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_MATERIALS_DIR", str(tmp_path / "materials"))


def _v1(payload, source):
    return [Draft(kind="signal", quote=FIRST, field="content", evidence_tier="A")]


def _v2(payload, source):
    return [Draft(kind="signal", quote=SECOND, field="content", evidence_tier="A")]


def _record():
    return RawRecord(
        source_key="hn", external_id="hn-extract-1",
        observed_at=datetime.now(timezone.utc), payload=PAYLOAD,
    )


def test_two_versions_keep_history_and_links():
    ingest_records("hn", [_record()], _v1)
    with Session(engine) as s:
        m1 = s.exec(select(Material).where(Material.quote == FIRST)).one()
        link = add_link(s, m1.id, "task", "task-1", "supports",
                        created_by="test")
        s.commit()
        m1_id, link_id = m1.id, link.id

    with Session(engine) as s:
        src = s.get(MaterialSource, "hn")
        src.extract_version = 2
        src.extract_config_hash = "v2"
        s.commit()

    ingest_records("hn", [_record()], _v2)
    ingest_records("hn", [_record()], _v2)  # 重放不重复写

    with Session(engine) as s:
        materials = s.exec(select(Material)).all()
        assert len(materials) == 2
        m2 = s.exec(select(Material).where(Material.quote == SECOND)).one()
        assert m1_id != m2.id
        assert m2.first_extract_version == 2

        extracts = s.exec(select(MaterialExtract)).all()
        assert len(extracts) == 2
        assert {e.extract_version for e in extracts} == {1, 2}

        link_row = s.get(MaterialLink, link_id)
        assert link_row is not None
        assert link_row.material_id == m1_id

        # 归档 M1 后历史与引用仍可查
        m1 = s.get(Material, m1_id)
        m1.status = "archived"
        s.commit()
        assert s.exec(
            select(MaterialExtract).where(MaterialExtract.material_id == m1_id)
        ).all()
        assert s.get(MaterialLink, link_id) is not None
