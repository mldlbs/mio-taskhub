# -*- coding: utf-8 -*-
"""P4 校准分析：分层误收/漏收/分类准确率 + 阈值错位分析。

只读取 sample/labels/manifest，不修改阈值、不写素材库。
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def _load_jsonl(path: Path) -> List[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _rate(yes: int, n: int):
    return round(yes / n, 4) if n else None


def analyze(sample_path: Path, labels_path: Path, manifest_path: Path) -> dict:
    sample = {r["sample_id"]: r for r in _load_jsonl(sample_path)}
    labels = _load_jsonl(labels_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    per_cell: Dict[str, Dict[str, Any]] = {}
    for cell, m in manifest["per_cell"].items():
        rows = [r for r in labels if r["cell"] == cell]
        active = [r for r in rows if r["status"] == "active"]
        quarantine = [r for r in rows if r["status"] != "active"]
        active_yes = sum(1 for r in active if r["label_usable"] == "yes")
        active_no = len(active) - active_yes
        q_yes = sum(1 for r in quarantine if r["label_usable"] == "yes")
        kind_ok = sum(1 for r in rows if r["label_kind"] == r["kind"])
        active_kind_ok = sum(1 for r in active if r["label_kind"] == r["kind"])
        per_cell[cell] = {
            "active_eligible": m["active_eligible"],
            "active_sampled": len(active),
            "active_usable_yes": active_yes,
            "active_usable_no": active_no,
            "misaccept_rate": _rate(active_no, len(active)),
            "quarantine_eligible": m["quarantine_eligible"],
            "quarantine_sampled": len(quarantine),
            "quarantine_usable_yes": q_yes,
            "miss_rate": _rate(q_yes, len(quarantine)),
            "kind_accuracy": _rate(kind_ok, len(rows)),
            "active_kind_accuracy": _rate(active_kind_ok, len(active)),
            "active_insufficient": m["active_insufficient"],
            "miss_insufficient": m["quarantine_insufficient"],
        }

    covered = [c for c, v in per_cell.items() if v["active_sampled"]]
    active_eligible = sum(per_cell[c]["active_eligible"] for c in covered)
    est_usable = sum(
        per_cell[c]["active_eligible"] * per_cell[c]["active_usable_yes"]
        / per_cell[c]["active_sampled"]
        for c in covered
    )
    est_kind_ok = sum(
        per_cell[c]["active_eligible"] * (per_cell[c]["active_kind_accuracy"] or 0)
        for c in covered
    )
    active_rows = [r for r in labels if r["status"] == "active"]
    q_rows = [r for r in labels if r["status"] != "active"]
    overall = {
        "active_sampled": len(active_rows),
        "active_usable_yes": sum(1 for r in active_rows if r["label_usable"] == "yes"),
        "misaccept_unweighted": _rate(
            sum(1 for r in active_rows if r["label_usable"] == "no"),
            len(active_rows)),
        "quarantine_sampled": len(q_rows),
        "quarantine_usable_yes": sum(1 for r in q_rows if r["label_usable"] == "yes"),
        "miss_unweighted": _rate(
            sum(1 for r in q_rows if r["label_usable"] == "yes"), len(q_rows)),
        "kind_accuracy_unweighted": _rate(
            sum(1 for r in labels if r["label_kind"] == r["kind"]), len(labels)),
        "active_eligible_covered": active_eligible,
        "active_usable_rate_weighted": round(est_usable / active_eligible, 4)
        if active_eligible else None,
        "kind_accuracy_weighted": round(est_kind_ok / active_eligible, 4)
        if active_eligible else None,
    }

    bands = [(0.65, 0.70), (0.70, 0.75), (0.75, 0.80), (0.80, 0.85), (0.85, 1.01)]
    band_stats = []
    for lo, hi in bands:
        rows = [r for r in active_rows if lo <= r["quality"] < hi]
        band_stats.append({
            "band": f"{lo:.2f}-{hi:.2f}",
            "n": len(rows),
            "usable_yes": sum(1 for r in rows if r["label_usable"] == "yes"),
            "usable_rate": _rate(
                sum(1 for r in rows if r["label_usable"] == "yes"), len(rows)),
        })

    def _quality_stats(rows):
        vals = [r["quality"] for r in rows]
        return {
            "n": len(vals),
            "min": round(min(vals), 4) if vals else None,
            "median": round(statistics.median(vals), 4) if vals else None,
            "max": round(max(vals), 4) if vals else None,
        }

    return {
        "sample": str(sample_path),
        "labels": str(labels_path),
        "manifest": str(manifest_path),
        "per_cell": per_cell,
        "overall": overall,
        "score_bands_active": band_stats,
        "quality_active_usable": _quality_stats(
            [r for r in active_rows if r["label_usable"] == "yes"]),
        "quality_active_unusable": _quality_stats(
            [r for r in active_rows if r["label_usable"] == "no"]),
        "quality_quarantine": _quality_stats(q_rows),
        "label_notes": {
            "reviewed": sum(1 for r in labels if r["review_status"] == "reviewed"),
            "contested_after_adjudication": sum(
                1 for r in labels if r["review_status"] == "contested"),
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = analyze(Path(args.sample), Path(args.labels), Path(args.manifest))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps(out["overall"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
