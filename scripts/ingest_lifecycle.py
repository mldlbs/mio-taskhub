# -*- coding: utf-8 -*-
"""回填 agent_lifecycle 本地 jsonl → event 表（T0.7，幂等）。

真相源：$MIO_HOME/lifecycle/<pid>/<run_id>.jsonl（见
docs/taskhub/design-agent-lifecycle-instrumentation.md A1/A5）。
幂等键 (type, run_id, ts)：event 表已有同键则跳过 → 整目录重放不产生重复行。

用法：
    python scripts/ingest_lifecycle.py            # --dry-run 预览（默认）
    python scripts/ingest_lifecycle.py --dry-run
    python scripts/ingest_lifecycle.py --apply    # 落库
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path  # noqa: E402

from sqlmodel import Session, select  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from mio_taskhub.db import engine  # noqa: E402
from mio_taskhub.models import Event  # noqa: E402

TYPE_PREFIX = "agent_lifecycle."


def _lifecycle_root() -> Path:
    from mio_taskhub.observability.lifecycle import lifecycle_dir
    return lifecycle_dir()


def iter_entries(root: Path):
    """遍历所有 jsonl 行 → (source_path, entry|None, raw_error|None)。"""
    if not root.exists():
        return
    for path in sorted(root.rglob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            yield path, None, f"read error: {e}"
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                yield path, None, f"line {lineno}: invalid json"
                continue
            if not isinstance(entry, dict):
                yield path, None, f"line {lineno}: not an object"
                continue
            yield path, entry, None


def existing_keys(db: Session) -> set:
    """event 表中 agent_lifecycle.* 的幂等键集合 (type, run_id, ts)。"""
    keys = set()
    rows = db.exec(
        select(Event).where(Event.type.startswith(TYPE_PREFIX))
    ).all()
    for r in rows:
        try:
            payload = json.loads(r.payload or "{}")
        except json.JSONDecodeError:
            payload = {}
        keys.add((r.type, r.run_id or payload.get("run_id", ""),
                  str(payload.get("ts", ""))))
    return keys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ingest agent_lifecycle jsonl → event（幂等）")
    ap.add_argument("--apply", action="store_true", help="落库（默认 dry-run 预览）")
    ap.add_argument("--dry-run", action="store_true", help="预览不写（默认行为）")
    args = ap.parse_args(argv)
    apply = bool(args.apply)

    root = _lifecycle_root()
    files = list(root.rglob("*.jsonl")) if root.exists() else []
    if not files:
        print(f"[ingest_lifecycle] no files under {root}")
        return 0

    SQLModel.metadata.create_all(engine)  # 空库时保证 event 表存在（幂等）

    total = new = dup = bad = 0
    with Session(engine) as db:
        keys = existing_keys(db)
        for path, entry, err in iter_entries(root):
            if err is not None:
                bad += 1
                print(f"  skip {path.name}: {err}")
                continue
            total += 1
            etype = str(entry.get("type", ""))
            run_id = str(entry.get("run_id", ""))
            ts = str(entry.get("ts", ""))
            if not etype.startswith(TYPE_PREFIX):
                bad += 1
                continue
            key = (etype, run_id, ts)
            if key in keys:
                dup += 1
                continue
            keys.add(key)
            new += 1
            if apply:
                db.add(Event(
                    type=etype, entity="run", entity_id=run_id, run_id=run_id,
                    payload=json.dumps(entry, ensure_ascii=False),
                ))
        if apply and new:
            db.commit()

    mode = "apply" if apply else "dry-run"
    print(f"[ingest_lifecycle] {mode}: files={len(files)} entries={total} "
          f"new={new} dup_skipped={dup} bad={bad} root={root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
