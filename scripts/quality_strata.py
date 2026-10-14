# -*- coding: utf-8 -*-
"""P 系列观测分层 V2（只读；T2.3 / 02 §8.4 F16 / G6b）。

观测字段（只读，不改判定方向）：
  - event.payload.quality_score：set_doc_status 推进时记录的质量分
  - task.doc_statuses：kind -> {state, at, note} 生命周期状态
  - QUALITY_SPEC 各 kind 必需/建议章节数（F16：全 26 节缺失）
  - MIN_SCORE 边界 80（G6b：45 分边界历史案例已修，此处观测分布）

分层（三态统计，04 §7.1 V2）：
  - missing   : 该 kind 从未推进过（无 quality_score 事件）
  - incomplete: 有推进但分数 < MIN_SCORE（kind 若在 MIN_SCORE 表）
  - complete  : 有推进且分数 ≥ MIN_SCORE（或无分数底线的 kind 有推进）

输出：JSON（stdout 或 --out）+ 可读摘要。Fisher 精确检验（纯 stdlib）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# 高规格 kind 与分数底线（与 doc_quality.MIN_SCORE 同步；summarize 不 import 全模块避免副作用）
MIN_SCORE = {"requirement": 80, "spec": 80, "api": 80}
HIGH_SPEC_KINDS = tuple(MIN_SCORE.keys())

# F16：各 kind 必需+建议章节数（与 QUALITY_SPEC 对齐的快照；观测用）
SECTION_COUNTS = {
    "requirement": {"required": 5, "recommended": 0, "total": 5},
    "architecture": {"required": 4, "recommended": 0, "total": 4},
    "spec": {"required": 3, "recommended": 0, "total": 3},
    "data-model": {"required": 3, "recommended": 0, "total": 3},
    "api": {"required": 11, "recommended": 15, "total": 26},  # F16: 26 节
}


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """双侧 Fisher 精确检验 p 值（纯 stdlib；2x2 表 [[a,b],[c,d]]）。"""
    from math import comb
    n = a + b + c + d
    r1, r2, c1 = a + b, c + d, a + c
    def p(x):
        return comb(r1, x) * comb(r2, c1 - x) / comb(n, c1)
    p_obs = p(a)
    p_sum = 0.0
    lo = max(0, c1 - r2)
    hi = min(r1, c1)
    for x in range(lo, hi + 1):
        px = p(x)
        if px <= p_obs * (1 + 1e-9):
            p_sum += px
    return min(1.0, p_sum)


def collect(db_path: Path) -> dict:
    """只读采集 event.quality_score + task.doc_statuses。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict = {"db": str(db_path), "by_kind": {}, "events_sample": []}

    # 1) event.payload.quality_score（set_doc_status 推进时记录）
    #    事件类型 task_doc_status；payload: {kind, from, to, note, forced, quality_score}
    q_events = """
        SELECT entity_id AS task_id, type, payload
        FROM event
        WHERE type = 'task_doc_status'
        ORDER BY rowid
    """
    score_by_task_kind: dict[tuple, list] = defaultdict(list)
    try:
        for row in conn.execute(q_events):
            try:
                payload = json.loads(row["payload"] or "{}")
            except Exception:
                continue
            qs = payload.get("quality_score")
            kind = payload.get("kind") or payload.get("doc_kind")
            if qs is not None and kind:
                score_by_task_kind[(row["task_id"], kind)].append(int(qs))
    except sqlite3.Error as e:
        out["error_events"] = str(e)

    # 2) task.doc_statuses
    q_tasks = "SELECT id, doc_statuses FROM task"
    doc_status_by_task: dict[str, dict] = {}
    try:
        for row in conn.execute(q_tasks):
            try:
                doc_status_by_task[row["id"]] = json.loads(row["doc_statuses"] or "{}")
            except Exception:
                doc_status_by_task[row["id"]] = {}
    except sqlite3.Error as e:
        out["error_tasks"] = str(e)

    # 3) 按 kind 分层统计
    all_kinds = set(SECTION_COUNTS) | set(MIN_SCORE)
    # 也收集 doc_statuses 里出现的其他 kind
    for statuses in doc_status_by_task.values():
        all_kinds |= set(statuses.keys())

    for kind in sorted(all_kinds):
        floor = MIN_SCORE.get(kind)
        n_tasks = 0
        n_missing = 0
        n_incomplete = 0
        n_complete = 0
        scores: list[int] = []
        n_with_status = 0
        states: dict[str, int] = defaultdict(int)

        for tid, statuses in doc_status_by_task.items():
            st = statuses.get(kind)
            has_score = (tid, kind) in score_by_task_kind
            if st is not None:
                n_with_status += 1
                states[st.get("state", "?")] += 1
            if st is None and not has_score:
                n_missing += 1
                continue
            n_tasks += 1
            if has_score:
                # 取最后一次推进的分数
                s = score_by_task_kind[(tid, kind)][-1]
                scores.append(s)
                if floor is not None and s < floor:
                    n_incomplete += 1
                else:
                    n_complete += 1
            else:
                # 有状态但无分数事件（旧数据/force 路径）
                n_complete += 1  # 不判 incomplete（缺分数≠低分）
        n_total = len(doc_status_by_task)
        # 三态归一到全体任务
        n_missing = n_total - n_with_status  # 从未登记该 kind
        out["by_kind"][kind] = {
            "n_tasks_total": n_total,
            "n_with_status": n_with_status,
            "n_missing": n_missing,
            "n_incomplete": n_incomplete,
            "n_complete": n_complete,
            "min_score_floor": floor,
            "section_counts": SECTION_COUNTS.get(kind),
            "score_min": min(scores) if scores else None,
            "score_max": max(scores) if scores else None,
            "score_median": (sorted(scores)[len(scores) // 2] if scores else None),
            "states": dict(states),
        }

    # 4) F16：api 26 节观测——有多少任务的 api 文档 missing 全部 26 节
    #    （无法从 event 直接读章节；此处用 quality_score 推断：无事件=missing）
    api_stats = out["by_kind"].get("api", {})
    out["f16_api_26_sections"] = {
        "total_sections": SECTION_COUNTS.get("api", {}).get("total", 26),
        "n_tasks_api_missing": api_stats.get("n_missing", 0),
        "n_tasks_api_with_status": api_stats.get("n_with_status", 0),
        "note": "missing = 无 doc_statuses[api] 且无 quality_score 事件",
    }

    # 5) G6b：45 分边界——观测 score 分布中 <80 的比例
    all_scores: list[int] = []
    for v in score_by_task_kind.values():
        all_scores.extend(v)
    if all_scores:
        n_lt80 = sum(1 for s in all_scores if s < 80)
        n_lt45 = sum(1 for s in all_scores if s < 45)
        out["g6b_score_distribution"] = {
            "n_scored": len(all_scores),
            "n_lt_80": n_lt80,
            "n_lt_45": n_lt45,
            "pct_lt_80": round(n_lt80 / len(all_scores), 4),
            "pct_lt_45": round(n_lt45 / len(all_scores), 4),
            "boundary_80": MIN_SCORE.get("api"),
        }
    else:
        out["g6b_score_distribution"] = {"n_scored": 0}

    # 6) Fisher：高规格 kind 的 missing vs complete（2x2）
    fisher_results = {}
    for kind in HIGH_SPEC_KINDS:
        ks = out["by_kind"].get(kind)
        if not ks:
            continue
        a = ks["n_complete"]
        b = ks["n_incomplete"]
        c = ks["n_missing"]
        d = max(0, ks["n_tasks_total"] - a - b - c)
        fisher_results[kind] = {
            "table": [[a, b], [c, d]],
            "p_two_sided": fisher_exact_2x2(a, b, c, d),
        }
    out["fisher_high_spec"] = fisher_results

    conn.close()
    return out


def render_summary(data: dict) -> str:
    lines = ["# P 系列观测分层 V2（只读；T2.3）", ""]
    lines.append(f"- db: `{data['db']}`")
    if data.get("error_events") or data.get("error_tasks"):
        lines.append(f"- ⚠️ 采集错误: events={data.get('error_events')} tasks={data.get('error_tasks')}")
    lines.append("")
    lines.append("## 按 kind 三态分层")
    lines.append("")
    lines.append("| kind | total | with_status | missing | incomplete | complete | floor | median |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for kind, ks in data["by_kind"].items():
        lines.append(
            f"| {kind} | {ks['n_tasks_total']} | {ks['n_with_status']} "
            f"| {ks['n_missing']} | {ks['n_incomplete']} | {ks['n_complete']} "
            f"| {ks['min_score_floor'] or '-'} | {ks['score_median'] or '-'} |")
    lines.append("")
    f16 = data.get("f16_api_26_sections", {})
    lines.append(f"## F16：api 26 节缺失观测")
    lines.append(f"- total_sections: {f16.get('total_sections')}")
    lines.append(f"- n_api_missing: {f16.get('n_tasks_api_missing')}")
    lines.append(f"- n_api_with_status: {f16.get('n_tasks_api_with_status')}")
    lines.append("")
    g6b = data.get("g6b_score_distribution", {})
    lines.append(f"## G6b：分数分布（边界 80）")
    lines.append(f"- n_scored: {g6b.get('n_scored')}")
    lines.append(f"- n_lt_80: {g6b.get('n_lt_80')} ({g6b.get('pct_lt_80', 0):.1%})")
    lines.append(f"- n_lt_45: {g6b.get('n_lt_45')} ({g6b.get('pct_lt_45', 0):.1%})")
    lines.append("")
    lines.append("## Fisher 精确检验（高规格 kind）")
    for kind, fr in data.get("fisher_high_spec", {}).items():
        lines.append(f"- {kind}: table={fr['table']} p={fr['p_two_sided']:.4f}")
    lines.append("")
    lines.append("> V2 纪律：本报告只做观测分层，不改 M/B 判定方向（04 §7.1）。")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="P 系列观测分层 V2（只读）")
    ap.add_argument("--db", default=str(Path.home() / ".mio_taskhub" / "taskhub.db"))
    ap.add_argument("--out", default="", help="输出 JSON 路径（缺省 stdout JSON）")
    ap.add_argument("--summary", action="store_true", help="输出可读摘要（缺省 JSON）")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.is_file():
        print(f"[quality_strata] db not found: {db}", file=sys.stderr)
        return 2
    data = collect(db)
    if args.summary:
        text = render_summary(data)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"[quality_strata] -> {args.out}")
        else:
            print(text)
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"[quality_strata] -> {args.out}")
        else:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
