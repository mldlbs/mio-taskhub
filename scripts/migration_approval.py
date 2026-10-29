# -*- coding: utf-8 -*-
"""迁移审批服务：服务端复核目标库哈希与代码 commit，写入签名审批。

只有处于 review 阶段的任务才能被批准；artifacts 由服务端生成并 HMAC 签名，
调用方无法只靠声明字符串通过流程校验。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mio_taskhub.dbhash import content_hash  # noqa: E402

SCOPE = "scope:task.updated_at"
SCOPE_VALUE = "task.updated_at"


def _commit() -> str:
    env = os.environ.get("MIO_CODE_COMMIT", "").strip()
    if env:
        return env
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True,
                          timeout=10).stdout.strip()


def _sign(secret: str, payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), blob, hashlib.sha256).hexdigest()


def verify(secret: str, artifacts: list, db_hash: str, commit: str,
           task_id: str, now: Optional[datetime] = None) -> dict:
    data = {}
    for item in artifacts or []:
        text = str(item)
        if ":" in text:
            key, value = text.split(":", 1)
            data[key.strip()] = value.strip()
    required = {"scope", "db_content_hash", "code_commit", "expires_at",
                "signature", "db_path"}
    if not required <= set(data):
        return {"ok": False, "error": "artifacts_incomplete",
                "missing": sorted(required - set(data))}
    if data["scope"] != SCOPE_VALUE:
        return {"ok": False, "error": "scope_mismatch"}
    if data["db_content_hash"] != db_hash:
        return {"ok": False, "error": "db_hash_mismatch"}
    if data["code_commit"] != commit:
        return {"ok": False, "error": "code_commit_mismatch"}
    try:
        expires = datetime.fromisoformat(data["expires_at"])
    except ValueError:
        return {"ok": False, "error": "expires_at_invalid"}
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= (now or datetime.now(timezone.utc)):
        return {"ok": False, "error": "approval_expired"}
    payload = {
        "task_id": task_id,
        "scope": data["scope"],
        "db_content_hash": data["db_content_hash"],
        "code_commit": data["code_commit"],
        "expires_at": data["expires_at"],
        "db_path": data["db_path"],
    }
    expected = _sign(secret, payload)
    if not hmac.compare_digest(expected, data["signature"]):
        return {"ok": False, "error": "signature_invalid"}
    return {"ok": True, "expires_at": data["expires_at"],
            "db_path": data["db_path"]}


def approve(taskhub_db: Path, task_id: str, target_db: Path, secret: str,
            ttl_hours: int = 24, reviewer: str = "ops") -> dict:
    if not taskhub_db.exists() or not target_db.exists():
        return {"ok": False, "error": "db_not_found"}
    conn = sqlite3.connect(taskhub_db)
    conn.row_factory = sqlite3.Row
    try:
        task = conn.execute(
            "select id, stage, review_started_at from task where id=?",
            (task_id,)).fetchone()
        if task is None:
            return {"ok": False, "error": "task_not_found"}
        if str(task["stage"]).upper() != "REVIEW":
            return {"ok": False, "error": "task_not_in_review",
                    "stage": task["stage"]}
        if not task["review_started_at"]:
            return {"ok": False, "error": "task_review_not_entered"}
        event = conn.execute(
            "select 1 from taskevent where task_id=? and event_type='stage_changed' "
            "and lower(to_stage)='review' limit 1", (task_id,)).fetchone()
        if event is None:
            return {"ok": False, "error": "missing_stage_transition_event"}
        existing = conn.execute(
            "select 1 from taskreview where task_id=? and decision='approve' "
            "and artifacts like '%scope:task.updated_at%' limit 1",
            (task_id,)).fetchone()
        if existing is not None:
            return {"ok": False, "error": "duplicate_approval"}
        db_hash = content_hash(target_db)
        commit = _commit()
        expires = (datetime.now(timezone.utc)
                   + timedelta(hours=ttl_hours)).isoformat()
        artifacts = [
            SCOPE,
            f"db_path:{target_db.resolve()}",
            f"db_content_hash:{db_hash}",
            f"code_commit:{commit}",
            f"expires_at:{expires}",
        ]
        payload = {
            "task_id": task_id,
            "scope": SCOPE_VALUE,
            "db_content_hash": db_hash,
            "code_commit": commit,
            "expires_at": expires,
            "db_path": str(target_db.resolve()),
        }
        artifacts.append(f"signature:{_sign(secret, payload)}")
        review_id = uuid.uuid4().hex[:12]
        conn.execute(
            "insert into taskreview (id, task_id, decision, checklist, summary, "
            "comments, artifacts, reviewer, review_duration_sec, created_at) "
            "values (?,?,?,?,?,?,?,?,?,?)",
            (review_id, task_id, "approve", None,
             "Migration approval bound to target content hash and code commit.",
             "", json.dumps(artifacts, ensure_ascii=False), reviewer, None,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return {"ok": True, "review_id": review_id, "task_id": task_id,
                "db_content_hash": db_hash, "code_commit": commit,
                "expires_at": expires, "artifacts": artifacts}
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--taskhub-db", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--target-db", required=True)
    p.add_argument("--ttl-hours", type=int, default=24)
    p.add_argument("--reviewer", default="ops")
    args = p.parse_args()
    secret = os.environ.get("MIO_APPROVAL_SECRET", "")
    if not secret:
        print("MIO_APPROVAL_SECRET is required", file=sys.stderr)
        return 2
    result = approve(Path(args.taskhub_db), args.task_id,
                     Path(args.target_db), secret, args.ttl_hours,
                     args.reviewer)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
