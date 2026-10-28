# -*- coding: utf-8 -*-
"""T-RAWVER：脱敏升级后旧 witness 仍按旧 raw revision 回读。"""
import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

import mio_taskhub.materials.sanitize as san
from mio_taskhub.db import engine
from mio_taskhub.materials.models import (
    Material, MaterialExtract, MaterialRaw, MaterialWitness,
)
from mio_taskhub.materials.pipeline import RawRecord, ingest_records
from mio_taskhub.materials.quality import Draft


@pytest.fixture(autouse=True)
def _materials_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_MATERIALS_DIR", str(tmp_path / "materials"))


def _extractor(payload, source):
    return [Draft(kind="problem", quote=payload["content"], field="content",
                  evidence_tier="C")]


def _record():
    return RawRecord(
        source_key="run.failure",
        external_id="run-1",
        observed_at=datetime.now(timezone.utc),
        payload={"content": "Server error password=supersecret caused restart"},
    )


def test_old_witness_survives_sanitizer_upgrade(monkeypatch):
    ingest_records("run.failure", [_record()], _extractor)
    with Session(engine) as s:
        raw1 = s.exec(select(MaterialRaw)).one()
        w1 = s.exec(select(MaterialWitness)).one()
        m = s.exec(select(Material)).one()
        assert w1.raw_id == raw1.id
        assert w1.sanitizer_version == 1
        quote1 = json.loads(raw1.payload)["content"]
        assert w1.quote_exact == quote1
        assert "supersecret" not in quote1

    # 模拟 sanitizer v2：同内容快照，不同算法/配置 → 新 raw revision
    monkeypatch.setattr(san, "SANITIZER_VERSION", 2)
    monkeypatch.setattr(san, "SANITIZER_CONFIG_HASH", "v2-config")

    ingest_records("run.failure", [_record()], _extractor)

    with Session(engine) as s:
        raws = s.exec(select(MaterialRaw).order_by(MaterialRaw.id)).all()
        assert len(raws) == 2
        assert raws[0].raw_revision == 1 and raws[1].raw_revision == 2
        assert raws[0].sanitizer_version == 1
        assert raws[1].sanitizer_version == 2

        # 旧 witness 仍指向旧 raw，逐字命中旧 payload
        w1 = s.exec(
            select(MaterialWitness).where(MaterialWitness.raw_id == raws[0].id)
        ).one()
        old_quote = json.loads(raws[0].payload)["content"]
        assert w1.quote_exact == old_quote
        assert w1.sanitized_payload_hash == raws[0].sanitized_payload_hash

        w2 = s.exec(
            select(MaterialWitness).where(MaterialWitness.raw_id == raws[1].id)
        ).one()
        assert w2.sanitizer_version == 2
        assert w2.sanitized_payload_hash == raws[1].sanitized_payload_hash

        # 同一来源记录不因 revision 增加而虚增 occurrences
        m = s.exec(select(Material)).one()
        assert m.occurrences == 1
        assert len(s.exec(select(MaterialExtract)).all()) == 2
