# -*- coding: utf-8 -*-
"""T-MIRROR：镜像源不虚增佐证，分组调整不回溯旧 witness。"""
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.materials.models import (
    Material, MaterialSource, MaterialSourceAudit, MaterialWitness,
)
from mio_taskhub.materials.pipeline import RawRecord, ingest_records
from mio_taskhub.materials.quality import Draft


@pytest.fixture(autouse=True)
def _materials_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_MATERIALS_DIR", str(tmp_path / "materials"))


def _extractor(payload, source):
    return [Draft(kind="signal", quote=payload["content"], field="content",
                  evidence_tier="A")]


def _record(source_key, external_id, content):
    return RawRecord(
        source_key=source_key, external_id=external_id,
        observed_at=datetime.now(timezone.utc),
        payload={"content": content},
    )


def _set_group(session, key, group, reason):
    src = session.get(MaterialSource, key)
    old = src.independence_group
    src.independence_group = group
    src.independence_version = (src.independence_version or 1) + 1
    session.add(MaterialSourceAudit(
        source_key=key, field="independence_group",
        old_value=old, new_value=group, actor="test", reason=reason,
    ))
    session.commit()


def test_mirror_counts_one_and_group_change_does_not_rewrite_history():
    quote = "OpenLDK brings the Java world into Common Lisp."
    with Session(engine) as s:
        _set_group(s, "hn", "mirror", "mirror pair")
        _set_group(s, "github-trending", "mirror", "mirror pair")

    ingest_records("hn", [_record("hn", "hn-m1", quote)], _extractor)
    ingest_records("github-trending",
                   [_record("github-trending", "gh-m1", quote)], _extractor)

    with Session(engine) as s:
        m = s.exec(select(Material)).one()
        assert m.occurrences == 2
        assert m.corroboration == 1
        witnesses = s.exec(select(MaterialWitness)).all()
        assert {w.independence_group for w in witnesses} == {"mirror"}

        # 调整 github 分组；旧 witness 快照不变，历史分数不回溯
        _set_group(s, "github-trending", "github-trending", "no longer mirror")
        s.refresh(m)
        assert m.corroboration == 1
        old = s.exec(
            select(MaterialWitness).where(MaterialWitness.external_id == "gh-m1")
        ).one()
        assert old.independence_group == "mirror"
        assert s.exec(select(MaterialSourceAudit)).all()

    # 新 witness 使用新分组，才允许 corroboration 增加
    ingest_records("github-trending",
                   [_record("github-trending", "gh-m2", quote)], _extractor)
    with Session(engine) as s:
        m = s.exec(select(Material)).one()
        assert m.corroboration == 2
        assert m.occurrences == 3
