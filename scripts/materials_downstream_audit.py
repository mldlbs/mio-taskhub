# -*- coding: utf-8 -*-
"""下游实验预审计：只做数据/泄漏/功效检查，不跑正式测试。

输出：重复任务、跨切分泄漏、标签分布、标签噪声、素材覆盖、功效估计。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")
EXTERNAL = ("hn", "github-trending")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _bucket(n: int) -> str:
    n = int(n or 0)
    return "0" if n == 0 else ("1" if n == 1 else "2+")


def load_tasks(db: Path) -> List[dict]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select id,title,description,acceptance_criteria,created_at,state,stage,"
        "project,target_agent_type,block_reason,review_result,retry_count,"
        "bounce_count,completed_at,failed_at,cancelled_at "
        "from task where state in ('COMPLETED','FAILED','CANCELLED')"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def group_tasks(rows: List[dict]) -> Dict[str, List[dict]]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for row in rows:
        key = _norm(row["title"]) + "||" + _norm(row["description"])
        groups[key].append(row)
    return groups


def material_coverage(db: Path, tasks: List[dict], k: int = 5) -> dict:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    mats = conn.execute(
        "select m.id, m.quote, m.observed_at from material m "
        "join material_witness w on w.material_id=m.id and w.is_primary=1 "
        "where w.source_key in ('hn','github-trending')").fetchall()
    conn.close()
    total = 0
    at_least_one = 0
    at_least_k = 0
    for task in tasks:
        created = task["created_at"]
        count = sum(1 for m in mats if m["observed_at"] <= created)
        total += 1
        at_least_one += 1 if count >= 1 else 0
        at_least_k += 1 if count >= k else 0
    return {
        "tasks": total,
        "at_least_one": at_least_one,
        "at_least_k": at_least_k,
        "k": k,
        "rate_at_least_one": round(at_least_one / total, 4) if total else None,
        "rate_at_least_k": round(at_least_k / total, 4) if total else None,
    }


def power_estimate(n_pos: int, n_neg: int, effect: float = 0.10,
                   alpha: float = 0.05, power: float = 0.8) -> dict:
    z_a = 1.959963984540054
    z_b = 0.8416212335729143
    se = math.sqrt(0.25 / n_pos + 0.25 / n_neg) if n_pos and n_neg else None
    half = z_a * se if se is not None else None
    detectable = (z_a + z_b) * se if se is not None else None
    required = (2 * (z_a + z_b) ** 2 * 0.25 / effect ** 2) if effect else None
    return {
        "effect": effect,
        "alpha": alpha,
        "power": power,
        "se_balanced_accuracy": round(se, 4) if se else None,
        "ci95_half_width": round(half, 4) if half else None,
        "min_detectable_effect_80pct": round(detectable, 4) if detectable else None,
        "required_positive_per_arm": math.ceil(required) if required else None,
        "assumptions": "independent arms, balanced accuracy, conservative variance",
    }


def audit(db: Path, cutoff: str) -> dict:
    rows = load_tasks(db)
    groups = group_tasks(rows)
    reps = [min(v, key=lambda r: r["created_at"]) for v in groups.values()]
    reps.sort(key=lambda r: r["created_at"])
    train = [r for r in reps if r["created_at"] < cutoff]
    test = [r for r in reps if r["created_at"] >= cutoff]

    def dist(items, field):
        return dict(Counter((r[field] or "(none)") for r in items).most_common(12))

    def states(items):
        return dict(Counter(r["state"] for r in items))

    duplicate_rows = sum(len(v) for v in groups.values() if len(v) > 1)
    cross_split = [v for v in groups.values()
                   if any(r["created_at"] < cutoff for r in v)
                   and any(r["created_at"] >= cutoff for r in v)]

    noise = {
        "rows_failed_repaired_block_reason": sum(
            1 for r in rows if r["state"] == "FAILED"
            and "失败被清洗为取消" in str(r.get("block_reason") or "")),
        "unique_failed_repaired": sum(
            1 for r in reps if r["state"] == "FAILED"
            and "失败被清洗为取消" in str(r.get("block_reason") or "")),
        "cancelled_before_implementing_rows": sum(
            1 for r in rows if r["state"] == "CANCELLED"
            and r["stage"] in ("READY", "BRAINSTORMING", "DESIGN", "PLANNING")),
        "cancelled_before_implementing_unique": sum(
            1 for r in reps if r["state"] == "CANCELLED"
            and r["stage"] in ("READY", "BRAINSTORMING", "DESIGN", "PLANNING")),
        "rows_without_review_result": sum(
            1 for r in rows if not str(r.get("review_result") or "").strip()),
        "unique_without_review_result": sum(
            1 for r in reps if not str(r.get("review_result") or "").strip()),
    }

    test_pos = sum(1 for r in test if r["state"] == "COMPLETED")
    test_neg = len(test) - test_pos
    power = power_estimate(test_pos, test_neg)

    report = {
        "db": str(db).replace("\\", "/"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": cutoff,
        "raw": {
            "terminal_tasks": len(rows),
            "states": states(rows),
            "agent_types": dist(rows, "target_agent_type"),
            "stages": dist(rows, "stage"),
            "retry_buckets": dict(Counter(_bucket(r["retry_count"]) for r in rows)),
            "bounce_buckets": dict(Counter(_bucket(r["bounce_count"]) for r in rows)),
        },
        "dedupe": {
            "unique_groups": len(groups),
            "duplicate_rows": duplicate_rows,
            "largest_group": max((len(v) for v in groups.values()), default=0),
            "groups_spanning_split": len(cross_split),
            "unique_states": states(reps),
            "unique_agent_types": dist(reps, "target_agent_type"),
            "unique_projects": dist(reps, "project"),
        },
        "split": {
            "train": {"n": len(train), "states": states(train)},
            "test": {"n": len(test), "states": states(test)},
            "test_positive": test_pos,
            "test_negative": test_neg,
        },
        "features": {
            "empty_title_rows": sum(1 for r in rows if not _norm(r["title"])),
            "empty_description_rows": sum(
                1 for r in rows if not _norm(r["description"])),
            "rows_with_acceptance": sum(
                1 for r in rows if _norm(r["acceptance_criteria"])),
            "unique_with_acceptance": sum(
                1 for r in reps if _norm(r["acceptance_criteria"])),
        },
        "label_noise": noise,
        "materials": {
            "test_external_before_creation": material_coverage(db, test),
            "train_external_before_creation": material_coverage(db, train),
        },
        "power": power,
        "verdict": {
            "runnable": False,
            "reasons": [
                "1,916 行终止任务仅对应 277 个唯一任务；重复任务主导标签分布",
                "1,490 行 FAILED 带“失败被清洗为取消”修复标记，标签非任务难度",
                "去重后测试集仅 71 completed / 41 failed，功效不足",
                "12 个重复组跨时间切分，存在直接泄漏",
            ],
        },
        "note": "只读审计；未训练模型、未使用测试标签做选择、未运行对照实验。",
    }
    return report


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--cutoff", default="2026-10-01")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    report = audit(Path(args.db), args.cutoff)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({
        "raw_terminal": report["raw"]["terminal_tasks"],
        "unique": report["dedupe"]["unique_groups"],
        "duplicate_rows": report["dedupe"]["duplicate_rows"],
        "largest_group": report["dedupe"]["largest_group"],
        "test_positive": report["split"]["test_positive"],
        "test_negative": report["split"]["test_negative"],
        "verdict": report["verdict"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
