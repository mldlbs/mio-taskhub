# -*- coding: utf-8 -*-
"""非实时、幂等地把本地 lifecycle_probe jsonl 导入 Hub event 表（task 5b4fa957）。

设计要点（docs/taskhub/design-agent-lifecycle-instrumentation.md §4.3 / R2-D3）：
- **非实时**：worker 短命，无法自补传；由本脚本（人工或 Hub 启动）触发。
- **幂等**：键 = run_id + event_type + ts；重复执行不产生重复事件。
- **只读本地 → 写 Hub**：不改任何调度/门控逻辑。
- 不新增常驻守护进程。

用法：
    python scripts/ingest_lifecycle.py [--probe-dir DIR] [--dry-run]
"""
import argparse
import glob
import json
import os
import sqlite3
import sys

NS = "agent_lifecycle."


def probe_dir_default() -> str:
    return os.path.join(os.path.expanduser("~"), ".mio_taskhub", "lifecycle_probe")


def db_path_default() -> str:
    return os.path.join(os.path.expanduser("~"), ".mio_taskhub", "taskhub.db")


def read_records(probe_dir: str):
    """读所有 pid jsonl，产出 (source_file, line_no, record) —— 坏行跳过。"""
    for fp in sorted(glob.glob(os.path.join(probe_dir, "*.jsonl"))):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield fp, i, json.loads(line)
                    except Exception:  # noqa: BLE001 —— 坏行跳过
                        continue
        except Exception:  # noqa: BLE001
            continue


def idem_key(rec: dict) -> str:
    return "%s|%s|%s" % (rec.get("run_id", ""), rec.get("event_type", ""), rec.get("ts", ""))


def existing_keys(conn: sqlite3.Connection) -> set:
    """已导入的幂等键集合：以 event 表 payload 里的 _probe_key 为准。"""
    keys = set()
    try:
        for (payload,) in conn.execute(
                "SELECT payload FROM event WHERE type LIKE ?", (NS + "%",)):
            try:
                d = json.loads(payload) if payload else {}
                if d.get("_probe_key"):
                    keys.add(d["_probe_key"])
            except Exception:  # noqa: BLE001
                continue
    except sqlite3.OperationalError:
        pass
    return keys


def ingest(probe_dir: str, db_path: str, dry_run: bool = False) -> dict:
    """导入。返回统计。幂等：已存在的 _probe_key 不再写入。"""
    stats = {"scanned": 0, "imported": 0, "skipped_dup": 0, "bad": 0}
    if not os.path.isdir(probe_dir):
        return stats

    conn = None
    have = set()
    if not dry_run:
        conn = sqlite3.connect(db_path)
        have = existing_keys(conn)

    try:
        for fp, ln, rec in read_records(probe_dir):
            stats["scanned"] += 1
            etype = rec.get("event_type", "")
            if not etype.startswith(NS):
                stats["bad"] += 1
                continue
            key = idem_key(rec)
            if key in have:
                stats["skipped_dup"] += 1
                continue
            if dry_run:
                stats["imported"] += 1
                have.add(key)
                continue
            payload = dict(rec)
            payload["_probe_key"] = key
            payload["_probe_source"] = os.path.basename(fp)
            try:
                conn.execute(
                    "INSERT INTO event (type, entity, entity_id, run_id, payload, at) "
                    "VALUES (?,?,?,?,?, datetime('now'))",
                    (etype, "run", rec.get("run_id", ""), rec.get("run_id", ""),
                     json.dumps(payload, ensure_ascii=False)),
                )
                have.add(key)
                stats["imported"] += 1
            except Exception:  # noqa: BLE001
                stats["bad"] += 1
        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="幂等导入 lifecycle_probe → Hub event")
    ap.add_argument("--probe-dir", default=probe_dir_default())
    ap.add_argument("--db", default=db_path_default())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = ingest(args.probe_dir, args.db, dry_run=args.dry_run)
    print("[ingest_lifecycle] probe_dir=%s dry_run=%s" % (args.probe_dir, args.dry_run))
    print("[ingest_lifecycle] scanned=%d imported=%d skipped_dup=%d bad=%d" %
          (s["scanned"], s["imported"], s["skipped_dup"], s["bad"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
