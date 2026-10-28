# -*- coding: utf-8 -*-
"""独立结构校验（不 import 提取器/质量规则）。

在素材库副本上检查规范级不变量：
- 逐字回读：locator 指向的原始字段切片 == witness.quote_exact == material.quote
- 原子性：≤240 字、≤2 句末标点、≤2 编号项（原子来源）
- 结论形状：decision 不以冒号结尾、不短于 10 字
- 裸计数：result 不能只是 "67 passed" / "failed=25"
- 列表标记泄漏：quote 不能以 ①/1)/- 开头
- 重复：active 中 content_fingerprint 不得重复

这些是规范检查，不是语义标注；输出只代表“结构上可判定的缺陷”。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

ATOMIC_SOURCES = {"doc.review", "discussion.closed", "run.result",
                  "hn", "github-trending"}
DECISION_SOURCES = {"doc.requirement", "doc.review", "discussion.closed"}
TERM = re.compile(r"[。！？]|[.!?](?=\s|$)")
ITEM = re.compile(r"[①-⑩]|(?<!\d)\d+[)）、]")
MARKER = re.compile(r"^\s*(?:[①-⑩]|\d+[)）、]|[-*]\s+)")
BARE_COUNT = re.compile(
    r"^\s*(?:\d+\s*(?:passed|failed|skipped|errors?)?|"
    r"(?:passed|failed|skipped|errors?)\s*[=:：]\s*\d+)\s*$", re.I)


def _load_exclusions(paths: Iterable[Path]) -> Tuple[Set[str], Set[Tuple[str, str]]]:
    """按 material_id 或 (kind, quote) 排除历史样本（DB 副本 id 会变）。"""
    ids: Set[str] = set()
    pairs: Set[Tuple[str, str]] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            mid = row.get("material_id")
            if mid:
                ids.add(str(mid))
            kind, quote = row.get("kind"), row.get("quote")
            if kind and quote:
                pairs.add((str(kind), str(quote)))
    return ids, pairs


def _locator_slice(payload: Any, locator: Any) -> Tuple[Optional[str], str]:
    """返回 (切片, 状态)；状态为 ok/missing/invalid。"""
    if not isinstance(payload, dict) or not isinstance(locator, dict):
        return None, "missing"
    field = locator.get("field")
    start, end = locator.get("char_start"), locator.get("char_end")
    if not field or not isinstance(start, int) or not isinstance(end, int):
        return None, "invalid"
    text = payload.get(field)
    if not isinstance(text, str) or start < 0 or end > len(text) or end < start:
        return None, "invalid"
    return text[start:end], "ok"


def _defects(row: Dict[str, Any]) -> Dict[str, bool]:
    q = str(row.get("quote") or "")
    src = str(row.get("source_key") or "")
    out = {
        "verbatim": False,
        "atomic": False,
        "decision_shape": False,
        "bare_count": False,
        "marker_leak": False,
    }
    if row.get("_verbatim") == "mismatch":
        out["verbatim"] = True
    if src in ATOMIC_SOURCES and (
            len(q) > 240 or len(TERM.findall(q)) > 2
            or len(ITEM.findall(q)) >= 3):
        out["atomic"] = True
    if row.get("kind") == "decision" and src in DECISION_SOURCES:
        if len(q) < 10 or q.rstrip().endswith(("：", ":")):
            out["decision_shape"] = True
    if row.get("kind") == "result" and BARE_COUNT.match(q.strip()):
        out["bare_count"] = True
    if MARKER.match(q):
        out["marker_leak"] = True
    return out


def validate(db_path: Path, exclude: Iterable[Path] = (),
             examples: int = 5) -> dict:
    excluded_ids, excluded_pairs = _load_exclusions(exclude)
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select m.id, m.kind, m.quote, m.status, m.status_reason, m.quality, "
        "m.content_fingerprint, w.source_key, w.quote_exact, w.locator, "
        "r.payload "
        "from material m "
        "left join material_witness w on w.material_id=m.id and w.is_primary=1 "
        "left join material_raw r on r.id=w.raw_id"
    ).fetchall()
    conn.close()

    records: List[Dict[str, Any]] = []
    excluded_rows = 0
    for row in rows:
        item = dict(row)
        if str(item["id"]) in excluded_ids or (
                str(item["kind"]), str(item["quote"])) in excluded_pairs:
            excluded_rows += 1
            continue
        payload = None
        if item.get("payload"):
            try:
                payload = json.loads(item["payload"])
            except Exception:
                payload = None
        locator = None
        if item.get("locator"):
            try:
                locator = json.loads(item["locator"])
            except Exception:
                locator = None
        snippet, status = _locator_slice(payload, locator)
        if status != "ok":
            verbatim = "missing"
        elif snippet != item.get("quote_exact") or item.get("quote_exact") != item.get("quote"):
            verbatim = "mismatch"
        else:
            verbatim = "ok"
        item["_verbatim"] = verbatim
        flags = _defects(item)
        item["_defects"] = [k for k, v in flags.items() if v]
        records.append(item)

    active = [r for r in records if r["status"] == "active"]
    quarantine = [r for r in records if r["status"] == "quarantine"]
    defect_counter = Counter()
    by_check = Counter()
    examples_out: Dict[str, List[dict]] = defaultdict(list)
    for r in active:
        for name in r["_defects"]:
            by_check[name] += 1
            if len(examples_out[name]) < examples:
                examples_out[name].append({
                    "id": r["id"], "kind": r["kind"], "source_key": r["source_key"],
                    "quote": str(r["quote"])[:180],
                })
    defect_counter["active_with_any"] = sum(1 for r in active if r["_defects"])

    fp_groups: Dict[str, List[str]] = defaultdict(list)
    for r in active:
        fp_groups[str(r["content_fingerprint"])].append(str(r["id"]))
    duplicates = {fp: ids for fp, ids in fp_groups.items() if len(ids) > 1}

    clean_quarantine = [r for r in quarantine if not r["_defects"]]
    q_reasons = Counter(str(r.get("status_reason") or "unknown")
                        for r in quarantine)
    verbatim_unverifiable = sum(1 for r in records if r["_verbatim"] == "missing")

    def _rate(n: int, d: int) -> Optional[float]:
        return round(n / d, 4) if d else None

    return {
        "db": str(db_path).replace("\\", "/"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "population": {
            "materials": len(rows),
            "excluded_held_out": excluded_rows,
            "evaluated": len(records),
            "active": len(active),
            "quarantine": len(quarantine),
        },
        "checks": {
            "active_with_any_defect": defect_counter["active_with_any"],
            "by_check": dict(by_check),
            "verbatim_unverifiable": verbatim_unverifiable,
            "duplicate_groups": len(duplicates),
            "duplicate_materials": sum(len(v) for v in duplicates.values()),
        },
        "structural_misaccept": {
            "n": defect_counter["active_with_any"],
            "rate": _rate(defect_counter["active_with_any"], len(active)),
            "examples": examples_out,
        },
        "quarantine_possible_miss": {
            "n": len(clean_quarantine),
            "rate": _rate(len(clean_quarantine), len(quarantine)),
            "status_reasons": dict(q_reasons),
            "examples": [
                {"id": r["id"], "kind": r["kind"], "source_key": r["source_key"],
                 "quote": str(r["quote"])[:180]}
                for r in clean_quarantine[:examples]
            ],
        },
        "duplicates": {
            fp: ids[:10] for fp, ids in list(duplicates.items())[:examples]
        },
        "note": ("结构检查不等于语义标注；true usability 未测量。"
                 "quarantine_possible_miss 仅表示未发现结构缺陷。"),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--exclude-sample", action="append", default=[])
    p.add_argument("--examples", type=int, default=5)
    args = p.parse_args()
    report = validate(
        Path(args.db),
        [Path(x) for x in args.exclude_sample],
        examples=args.examples,
    )
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({
        "population": report["population"],
        "structural_misaccept": {
            "n": report["structural_misaccept"]["n"],
            "rate": report["structural_misaccept"]["rate"],
        },
        "quarantine_possible_miss": {
            "n": report["quarantine_possible_miss"]["n"],
            "rate": report["quarantine_possible_miss"]["rate"],
        },
        "checks": report["checks"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
