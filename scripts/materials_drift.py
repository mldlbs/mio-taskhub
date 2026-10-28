# -*- coding: utf-8 -*-
"""两版素材库的分类漂移对比（按内容指纹 + 类型 + 状态）。

用于：
- 提取器版本对比（P5 vs P5.1）：漂移是变更说明，不是质量结论；
- 确定性复跑对比（同一版本两次回填）：漂移应为 0。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


def _load(db: Path) -> Dict[Tuple[str, str], dict]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select m.id, m.kind, m.quote, m.status, m.content_fingerprint, "
        "w.source_key from material m "
        "left join material_witness w on w.material_id=m.id and w.is_primary=1"
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        key = (str(r["content_fingerprint"]), str(r["kind"]))
        out[key] = dict(r)
    return out


def compare(baseline: Path, candidate: Path, examples: int = 8) -> dict:
    base = _load(baseline)
    cand = _load(candidate)
    base_keys, cand_keys = set(base), set(cand)

    base_by_fp = defaultdict(set)
    cand_by_fp = defaultdict(set)
    for fp, kind in base_keys:
        base_by_fp[fp].add(kind)
    for fp, kind in cand_keys:
        cand_by_fp[fp].add(kind)

    removed = sorted(set(base_by_fp) - set(cand_by_fp))
    added = sorted(set(cand_by_fp) - set(base_by_fp))
    kind_changed = []
    for fp in sorted(set(base_by_fp) & set(cand_by_fp)):
        if not (base_by_fp[fp] & cand_by_fp[fp]):
            kind_changed.append({
                "fp": fp,
                "from": sorted(base_by_fp[fp]),
                "to": sorted(cand_by_fp[fp]),
                "quote": base[(fp, sorted(base_by_fp[fp])[0])]["quote"],
            })

    status_changed = []
    for key in sorted(base_keys & cand_keys):
        if base[key]["status"] != cand[key]["status"]:
            status_changed.append({
                "fp": key[0], "kind": key[1],
                "from": base[key]["status"], "to": cand[key]["status"],
                "quote": base[key]["quote"],
            })

    union = len(set(base_by_fp) | set(cand_by_fp))
    changed = len(removed) + len(added) + len(kind_changed)
    return {
        "baseline": str(baseline).replace("\\", "/"),
        "candidate": str(candidate).replace("\\", "/"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "baseline": len(base_by_fp),
            "candidate": len(cand_by_fp),
            "removed": len(removed),
            "added": len(added),
            "kind_changed": len(kind_changed),
            "status_changed": len(status_changed),
            "union": union,
        },
        "drift_rate": round(changed / union, 4) if union else None,
        "removed_examples": [
            {"fp": fp, "kinds": sorted(base_by_fp[fp]),
             "quote": base[(fp, sorted(base_by_fp[fp])[0])]["quote"][:160]}
            for fp in removed[:examples]
        ],
        "added_examples": [
            {"fp": fp, "kinds": sorted(cand_by_fp[fp]),
             "quote": cand[(fp, sorted(cand_by_fp[fp])[0])]["quote"][:160]}
            for fp in added[:examples]
        ],
        "kind_changed_examples": kind_changed[:examples],
        "status_changed_examples": status_changed[:examples],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--examples", type=int, default=8)
    args = p.parse_args()
    report = compare(Path(args.baseline), Path(args.candidate), args.examples)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({
        "counts": report["counts"],
        "drift_rate": report["drift_rate"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
