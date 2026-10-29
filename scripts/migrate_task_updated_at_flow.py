# -*- coding: utf-8 -*-
"""task.updated_at 迁移的端到端自动化流程（预检 → 授权 → 迁移 → 验证 → 失败回滚）。

授权走既有 taskhub 评审状态机：要求指定任务存在一条 approve 评审，
且 artifacts 同时包含 scope / db_content_hash / code_commit，防止复用旧审批。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrate_task_updated_at import (  # noqa: E402
    TRIGGER_INSERT, TRIGGER_UPDATE, apply as apply_migration,
    rollback as rollback_migration,
)

EXCLUDE_TABLE = "task"
REQUIRED_TRIGGERS = {
    "task_updated_at_after_insert": TRIGGER_INSERT,
    "task_updated_at_after_update": TRIGGER_UPDATE,
}
APPROVAL_SCOPE = "scope:task.updated_at"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_hash(db: Path) -> str:
    """逻辑内容哈希：跨 sqlite backup 稳定，用于审批绑定。"""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    tables = sorted(r[0] for r in conn.execute(
        "select name from sqlite_master where type='table' "
        "and name not like 'sqlite_%'"))
    h = hashlib.sha256()
    for table in tables:
        cols = _columns(conn, table)
        h.update(table.encode("utf-8"))
        h.update(repr(cols).encode("utf-8"))
        try:
            rows = conn.execute(f"select * from {table} order by rowid").fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(f"select * from {table} order by 1").fetchall()
        for row in rows:
            h.update(repr(tuple(row)).encode("utf-8"))
    conn.close()
    return h.hexdigest()


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f"pragma table_info({table})")]


def _triggers(conn: sqlite3.Connection) -> Dict[str, str]:
    return {r[0]: r[1] for r in conn.execute(
        "select name, sql from sqlite_master where type='trigger' "
        "and name like 'task_updated_at_%'")}


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql or "").strip().lower()


def _trigger_ok(name: str, sql: str) -> bool:
    text = _norm(sql)
    if "after_insert" in name:
        return all(part in text for part in (
            "after insert on task", "new.updated_at is null",
            "coalesce(new.last_transition_at"))
    return all(part in text for part in (
        "after update of last_transition_at on task", "new.last_transition_at"))


def _table_hashes(db: Path, exclude: str = EXCLUDE_TABLE) -> Dict[str, str]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    names = [r[0] for r in conn.execute(
        "select name from sqlite_master where type='table' "
        "and name not like 'sqlite_%'")]
    out = {}
    for name in names:
        if name == exclude:
            continue
        rows = conn.execute(f"select * from {name} order by 1").fetchall()
        out[name] = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
    conn.close()
    return out


def _row_stats(conn: sqlite3.Connection) -> dict:
    count = conn.execute("select count(*) from task").fetchone()[0]
    cols = _columns(conn, "task")
    stats = {"rows": count}
    for col in ("state", "stage", "created_at"):
        stats[f"{col}_null"] = (conn.execute(
            f"select count(*) from task where {col} is null").fetchone()[0]
            if col in cols else None)
    stats["updated_at_null"] = (conn.execute(
        "select count(*) from task where updated_at is null").fetchone()[0]
        if "updated_at" in cols else None)
    return stats


def _head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, timeout=10).stdout.strip()


def _fetch_reviews(hub: str, task_id: str) -> list:
    url = f"{hub.rstrip('/')}/api/v1/tasks/{task_id}/reviews"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def approval_check(hub: Optional[str], task_id: Optional[str],
                   db_hash: str, commit: str) -> dict:
    if not hub or not task_id:
        return {"ok": False, "error": "approval_task_required"}
    try:
        reviews = _fetch_reviews(hub, task_id)
    except Exception as exc:
        return {"ok": False, "error": f"approval_fetch_failed:{exc}"}
    required = {APPROVAL_SCOPE, f"db_content_hash:{db_hash}", f"code_commit:{commit}"}
    for review in reviews:
        if str(review.get("decision", "")).lower() != "approve":
            continue
        artifacts = {str(a) for a in (review.get("artifacts") or [])}
        if required <= artifacts:
            return {"ok": True, "review_id": review.get("id"),
                    "reviewer": review.get("reviewer"),
                    "artifacts": sorted(required)}
    return {"ok": False, "error": "no_matching_approval",
            "required": sorted(required)}


def app_read(db: Path) -> Tuple[bool, str]:
    code = (
        "import json, os;"
        "from sqlmodel import Session, select;"
        "from mio_taskhub.db import engine;"
        "from mio_taskhub.models import Task;"
        "s=Session(engine);print(json.dumps({'rows':len(s.exec(select(Task)).all())}));"
        "s.close()"
    )
    env = os.environ.copy()
    env["MIO_TASKHUB_DB"] = str(db)
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout)[-500:]
    return True, proc.stdout.strip()


def run_flow(db: Path, expect_content_hash: str, backup: Path, hub: str,
             approval_task: str, app_read_check: bool = True) -> dict:
    checks: List[dict] = []

    def add(name: str, ok: bool, detail=None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if not db.exists():
        return {"ok": False, "stage": "preflight",
                "checks": [{"name": "db_exists", "ok": False}]}
    actual_hash = _content_hash(db)
    add("db_identity", actual_hash == expect_content_hash,
        {"expected": expect_content_hash, "actual": actual_hash})
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    tables = [r[0] for r in conn.execute(
        "select name from sqlite_master where type='table'")]
    add("schema_present", "task" in tables)
    if "task" not in tables:
        conn.close()
        return {"ok": False, "stage": "preflight", "checks": checks,
                "db_content_hash": actual_hash}
    integrity = conn.execute("pragma integrity_check").fetchone()[0]
    add("integrity", integrity == "ok", integrity)
    before_cols = _columns(conn, "task")
    before_stats = _row_stats(conn)
    before_triggers = _triggers(conn)
    conn.close()

    already = ("updated_at" in before_cols
               and before_stats.get("updated_at_null") == 0
               and set(REQUIRED_TRIGGERS) <= set(before_triggers))
    if not all(c["ok"] for c in checks):
        return {"ok": False, "stage": "preflight", "checks": checks,
                "db_content_hash": actual_hash}

    approval_hash = actual_hash
    if already:
        if not backup.exists():
            return {"ok": False, "stage": "approval", "checks": checks,
                    "error": "already_applied_without_backup",
                    "db_content_hash": actual_hash}
        approval_hash = _content_hash(backup)
    approval = approval_check(hub, approval_task, approval_hash, _head())
    add("approval", approval["ok"], approval)
    if not approval["ok"]:
        return {"ok": False, "stage": "approval", "checks": checks,
                "approval": approval, "db_content_hash": actual_hash}

    before_hashes = _table_hashes(db)
    if already:
        return {"ok": True, "stage": "already_applied", "checks": checks,
                "approval": approval, "db_content_hash": actual_hash,
                "rows": before_stats["rows"]}

    result = apply_migration(db, backup)
    add("apply", result.get("ok", False), {
        "backup": result.get("backup"), "error": result.get("error")})
    if not result.get("ok"):
        rolled = {"ok": False}
        if backup.exists():
            rolled = rollback_migration(db, backup)
        add("rollback_after_apply_failure", rolled.get("ok", False), rolled)
        return {"ok": False, "stage": "apply_failed", "checks": checks,
                "approval": approval, "db_content_hash": actual_hash,
                "backup": str(backup).replace("\\", "/"),
                "rolled_back": rolled.get("ok", False)}

    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    after_cols = _columns(conn, "task")
    after_stats = _row_stats(conn)
    after_triggers = _triggers(conn)
    after_integrity = conn.execute("pragma integrity_check").fetchone()[0]
    conn.close()
    add("schema_updated_at", "updated_at" in after_cols, after_cols[-3:])
    add("row_count_preserved", after_stats["rows"] == before_stats["rows"],
        {"before": before_stats["rows"], "after": after_stats["rows"]})
    add("updated_at_no_null", after_stats.get("updated_at_null") == 0,
        after_stats.get("updated_at_null"))
    add("integrity_after", after_integrity == "ok", after_integrity)
    add("trigger_insert", set(REQUIRED_TRIGGERS) <= set(after_triggers),
        sorted(after_triggers))
    trigger_ok = all(
        _trigger_ok(name, after_triggers.get(name, ""))
        for name in REQUIRED_TRIGGERS)
    add("trigger_definition", trigger_ok, sorted(after_triggers))
    after_hashes = _table_hashes(db)
    changed = sorted(k for k in before_hashes
                     if before_hashes[k] != after_hashes.get(k))
    add("non_target_tables", not changed, changed)
    if app_read_check:
        ok, detail = app_read(db)
        add("app_read", ok, detail)

    failed = [c for c in checks if not c["ok"]]
    report = {
        "ok": not failed,
        "stage": "verified" if not failed else "verify_failed",
        "checks": checks,
        "approval": approval,
        "db_content_hash": actual_hash,
        "code_commit": _head(),
        "backup": str(backup).replace("\\", "/"),
        "rolled_back": False,
    }
    if failed:
        rolled = rollback_migration(db, backup)
        add("rollback", rolled.get("ok", False), rolled)
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        cols = _columns(conn, "task")
        rows = conn.execute("select count(*) from task").fetchone()[0]
        conn.close()
        add("rollback_schema", "updated_at" not in cols, cols[-2:])
        add("rollback_rows", rows == before_stats["rows"], rows)
        report["ok"] = False
        report["rolled_back"] = rolled.get("ok", False)
        report["stage"] = "rolled_back" if rolled.get("ok") else "rollback_failed"
    return report


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--expect-content-hash", required=True)
    p.add_argument("--backup", required=True)
    p.add_argument("--approval-task", required=True)
    p.add_argument("--hub", default="http://127.0.0.1:48620")
    p.add_argument("--report", default=None)
    args = p.parse_args()
    report = run_flow(Path(args.db), args.expect_content_hash, Path(args.backup),
                      args.hub, args.approval_task, app_read_check=True)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "checks"},
                     ensure_ascii=False, indent=2))
    for check in report.get("checks", []):
        print(("PASS " if check["ok"] else "FAIL ") + check["name"])
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
