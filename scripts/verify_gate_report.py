# -*- coding: utf-8 -*-
"""校验门禁报告与当前代码/数据库绑定是否一致（防止报告与发布对象脱钩）。

检查：
- report.ok 为 true；
- report_fingerprint 未被篡改；
- binding.code_commit == 当前 HEAD（或 --commit）；
- 工作区干净（除非 --allow-dirty）；
- 被测 DB 的 sha256/size 与报告一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


def _fingerprint(report: dict) -> str:
    body = {k: v for k, v in report.items() if k != "report_fingerprint"}
    blob = json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return ""


def verify(report_path: Path, db: Optional[Path] = None,
           commit: Optional[str] = None, allow_dirty: bool = False) -> List[str]:
    problems: List[str] = []
    if not report_path.exists():
        return ["report_missing"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("ok") is not True:
        problems.append("gate_not_ok")
    if report.get("report_fingerprint") != _fingerprint(report):
        problems.append("report_fingerprint_mismatch")

    binding = report.get("binding") or {}
    current = commit or _head()
    if not current or binding.get("code_commit") != current:
        problems.append("code_commit_mismatch")
    if binding.get("code_dirty") and not allow_dirty:
        problems.append("code_dirty")

    target = db or (Path(binding["db_path"]) if binding.get("db_path") else None)
    if target is None or not target.exists():
        problems.append("db_missing")
    else:
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != binding.get("db_sha256"):
            problems.append("db_sha256_mismatch")
        if target.stat().st_size != binding.get("db_size"):
            problems.append("db_size_mismatch")
    return problems


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", required=True)
    p.add_argument("--db", default=None)
    p.add_argument("--commit", default=None)
    p.add_argument("--allow-dirty", action="store_true")
    args = p.parse_args()
    problems = verify(Path(args.report), Path(args.db) if args.db else None,
                      args.commit, args.allow_dirty)
    print(json.dumps({"ok": not problems, "problems": problems},
                     ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
