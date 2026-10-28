# -*- coding: utf-8 -*-
"""生产回填演练：在 taskhub.db 副本上跑两遍 30 天回填，核对业务表哈希与幂等。

不写生产库。用法：
  python scripts/materials_rehearsal.py --days 30
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from mio_taskhub.materials.adapters import ADAPTERS, run_adapter, source_health  # noqa: E402
from mio_taskhub.materials.bootstrap import init_materials_db  # noqa: E402
from mio_taskhub.materials.extract import extract_for_source  # noqa: E402
from mio_taskhub.materials.models import (  # noqa: E402
    Material, MaterialExtract, MaterialLink, MaterialRaw, MaterialSource,
    MaterialWitness,
)
from mio_taskhub.materials.pipeline import ingest_records  # noqa: E402
from scripts.materials_audit import run_audit  # noqa: E402

BUSINESS_TABLES = [
    "task", "run", "event", "historyevent", "discussion",
    "tasktemplate", "scheduledjob",
]
MATERIAL_TABLES = [
    "material_source", "material_raw", "material", "material_witness",
    "material_extract", "material_link", "material_source_audit",
]
CHUNK = 200


def _hash_table(conn: sqlite3.Connection, table: str):
    cols = [r[1] for r in conn.execute(f"pragma table_info('{table}')")]
    if not cols:
        return None
    h = hashlib.sha256()
    query = f"select {', '.join(chr(34)+c+chr(34) for c in cols)} from '{table}' order by rowid"
    for row in conn.execute(query):
        h.update(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
    return h.hexdigest()


def _snapshot(path: Path) -> Dict[str, Any]:
    with sqlite3.connect(path) as c:
        out = {}
        for t in BUSINESS_TABLES + MATERIAL_TABLES:
            try:
                rows = int(c.execute(f"select count(*) from '{t}'").fetchone()[0])
            except Exception:
                continue
            out[t] = {"rows": rows, "sha256": _hash_table(c, t)}
        return out


def _backup(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    s = sqlite3.connect(src)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        s.close()
        d.close()


def _copy_engine(path: Path):
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return eng


def _backfill(eng, days: int, root: str) -> Dict[str, Any]:
    totals = {"raw_new": 0, "raw_reused": 0, "accepted": 0,
              "rejected": 0, "materials_new": 0, "materials_merged": 0}
    per_source: Dict[str, Any] = {}
    for key in ADAPTERS:
        with Session(eng) as s:
            src = s.get(MaterialSource, key)
            if src is None:
                continue
            batch = run_adapter(key, s, days=days, cursor=None, limit=None, root=root)
        stats = {"records": len(batch.records)}
        for attr in ("raw_new", "raw_reused", "accepted", "rejected",
                     "materials_new", "materials_merged"):
            stats[attr] = 0
        total = len(batch.records)
        for i in range(0, total, CHUNK):
            chunk = batch.records[i:i + CHUNK]
            cursor = batch.cursor if i + CHUNK >= total else None
            st = ingest_records(
                key, chunk,
                lambda p, s: extract_for_source(s.key, p, s),
                cursor=cursor, target_engine=eng,
            )
            for attr in ("raw_new", "raw_reused", "accepted", "rejected",
                         "materials_new", "materials_merged"):
                v = getattr(st, attr)
                stats[attr] += v
                totals[attr] += v
        per_source[key] = stats
    return {"totals": totals, "per_source": per_source}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.path.expanduser("~/.mio_taskhub/taskhub.db"))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--workdir", default=os.path.expanduser(
        "~/.mio_taskhub/preflight"))
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    src = Path(args.db)
    if not src.exists():
        raise SystemExit(f"db not found: {src}")
    work = Path(args.workdir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    copy = work / f"rehearsal_{stamp}.db"
    _backup(src, copy)
    snapshot_at = datetime.now(timezone.utc).isoformat()
    sha = hashlib.sha256(copy.read_bytes()).hexdigest()

    eng = _copy_engine(copy)
    before = _snapshot(copy)
    init_materials_db(target_engine=eng)
    pass1 = _backfill(eng, args.days, args.root)
    after1 = _snapshot(copy)
    pass2 = _backfill(eng, args.days, args.root)
    after2 = _snapshot(copy)
    audit = run_audit(target_engine=eng)
    with Session(eng) as s:
        health = source_health(s)

    business_ok = all(
        before[t]["sha256"] == after2[t]["sha256"]
        for t in BUSINESS_TABLES if t in before
    )
    idempotent = all(
        after1[t]["rows"] == after2[t]["rows"]
        for t in MATERIAL_TABLES if t in after1
    )
    result = {
        "snapshot_at": snapshot_at,
        "source_db": str(src),
        "copy_db": str(copy),
        "copy_sha256": sha,
        "days": args.days,
        "business_unchanged": business_ok,
        "materials_idempotent": idempotent,
        "business_before": {t: before[t] for t in BUSINESS_TABLES if t in before},
        "business_after": {t: after2[t] for t in BUSINESS_TABLES if t in after2},
        "materials_after_pass1": {t: after1.get(t) for t in MATERIAL_TABLES},
        "materials_after_pass2": {t: after2.get(t) for t in MATERIAL_TABLES},
        "pass1": pass1,
        "pass2": pass2,
        "audit": audit,
        "health": health,
    }
    text_out = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.json_out:
        Path(args.json_out).write_text(text_out, encoding="utf-8")
    print(text_out)
    eng.dispose()
    return 0 if (business_ok and idempotent and audit.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
