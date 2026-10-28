# -*- coding: utf-8 -*-
"""T-CRASH：重建中途崩溃，重启后从最后成功批次续跑。"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.materials.models import (
    Material, MaterialExtract, MaterialLink, MaterialSource, MaterialWitness,
)
from mio_taskhub.materials.pipeline import (
    RawRecord, SimulatedCrash, add_link, ingest_records, rebuild_source,
)
from mio_taskhub.materials.quality import Draft


@pytest.fixture(autouse=True)
def _materials_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_MATERIALS_DIR", str(tmp_path / "materials"))


def _extractor(payload, source):
    return [Draft(kind="signal", quote=payload["content"], field="content",
                  evidence_tier="A")]


def _record(i):
    return RawRecord(
        source_key="hn", external_id=f"hn-crash-{i}",
        observed_at=datetime.now(timezone.utc),
        payload={"content": f"Evidence sentence number {i} is sufficiently long."},
    )


def test_rebuild_crash_resumes_from_last_committed_batch():
    records = [_record(i) for i in range(1, 5)]
    ingest_records("hn", records, _extractor)

    with Session(engine) as s:
        first = s.exec(select(Material).order_by(Material.id)).first()
        add_link(s, first.id, "task", "task-crash", "context", created_by="test")
        s.commit()

    with pytest.raises(SimulatedCrash):
        rebuild_source("hn", _extractor, to_version=2, to_config_hash="v2",
                       batch_size=2, crash_after_batches=1)

    with Session(engine) as s:
        src = s.get(MaterialSource, "hn")
        assert src.rebuild_state == "running"
        assert src.rebuild_resumed_count == 0
        cursor = src.rebuild_cursor
        assert cursor and '"last_raw_id"' in cursor
        # 模拟锁过期
        src.lock_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.commit()

    result = rebuild_source("hn", _extractor, to_version=2,
                            to_config_hash="v2", batch_size=2, resume=True)

    with Session(engine) as s:
        src = s.get(MaterialSource, "hn")
        assert src.rebuild_state == "done"
        assert src.rebuild_resumed_count == 1
        assert result["batches"] == 1

        materials = s.exec(select(Material)).all()
        assert len(materials) == 4
        # 4 条 v1 + 4 条 v2
        assert len(s.exec(select(MaterialExtract)).all()) == 8
        assert len(s.exec(select(MaterialWitness)).all()) == 4
        assert len(s.exec(select(MaterialLink)).all()) == 1
        assert all(m.occurrences == 1 for m in materials)
