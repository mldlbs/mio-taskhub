# -*- coding: utf-8 -*-
"""task.updated_at 独立迁移（先备份、可回滚、可验证）。

不触碰 material_* 表，也不改变素材门禁。
用法：
    python scripts/migrate_task_updated_at.py --db <db> --apply
    python scripts/migrate_task_updated_at.py --db <db> --verify
    python scripts/migrate_task_updated_at.py --db <db> --rollback <backup> --yes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

TRIGGER_INSERT = """
create trigger if not exists task_updated_at_after_insert
after insert on task
for each row
when new.updated_at is null
begin
  update task
     set updated_at = coalesce(new.last_transition_at, new.created_at,
                               current_timestamp)
   where id = new.id;
end
"""

TRIGGER_UPDATE = """
create trigger if not exists task_updated_at_after_update
after update of last_transition_at on task
for each row
when new.last_transition_at is not null
begin
  update task
     set updated_at = new.last_transition_at
   where id = new.id and (updated_at is null or updated_at != new.last_transition_at);
end
"""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in conn.execute(f"pragma table_info({table})")]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(db: Path) -> dict:
    if not db.exists():
        return {"ok": False, "error": "db_not_found", "db": str(db)}
    conn = _connect(db)
    try:
        tables = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'")]
        if "task" not in tables:
            return {"ok": False, "error": "task_table_missing", "db": str(db)}
        cols = _columns(conn, "task")
        rows = conn.execute("select count(*) from task").fetchone()[0]
        nulls = (conn.execute(
            "select count(*) from task where updated_at is null").fetchone()[0]
            if "updated_at" in cols else None)
        triggers = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='trigger' "
            "and name like 'task_updated_at_%'")]
        return {
            "ok": "updated_at" in cols and (nulls == 0),
            "db": str(db).replace("\\", "/"),
            "task_rows": rows,
            "has_updated_at": "updated_at" in cols,
            "null_updated_at": nulls,
            "triggers": sorted(triggers),
        }
    finally:
        conn.close()


def apply(db: Path, backup: Optional[Path] = None) -> dict:
    pre = check(db)
    if not pre.get("db"):
        return pre
    if pre.get("error"):
        return pre
    if pre["has_updated_at"] and pre["null_updated_at"] == 0:
        return {"ok": True, "already_applied": True, **pre}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup or db.with_name(f"{db.stem}.pre-updated-at-{stamp}.db")
    backup.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db)
    dst = sqlite3.connect(backup)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    bconn = _connect(backup)
    ok = bconn.execute("pragma integrity_check").fetchone()[0]
    bconn.close()
    if ok != "ok":
        return {"ok": False, "error": f"backup_integrity:{ok}", "backup": str(backup)}

    conn = _connect(db)
    try:
        cols = _columns(conn, "task")
        if "updated_at" not in cols:
            conn.execute("alter table task add column updated_at datetime")
        conn.execute(
            "update task set updated_at = coalesce(last_transition_at, created_at, "
            "current_timestamp) where updated_at is null")
        conn.execute(TRIGGER_INSERT)
        conn.execute(TRIGGER_UPDATE)
        conn.commit()
    finally:
        conn.close()

    post = check(db)
    return {
        "ok": bool(post.get("ok")),
        "applied": True,
        "backup": str(backup).replace("\\", "/"),
        "backup_sha256": _sha256(backup),
        "backup_integrity": "ok",
        "before": pre,
        "after": post,
    }


def rollback(db: Path, backup: Path) -> dict:
    if not backup.exists():
        return {"ok": False, "error": "backup_not_found", "backup": str(backup)}
    conn = _connect(backup)
    ok = conn.execute("pragma integrity_check").fetchone()[0]
    conn.close()
    if ok != "ok":
        return {"ok": False, "error": f"backup_integrity:{ok}"}
    shutil.copyfile(backup, db)
    return {"ok": True, "restored": True, "db": str(db).replace("\\", "/"),
            "backup": str(backup).replace("\\", "/")}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--rollback", default=None)
    p.add_argument("--backup", default=None)
    p.add_argument("--yes", action="store_true")
    args = p.parse_args()
    db = Path(args.db)

    if args.rollback:
        if not args.yes:
            print("rollback requires --yes", file=sys.stderr)
            return 2
        result = rollback(db, Path(args.rollback))
    elif args.apply:
        result = apply(db, Path(args.backup) if args.backup else None)
    else:
        result = check(db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
