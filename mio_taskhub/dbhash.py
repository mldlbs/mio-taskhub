# -*- coding: utf-8 -*-
"""数据库逻辑内容哈希（跨 sqlite backup 稳定）。"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


def _columns(conn: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in conn.execute(f"pragma table_info({table})")]


def content_hash(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = sorted(r[0] for r in conn.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%' and name != 'taskreview'"))
        h = hashlib.sha256()
        for table in tables:
            h.update(table.encode("utf-8"))
            h.update(repr(_columns(conn, table)).encode("utf-8"))
            try:
                rows = conn.execute(f"select * from {table} order by rowid").fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(f"select * from {table} order by 1").fetchall()
            for row in rows:
                h.update(repr(tuple(row)).encode("utf-8"))
        return h.hexdigest()
    finally:
        conn.close()
