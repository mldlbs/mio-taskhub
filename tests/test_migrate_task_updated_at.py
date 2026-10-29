# -*- coding: utf-8 -*-
"""task.updated_at 迁移：备份、回填、触发器、回滚。"""
import sqlite3
from pathlib import Path

from scripts.migrate_task_updated_at import apply, check, rollback

SCHEMA = (
    "create table task (id text primary key, state text, "
    "created_at datetime, last_transition_at datetime)"
)


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.execute("insert into task values ('t1','ready','2026-10-01 00:00:00',"
                 "'2026-10-02 00:00:00')")
    conn.execute("insert into task values ('t2','done','2026-10-03 00:00:00',null)")
    conn.commit()
    return conn


def test_apply_backfills_and_adds_triggers(tmp_path):
    db = tmp_path / "taskhub.db"
    _db(db).close()

    result = apply(db)
    assert result["ok"] is True
    assert result["applied"] is True
    assert Path(result["backup"]).exists()
    assert result["after"]["has_updated_at"] is True
    assert result["after"]["null_updated_at"] == 0
    assert "task_updated_at_after_insert" in result["after"]["triggers"]
    assert "task_updated_at_after_update" in result["after"]["triggers"]

    conn = sqlite3.connect(db)
    assert conn.execute("select updated_at from task where id='t1'").fetchone()[0] \
        == "2026-10-02 00:00:00"
    assert conn.execute("select updated_at from task where id='t2'").fetchone()[0] \
        == "2026-10-03 00:00:00"

    # 旧 exe 插入：不写 updated_at，触发器回填
    conn.execute("insert into task (id,state,created_at,last_transition_at) "
                 "values ('t3','ready','2026-10-04 00:00:00',null)")
    assert conn.execute("select updated_at from task where id='t3'").fetchone()[0] \
        == "2026-10-04 00:00:00"
    # 旧 exe 只更新 last_transition_at：触发器同步
    conn.execute("update task set last_transition_at='2026-10-05 00:00:00' "
                 "where id='t3'")
    assert conn.execute("select updated_at from task where id='t3'").fetchone()[0] \
        == "2026-10-05 00:00:00"
    conn.commit()
    conn.close()

    again = apply(db)
    assert again.get("already_applied") is True


def test_check_and_rollback(tmp_path):
    db = tmp_path / "taskhub.db"
    _db(db).close()
    before = check(db)
    assert before["ok"] is False
    assert before["has_updated_at"] is False

    result = apply(db)
    assert check(db)["ok"] is True
    rolled = rollback(db, Path(result["backup"]))
    assert rolled["ok"] is True
    after = check(db)
    assert after["has_updated_at"] is False

    conn = sqlite3.connect(db)
    assert conn.execute("select count(*) from task").fetchone()[0] == 2
    conn.close()


def test_missing_table_is_reported(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    result = check(db)
    assert result["ok"] is False
    assert result["error"] == "task_table_missing"
