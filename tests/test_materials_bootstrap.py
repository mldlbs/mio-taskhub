# -*- coding: utf-8 -*-
"""生产回填前的 bootstrap：只建素材表、幂等、不碰业务表。"""
import sqlite3

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

import mio_taskhub.materials.pipeline as pipe
from mio_taskhub.db import engine
from mio_taskhub.materials.bootstrap import init_materials_db
from mio_taskhub.materials.models import MaterialSource
from mio_taskhub.materials.pipeline import IngestStats


def test_bootstrap_idempotent_on_global_engine():
    init_materials_db()
    init_materials_db()
    with Session(engine) as s:
        assert len(s.exec(select(MaterialSource)).all()) == 10


def test_bootstrap_does_not_touch_existing_task_table(tmp_path):
    db = tmp_path / "sentinel.db"
    with sqlite3.connect(db) as c:
        c.execute("create table task (id text primary key, title text)")
        c.execute("insert into task (id, title) values ('t1', 'existing')")
        c.commit()
    eng = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    with eng.connect() as c:
        before = c.execute(text("pragma table_info(task)")).all()
    init_materials_db(target_engine=eng)
    with eng.connect() as c:
        after = c.execute(text("pragma table_info(task)")).all()
        row = c.execute(text("select id, title from task")).all()
        tables = {r[0] for r in c.execute(
            text("select name from sqlite_master where type='table'")).all()}
    assert before == after
    assert row == [("t1", "existing")]
    assert "material_source" in tables
    assert "material" in tables


def test_ingest_retries_locked(monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise OperationalError("stmt", {}, Exception("database is locked"))
        return IngestStats()

    monkeypatch.setattr(pipe, "_ingest_records_once", fake)
    stats = pipe.ingest_records("hn", [], lambda p, s: [])
    assert stats.accepted == 0
    assert len(calls) == 3
