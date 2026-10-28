# -*- coding: utf-8 -*-
"""只读预检：不改库，能识别缺列与已存在的 material_* 表。"""
import shutil
import sqlite3

from scripts.materials_preflight import preflight


def _make_db(path, with_material=False):
    with sqlite3.connect(path) as c:
        c.execute("create table task (id text primary key, title text)")
        c.execute("insert into task values ('t1', 'x')")
        c.execute("create table run (id text primary key, task_id text)")
        if with_material:
            c.execute("create table material (id text primary key)")
        c.commit()


def test_preflight_read_only_flags(tmp_path):
    db = tmp_path / "taskhub.db"
    _make_db(db)
    backups = tmp_path / "backups"
    backups.mkdir()
    shutil.copy2(db, backups / "taskhub_20261028_000000.db")
    out = preflight(db, backups)
    assert out["quick_check"] == "ok"
    assert out["task_updated_at"] is False
    assert out["materials"] == {}
    assert any("updated_at" in b for b in out["blockers"])
    assert out["backups"][0]["quick_check"] == "ok"
    # 预检不应创建 material 表
    with sqlite3.connect(db) as c:
        names = {r[0] for r in c.execute(
            "select name from sqlite_master where type='table'")}
    assert "material" not in names


def test_preflight_flags_existing_material_tables(tmp_path):
    db = tmp_path / "taskhub.db"
    _make_db(db, with_material=True)
    backups = tmp_path / "backups"
    backups.mkdir()
    shutil.copy2(db, backups / "taskhub_20261028_000000.db")
    out = preflight(db, backups)
    assert "material" in out["materials"]
    assert any("material_*" in b for b in out["blockers"])
