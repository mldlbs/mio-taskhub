# -*- coding: utf-8 -*-
"""P4 抽样：按 kind × origin 分层，输出可追溯的标注模板（不写标签）。

用法：
  python scripts/materials_sample.py --db <rehearsal.db> \
    --out docs/taskhub/materials/calibration/sample-YYYYMMDD.jsonl \
    --manifest docs/taskhub/materials/calibration/manifest-YYYYMMDD.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KINDS = ["signal", "fact", "result", "problem", "decision", "lesson"]
ORIGINS = ["external", "internal", "task", "log"]


def allocate(counts: Dict[str, int], n: int) -> Dict[str, int]:
    """分层配额：先给每个非空层保底，再按剩余容量做水位均衡。"""
    total = sum(counts.values())
    if total <= n:
        return dict(counts)
    keys = [k for k, v in counts.items() if v > 0]
    if n < len(keys):
        chosen = sorted(keys, key=lambda k: (-counts[k], k))[:n]
        return {k: (1 if k in chosen else 0) for k in counts}

    if n >= 20 * len(keys):
        base = 20
    elif n >= 10 * len(keys):
        base = 10
    else:
        base = max(1, n // len(keys))

    out = {k: (min(counts[k], base) if k in keys else 0) for k in counts}
    remaining = n - sum(out.values())
    active = [k for k in keys if out[k] < counts[k]]
    while remaining > 0 and active:
        share = max(1, remaining // len(active))
        progressed = False
        for k in list(active):
            if remaining <= 0:
                break
            add = min(share, counts[k] - out[k], remaining)
            if add > 0:
                out[k] += add
                remaining -= add
                progressed = True
        active = [k for k in active if out[k] < counts[k]]
        if not progressed:
            break
    for k in keys:
        if remaining <= 0:
            break
        if out[k] < counts[k]:
            out[k] += 1
            remaining -= 1
    return out


def build_sample(db: Path, n: int, seed: int) -> tuple[List[dict], dict]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select m.id, m.kind, m.origin, m.status, m.quality, m.quote, "
        "m.observed_at, w.source_key "
        "from material m join material_witness w "
        "on w.material_id = m.id and w.is_primary = 1 "
        "where m.status in ('active','quarantine','stale')"
    ).fetchall()
    conn.close()

    by_status: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_status[r["status"]].append(dict(r))

    rng = random.Random(seed)

    # 隔离/过期样本全部纳入（数量很小），用于估计漏收；不足 n 时再补 active。
    q_rows = by_status["quarantine"] + by_status["stale"]
    if len(q_rows) <= n:
        q_selected = q_rows
    else:
        q_cells: Dict[str, List[dict]] = defaultdict(list)
        for r in q_rows:
            q_cells[f"{r['kind']}|{r['origin']}"].append(r)
        q_quotas = allocate({k: len(v) for k, v in q_cells.items()}, n)
        q_selected = []
        for cell, items in q_cells.items():
            items = sorted(items, key=lambda x: x["id"])
            q_selected.extend(rng.sample(items, min(q_quotas.get(cell, 0), len(items))))

    remaining = max(0, n - len(q_selected))
    active_rows = by_status["active"]
    active_cells: Dict[str, List[dict]] = defaultdict(list)
    for r in active_rows:
        active_cells[f"{r['kind']}|{r['origin']}"].append(r)
    active_counts = {k: len(v) for k, v in active_cells.items()}
    populated = [k for k, v in active_counts.items() if v > 0]
    if populated and remaining >= 20 * len(populated):
        minimum = 20
    elif populated and remaining >= 10 * len(populated):
        minimum = 10
    else:
        minimum = max(1, remaining // max(1, len(populated)))
    quotas = allocate(active_counts, remaining) if remaining else {}

    q_by_cell: Dict[str, List[dict]] = defaultdict(list)
    for r in q_selected:
        q_by_cell[f"{r['kind']}|{r['origin']}"].append(r)

    sampled: List[dict] = []
    per_cell = {}
    all_cells = sorted(set(
        [f"{k}|{o}" for k in KINDS for o in ORIGINS]
        + list(active_cells) + list(q_by_cell)
    ))
    for cell in all_cells:
        a_items = sorted(active_cells.get(cell, []), key=lambda x: x["id"])
        q_items = sorted(q_by_cell.get(cell, []), key=lambda x: x["id"])
        a_q = min(quotas.get(cell, 0), len(a_items)) if remaining else 0
        a_sel = rng.sample(a_items, a_q) if a_q else []
        sampled.extend(a_sel)
        sampled.extend(q_items)
        active_insufficient = (not a_items) or len(a_items) < minimum
        quarantine_insufficient = not q_items
        per_cell[cell] = {
            "active_eligible": len(a_items),
            "active_target": quotas.get(cell, 0),
            "active_sampled": a_q,
            "quarantine_eligible": len(q_items),
            "quarantine_sampled": len(q_items),
            "minimum": minimum if a_items else 0,
            "active_insufficient": active_insufficient,
            "quarantine_insufficient": quarantine_insufficient,
            "evidence_insufficient": active_insufficient,
        }

    sampled.sort(key=lambda x: (x["kind"], x["origin"], x["id"]))
    out = []
    for i, r in enumerate(sampled, 1):
        cell = f"{r['kind']}|{r['origin']}"
        out.append({
            "sample_id": i,
            "material_id": r["id"],
            "kind": r["kind"],
            "origin": r["origin"],
            "status": r["status"],
            "quality": r["quality"],
            "source_key": r["source_key"],
            "observed_at": r["observed_at"],
            "quote": r["quote"],
            "cell": cell,
            "active_insufficient": per_cell[cell]["active_insufficient"],
            "quarantine_insufficient": per_cell[cell]["quarantine_insufficient"],
            "label_kind": "",
            "label_usable": "",
            "label_duplicate_of": "",
            "label_notes": "",
        })
    manifest = {
        "version": 2,
        "sampling_design": "all quarantine/stale + balanced active",
        "source_db": str(db),
        "source_db_sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "requested": n,
        "eligible": len(rows),
        "sampled": len(out),
        "by_status": {
            "active": {"eligible": len(by_status["active"]),
                       "sampled": sum(1 for r in out if r["status"] == "active")},
            "quarantine": {"eligible": len(by_status["quarantine"]),
                           "sampled": sum(1 for r in out if r["status"] == "quarantine")},
            "stale": {"eligible": len(by_status["stale"]),
                      "sampled": sum(1 for r in out if r["status"] == "stale")},
        },
        "per_cell": per_cell,
        "insufficient_cells": [
            c for c, v in per_cell.items() if v["active_insufficient"]
        ],
        "miss_insufficient_cells": [
            c for c, v in per_cell.items() if v["quarantine_insufficient"]
        ],
        "labeling_note": "label_* 字段为空，需人工填写；本文件不是校准结论。",
    }
    return out, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"db not found: {db}")
    sample, manifest = build_sample(db, args.n, args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in sample:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    Path(args.manifest).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"sampled={len(sample)} eligible={manifest['eligible']} "
          f"insufficient_cells={len(manifest['insufficient_cells'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
