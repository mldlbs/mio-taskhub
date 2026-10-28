# -*- coding: utf-8 -*-
"""P3 一次性回填：读取来源窗口并入库。

用法：
  python scripts/materials_backfill.py --days 30
  python scripts/materials_backfill.py --days 30 --source hn --source github-trending
  python scripts/materials_backfill.py --days 30 --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlmodel import Session  # noqa: E402

from mio_taskhub.db import engine, init_db  # noqa: E402
from mio_taskhub.materials.adapters import ADAPTERS, run_adapter, source_health  # noqa: E402
from mio_taskhub.materials.extract import extract_for_source  # noqa: E402
from mio_taskhub.materials.models import MaterialSource  # noqa: E402
from mio_taskhub.materials.pipeline import ingest_records  # noqa: E402
from mio_taskhub.utils import _now  # noqa: E402

CHUNK = 200


def _extractor(payload, source):
    return extract_for_source(source.key, payload, source)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--source", action="append", default=None,
                        help="可重复；默认全部已适配来源")
    parser.add_argument("--limit", type=int, default=0,
                        help="每个来源最多处理多少条；0 表示不限制")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", default=None,
                        help="文档路径根目录；默认 task.workspace")
    args = parser.parse_args()
    init_db()

    keys = args.source or list(ADAPTERS.keys())
    failed = 0
    for key in keys:
        try:
            with Session(engine) as s:
                src = s.get(MaterialSource, key)
                if src is None:
                    print(f"[skip] {key}: source not registered")
                    continue
                batch = run_adapter(
                    key, s, days=args.days, cursor=None,
                    limit=(args.limit or None), root=args.root,
                )
            print(f"[read] {key}: {len(batch.records)} records")
            if args.dry_run or not batch.records:
                continue
            total = len(batch.records)
            stats_all = []
            for i in range(0, total, CHUNK):
                chunk = batch.records[i:i + CHUNK]
                cursor = batch.cursor if i + CHUNK >= total else None
                stats = ingest_records(key, chunk, _extractor, cursor=cursor)
                stats_all.append(stats)
            accepted = sum(x.accepted for x in stats_all)
            rejected = sum(x.rejected for x in stats_all)
            new = sum(x.materials_new for x in stats_all)
            merged = sum(x.materials_merged for x in stats_all)
            with Session(engine) as s:
                src = s.get(MaterialSource, key)
                src.last_run_at = _now()
                src.last_status = "ok"
                src.last_error = None
                if batch.cursor:
                    src.cursor = batch.cursor
                s.commit()
            print(f"[done] {key}: accepted={accepted} rejected={rejected} "
                  f"materials_new={new} materials_merged={merged}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[error] {key}: {exc}")

    with Session(engine) as s:
        for row in source_health(s):
            print(f"[health] {row['key']}: raw={row['raw_total']} "
                  f"materials={row['materials']} status={row['by_status']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
