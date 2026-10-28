# -*- coding: utf-8 -*-
"""独立人工复核分析：一致性、kappa、混淆矩阵、分歧清单。

不修改原始复核表，不自动裁决。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

KINDS = {"signal", "fact", "result", "problem", "decision", "lesson"}


def _wilson(k: int, n: int, z: float = 1.96):
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return [round(max(0, centre - half), 4), round(min(1, centre + half), 4)]


def _kappa(a: List[str], b: List[str]) -> Optional[float]:
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if pe >= 1:
        return 1.0
    return round((po - pe) / (1 - pe), 4)


def _read_pack(path: Path) -> Dict[str, dict]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {str(r["sample_id"]): r for r in rows}


def _read_reviewer(path: Path) -> Dict[str, dict]:
    out = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            sid = row.get("sample_id", "").strip()
            if not sid:
                continue
            if sid in out:
                raise ValueError(f"{path}: duplicate sample_id {sid}")
            kind = (row.get("label_kind") or "").strip()
            usable = (row.get("label_usable") or "").strip().lower()
            conf = (row.get("confidence") or "").strip().lower()
            if kind not in KINDS:
                raise ValueError(f"{path}: {sid}: invalid label_kind {kind!r}")
            if usable not in ("yes", "no"):
                raise ValueError(f"{path}: {sid}: invalid label_usable {usable!r}")
            if conf not in ("high", "medium", "low"):
                raise ValueError(f"{path}: {sid}: invalid confidence {conf!r}")
            if not (row.get("reason") or "").strip():
                raise ValueError(f"{path}: {sid}: missing reason")
            out[sid] = {
                "label_kind": kind,
                "label_usable": usable,
                "confidence": conf,
                "reason": row.get("reason", ""),
                "quote": row.get("quote", ""),
                "reviewer_id": (row.get("reviewer_id") or "").strip(),
                "reviewed_at": (row.get("reviewed_at") or "").strip(),
                "round": (row.get("round") or "").strip(),
            }
    return out


def _expected_quote(text: str) -> str:
    return text.replace("\t", " ").replace("\n", "\\n").strip()


def _integrity(pack: Dict[str, dict], reviewers: Dict[str, Dict[str, dict]]) -> dict:
    """标注表完整性：行数、复核人标识、原文一致性、轮次与时间。"""

    quote_mismatch: Dict[str, List[str]] = {}
    ids_by_reviewer: Dict[str, List[str]] = {}
    missing_id: Dict[str, List[str]] = {}
    rounds: Dict[str, List[str]] = {}
    for name, rows in reviewers.items():
        bad = [s for s, r in rows.items()
               if r["quote"].strip()
               and r["quote"].strip() != _expected_quote(pack[s]["quote"])]
        quote_mismatch[name] = sorted(bad)
        ids_by_reviewer[name] = sorted({r["reviewer_id"] for r in rows.values()
                                        if r["reviewer_id"]})
        missing_id[name] = sorted(s for s, r in rows.items() if not r["reviewer_id"])
        rounds[name] = sorted({r["round"] for r in rows.values() if r["round"]})

    owners: Dict[str, set] = {}
    for name, ids in ids_by_reviewer.items():
        for rid in ids:
            owners.setdefault(rid, set()).add(name)
    collisions = sorted(rid for rid, who in owners.items() if len(who) > 1)

    return {
        "rows_per_reviewer": {n: len(r) for n, r in reviewers.items()},
        "reviewer_ids": ids_by_reviewer,
        "missing_reviewer_id": missing_id,
        "identity_collisions": collisions,
        "quote_mismatches": quote_mismatch,
        "rounds": rounds,
        "clean": (not collisions
                  and not any(missing_id.values())
                  and not any(quote_mismatch.values())),
    }


def _confusion(a: List[str], b: List[str]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for x, y in zip(a, b):
        out.setdefault(x, {}).setdefault(y, 0)
        out[x][y] += 1
    return out


def analyze(kit: Path, reviewer_paths: List[Path],
            system_labels: Optional[Path] = None) -> dict:
    pack = _read_pack(kit / "review-pack.jsonl")
    reviewers = {p.stem: _read_reviewer(p) for p in reviewer_paths}
    ids = set(pack)
    for name, rows in reviewers.items():
        if set(rows) != ids:
            missing = sorted(ids - set(rows))
            extra = sorted(set(rows) - ids)
            raise ValueError(f"{name}: sample mismatch missing={missing} extra={extra}")

    names = sorted(reviewers)
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = reviewers[names[i]], reviewers[names[j]]
            ka = [a[s]["label_kind"] for s in sorted(ids)]
            kb = [b[s]["label_kind"] for s in sorted(ids)]
            ua = [a[s]["label_usable"] for s in sorted(ids)]
            ub = [b[s]["label_usable"] for s in sorted(ids)]
            pairs.append({
                "reviewer_a": names[i],
                "reviewer_b": names[j],
                "kind_agreement": round(
                    sum(1 for x, y in zip(ka, kb) if x == y) / len(ids), 4),
                "kind_agreement_ci95": _wilson(
                    sum(1 for x, y in zip(ka, kb) if x == y), len(ids)),
                "kind_kappa": _kappa(ka, kb),
                "usable_agreement": round(
                    sum(1 for x, y in zip(ua, ub) if x == y) / len(ids), 4),
                "usable_agreement_ci95": _wilson(
                    sum(1 for x, y in zip(ua, ub) if x == y), len(ids)),
                "usable_kappa": _kappa(ua, ub),
                "kind_confusion": _confusion(ka, kb),
            })

    disagreements = []
    for s in sorted(ids):
        kinds = {name: reviewers[name][s]["label_kind"] for name in names}
        usable = {name: reviewers[name][s]["label_usable"] for name in names}
        if len(set(kinds.values())) > 1 or len(set(usable.values())) > 1:
            disagreements.append({
                "sample_id": s,
                "source_key": pack[s]["source_key"],
                "quote": pack[s]["quote"],
                "kinds": kinds,
                "usable": usable,
                "reasons": {name: reviewers[name][s]["reason"] for name in names},
                "confidence": {name: reviewers[name][s]["confidence"] for name in names},
            })

    vs_system = None
    if system_labels and system_labels.exists():
        sys_rows = {r["sample_id"]: r for r in (
            json.loads(x) for x in system_labels.read_text(
                encoding="utf-8").splitlines() if x.strip())}
        vs_system = {}
        for name in names:
            rows = reviewers[name]
            common = sorted(ids & set(sys_rows))
            ka = [rows[s]["label_kind"] for s in common]
            ks = [sys_rows[s]["label_kind"] for s in common]
            ua = [rows[s]["label_usable"] for s in common]
            us = [sys_rows[s]["label_usable"] for s in common]
            vs_system[name] = {
                "n": len(common),
                "kind_agreement": round(
                    sum(1 for x, y in zip(ka, ks) if x == y) / len(common), 4)
                if common else None,
                "kind_kappa": _kappa(ka, ks),
                "usable_agreement": round(
                    sum(1 for x, y in zip(ua, us) if x == y) / len(common), 4)
                if common else None,
                "usable_kappa": _kappa(ua, us),
            }

    integrity = _integrity(pack, reviewers)
    integrity["sheet_sha256"] = {
        p.stem: hashlib.sha256(p.read_bytes()).hexdigest() for p in reviewer_paths}
    integrity["sheet_sha256_lf"] = {
        p.stem: hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for p in reviewer_paths}

    return {
        "kit": str(kit),
        "samples": len(ids),
        "reviewers": names,
        "integrity": integrity,
        "per_reviewer": {
            name: {
                "kind_counts": dict(Counter(r["label_kind"] for r in rows.values())),
                "usable_counts": dict(Counter(r["label_usable"] for r in rows.values())),
                "confidence_counts": dict(Counter(r["confidence"] for r in rows.values())),
            } for name, rows in reviewers.items()
        },
        "pairs": pairs,
        "disagreements": disagreements,
        "vs_system": vs_system,
    }


def render(report: dict, out: Path) -> None:
    lines = ["# P5.1 独立复核报告", ""]
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 复核人 | {', '.join(report['reviewers'])} |")
    lines.append(f"| 样本 | {report['samples']} |")
    lines.append("")
    integrity = report.get("integrity") or {}
    lines.append("## 完整性检查")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 行数 | {json.dumps(integrity.get('rows_per_reviewer', {}), ensure_ascii=False)} |")
    lines.append(f"| 复核人标识 | {json.dumps(integrity.get('reviewer_ids', {}), ensure_ascii=False)} |")
    lines.append(f"| 标识冲突 | {integrity.get('identity_collisions') or '无'} |")
    lines.append(f"| 缺复核人标识 | {json.dumps(integrity.get('missing_reviewer_id', {}), ensure_ascii=False)} |")
    lines.append(f"| 原文不一致 | {json.dumps(integrity.get('quote_mismatches', {}), ensure_ascii=False)} |")
    lines.append(f"| 轮次 | {json.dumps(integrity.get('rounds', {}), ensure_ascii=False)} |")
    lines.append(f"| 表 hash | {json.dumps(integrity.get('sheet_sha256', {}), ensure_ascii=False)} |")
    lines.append(f"| 表 hash (LF) | {json.dumps(integrity.get('sheet_sha256_lf', {}), ensure_ascii=False)} |")
    lines.append("")
    lines.append("## 一致性")
    lines.append("")
    lines.append("| 对比 | 类型一致率 | κ | 可用性一致率 | κ |")
    lines.append("|---|---|---|---|---|")
    for p in report["pairs"]:
        lines.append(
            f"| {p['reviewer_a']} vs {p['reviewer_b']} | "
            f"{p['kind_agreement']:.1%} [ {p['kind_agreement_ci95'][0]:.1%}, "
            f"{p['kind_agreement_ci95'][1]:.1%} ] | {p['kind_kappa']} | "
            f"{p['usable_agreement']:.1%} [ {p['usable_agreement_ci95'][0]:.1%}, "
            f"{p['usable_agreement_ci95'][1]:.1%} ] | {p['usable_kappa']} |")
    lines.append("")
    lines.append("## 分歧（需裁决）")
    lines.append("")
    lines.append("| sample | source | kinds | usable | reasons |")
    lines.append("|---|---|---|---|---|")
    for d in report["disagreements"]:
        lines.append(
            f"| {d['sample_id']} | {d['source_key']} | "
            f"{json.dumps(d['kinds'], ensure_ascii=False)} | "
            f"{json.dumps(d['usable'], ensure_ascii=False)} | "
            f"{json.dumps(d['reasons'], ensure_ascii=False)[:300]} |")
    lines.append("")
    lines.append("## 系统标签对照（分析阶段，不参与盲标）")
    lines.append("")
    if report["vs_system"]:
        lines.append("| 复核人 | n | 类型一致率 | κ | 可用性一致率 | κ |")
        lines.append("|---|---|---|---|---|---|")
        for name, v in report["vs_system"].items():
            lines.append(f"| {name} | {v['n']} | {v['kind_agreement']} | {v['kind_kappa']} | {v['usable_agreement']} | {v['usable_kappa']} |")
    else:
        lines.append("未提供系统标签。")
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--kit", required=True)
    p.add_argument("--reviewer", action="append", required=True)
    p.add_argument("--system-labels", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--report", required=True)
    args = p.parse_args()
    report = analyze(
        Path(args.kit),
        [Path(x) for x in args.reviewer],
        Path(args.system_labels) if args.system_labels else None,
    )
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    render(report, Path(args.report))
    print(json.dumps({k: v for k, v in report.items() if k != "disagreements"},
                     ensure_ascii=False, indent=2))
    print(f"disagreements={len(report['disagreements'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
