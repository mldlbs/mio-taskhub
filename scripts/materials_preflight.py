# -*- coding: utf-8 -*-
"""生产回填前只读预检：不改库、不跑迁移、不建表。

用法：
  python scripts/materials_preflight.py
  python scripts/materials_preflight.py --db ~/.mio_taskhub/taskhub.db --json-out preflight.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUSINESS_TABLES = [
    "task", "run", "event", "historyevent", "discussion",
    "tasktemplate", "scheduledjob",
]
MATERIAL_TABLES = [
    "material_source", "material_raw", "material", "material_witness",
    "material_extract", "material_link", "material_source_audit",
]


def _ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=3.0)


def _tables(conn: sqlite3.Connection) -> List[str]:
    return [r[0] for r in conn.execute(
        "select name from sqlite_master where type='table' order by 1")]


def _columns(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    return [
        {"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
        for r in conn.execute(f"pragma table_info('{table}')")
    ]


def _count(conn: sqlite3.Connection, table: str) -> Optional[int]:
    try:
        return int(conn.execute(f"select count(*) from '{table}'").fetchone()[0])
    except Exception:
        return None


def _hash_table(conn: sqlite3.Connection, table: str) -> Optional[str]:
    try:
        cols = [r[1] for r in conn.execute(f"pragma table_info('{table}')")]
        if not cols:
            return None
        h = hashlib.sha256()
        query = f"select {', '.join('\"' + c + '\"' for c in cols)} from '{table}' order by rowid"
        for row in conn.execute(query):
            h.update(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
        return h.hexdigest()
    except Exception:
        return None


def _process_info() -> List[Dict[str, Any]]:
    if os.name != "nt":
        return []
    cmd = (
        "Get-Process TaskHub -ErrorAction SilentlyContinue | "
        "Select-Object Id,StartTime,Path | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=10,
        )
        raw = (r.stdout or "").strip()
        if not raw:
            return []
        obj = json.loads(raw)
        return obj if isinstance(obj, list) else [obj]
    except Exception:
        return []


def _backups(path: Path, limit: int = 5) -> List[Dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    files = sorted(
        [p for p in path.glob("*.db") if p.is_file()],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )[:limit]
    for p in files:
        row = {
            "name": p.name,
            "size": p.stat().st_size,
            "mtime": datetime.fromtimestamp(
                p.stat().st_mtime, timezone.utc).isoformat(),
            "quick_check": None,
        }
        try:
            with _ro(p) as c:
                row["quick_check"] = c.execute("pragma quick_check").fetchone()[0]
        except Exception as exc:
            row["quick_check"] = f"error: {exc}"
        out.append(row)
    return out


def preflight(db_path: Path, backup_dir: Path) -> Dict[str, Any]:
    if not db_path.exists():
        raise SystemExit(f"db not found: {db_path}")
    stat = db_path.stat()
    result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "db_size": stat.st_size,
        "db_mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "wal_size": db_path.with_suffix(db_path.suffix + "-wal").stat().st_size
        if db_path.with_suffix(db_path.suffix + "-wal").exists() else 0,
        "shm_size": db_path.with_suffix(db_path.suffix + "-shm").stat().st_size
        if db_path.with_suffix(db_path.suffix + "-shm").exists() else 0,
        "disk": None,
        "journal_mode": None,
        "quick_check": None,
        "tables": [],
        "business": {},
        "materials": {},
        "task_updated_at": False,
        "backups": _backups(backup_dir),
        "processes": _process_info(),
        "blockers": [],
    }
    try:
        usage = shutil.disk_usage(db_path.parent)
        result["disk"] = {
            "total": usage.total, "used": usage.used, "free": usage.free,
        }
    except Exception:
        pass

    with _ro(db_path) as c:
        result["journal_mode"] = c.execute("pragma journal_mode").fetchone()[0]
        result["quick_check"] = c.execute("pragma quick_check").fetchone()[0]
        tables = _tables(c)
        result["tables"] = tables
        for t in BUSINESS_TABLES:
            if t in tables:
                result["business"][t] = {
                    "rows": _count(c, t),
                    "columns": _columns(c, t),
                    "sha256": _hash_table(c, t),
                }
        for t in MATERIAL_TABLES:
            if t in tables:
                result["materials"][t] = {"rows": _count(c, t)}
        task_cols = {x["name"] for x in result["business"].get("task", {}).get("columns", [])}
        result["task_updated_at"] = "updated_at" in task_cols
        if not result["task_updated_at"]:
            result["blockers"].append(
                "task.updated_at 不存在：当前工作区 models.py 已新增该字段，"
                "新 exe 直接启动会因 ORM select 缺列而失败；需先由该改动补迁移。"
            )
        if result["materials"]:
            result["blockers"].append(
                "material_* 表已存在：回填前需确认是否重复执行/清理边界。"
            )
        if result["quick_check"] != "ok":
            result["blockers"].append("生产库 quick_check 未通过。")
        if not result["backups"]:
            result["blockers"].append("未找到可用备份。")
        else:
            if result["backups"][0]["quick_check"] != "ok":
                result["blockers"].append("最近备份 quick_check 未通过。")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.path.expanduser("~/.mio_taskhub/taskhub.db"))
    parser.add_argument("--backups", default=os.path.expanduser("~/.mio_taskhub/backups"))
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()
    result = preflight(Path(args.db), Path(args.backups))
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
