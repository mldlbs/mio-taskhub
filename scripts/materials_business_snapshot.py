# -*- coding: utf-8 -*-
"""业务表快照：行数 + 内容哈希；用于回填前后差异对比。

用法：python scripts/materials_business_snapshot.py --db <db> --out <json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

BUSINESS = [
    "task", "run", "event", "historyevent", "discussion",
    "tasktemplate", "scheduledjob",
]
MATERIAL = [
    "material_source", "material_raw", "material", "material_witness",
    "material_extract", "material_link", "material_source_audit",
]


def _hash_table(conn: sqlite3.Connection, table: str):
    cols = [r[1] for r in conn.execute(f"pragma table_info('{table}')")]
    if not cols:
        return None
    h = hashlib.sha256()
    query = f"select {', '.join(chr(34) + c + chr(34) for c in cols)} from '{table}' order by rowid"
    for row in conn.execute(query):
        h.update(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    db = Path(args.db)
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as c:
        tables = {r[0] for r in c.execute(
            "select name from sqlite_master where type='table'")}
        out = {"db": str(db), "tables": {}}
        for t in BUSINESS + MATERIAL:
            if t not in tables:
                continue
            rows = int(c.execute(f"select count(*) from '{t}'").fetchone()[0])
            out["tables"][t] = {"rows": rows, "sha256": _hash_table(c, t)}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
