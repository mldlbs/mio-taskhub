# -*- coding: utf-8 -*-
"""D1 决策门数据采集：首启样本统计（03 §5/§7 D1，T1.4）。

只读 run + event(reaper_decision) + task 三表，按任务首个 run（MIN started_at）
定义「首启样本」，输出：
  - n 首启样本（--min-n 门控，n 不足 exit 1）；
  - 结局分布（COMPLETED / FAILED / CANCELLED / QUEUED）与首启成功率；
  - 失败三分根因（观测不足 / 链路断 / 真死，03 §5 D1）；
  - reaper_decision 样本量（"非误回收"证明门槛 n≥30）。

用法：
  python scripts/first_start_stats.py --min-n 100
  python scripts/first_start_stats.py --db path/to.db --json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Optional


def _default_db() -> str:
    return os.environ.get("MIO_TASKHUB_DB") or str(
        Path.home() / ".mio_taskhub" / "taskhub.db")


def _classify_failure(reaper_kind: Optional[str], outcome: Optional[str],
                      first_progress: Optional[int]) -> str:
    """失败三分根因（03 §5 D1）：
    - 观测不足：never_started（claim 后从未上报 progress，requeue 也未成功）；
    - 链路断：agent_offline（曾上报 progress 后掉线，requeue）；
    - 真死：outcome=fail（requeue 用尽/bounce 超限终态失败）或无 reaper 事件但任务 FAILED。
    """
    if outcome == "fail":
        return "real_death"
    if reaper_kind == "never_started":
        return "insufficient_obs"
    if reaper_kind == "agent_offline":
        return "link_broken"
    # 无 reaper 事件却 FAILED：显式失败提交或其他终态
    return "real_death"


def collect(conn: sqlite3.Connection, min_n: int = 100) -> dict:
    # 首启样本：每任务第一个 run（MIN started_at）
    first_runs = conn.execute("""
        SELECT r.id AS run_id, r.task_id, r.progress AS first_progress,
               r.state AS run_state, t.state AS task_state
        FROM run r
        JOIN (SELECT task_id, MIN(started_at) AS m FROM run GROUP BY task_id) f
          ON r.task_id = f.task_id AND r.started_at = f.m
        LEFT JOIN task t ON t.id = r.task_id
    """).fetchall()

    # 首启 run 的 reaper_decision（取每个 run 最新一条）
    reaper_by_run: dict[str, dict] = {}
    for row in conn.execute("""
        SELECT json_extract(payload, '$.run_id') AS run_id,
               json_extract(payload, '$.reaper_kind') AS reaper_kind,
               json_extract(payload, '$.outcome') AS outcome,
               at
        FROM event
        WHERE type = 'reaper_decision'
        ORDER BY at
    """):
        run_id = row[0]
        if run_id:
            reaper_by_run[run_id] = {"reaper_kind": row[1], "outcome": row[2]}

    n = len(first_runs)
    outcome_dist: Counter = Counter()
    fail_causes: Counter = Counter()
    reaper_samples = 0
    first_start_success = 0
    decided = 0  # COMPLETED + FAILED（成功率分母）

    for run_id, task_id, first_progress, run_state, task_state in first_runs:
        ts = (task_state or "?").upper() if task_state else "?"
        outcome_dist[ts] += 1
        reaper = reaper_by_run.get(run_id)
        if reaper:
            reaper_samples += 1
        if ts == "COMPLETED":
            first_start_success += 1
            decided += 1
        elif ts == "FAILED":
            decided += 1
            cause = _classify_failure(
                reaper["reaper_kind"] if reaper else None,
                reaper["outcome"] if reaper else None,
                first_progress,
            )
            fail_causes[cause] += 1
        # CANCELLED / QUEUED 不进成功率分母（非执行结局）

    success_rate = (first_start_success / decided) if decided else None
    return {
        "n_first_start": n,
        "outcome_dist": dict(outcome_dist),
        "first_start_success": first_start_success,
        "decided": decided,  # COMPLETED+FAILED 分母
        "success_rate": success_rate,
        "failure_causes": {
            "insufficient_obs": fail_causes.get("insufficient_obs", 0),
            "link_broken": fail_causes.get("link_broken", 0),
            "real_death": fail_causes.get("real_death", 0),
        },
        "reaper_decision_samples": reaper_samples,
        "gates": {
            "n_ge_min": n >= min_n,
            "reaper_ge_30": reaper_samples >= 30,
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="D1 首启样本统计（T1.4，只读）")
    ap.add_argument("--db", default=_default_db())
    ap.add_argument("--min-n", type=int, default=100,
                    help="首启样本量门控；n<min-n 时 exit 1（03 §7 D1）")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[first_start_stats] db not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    try:
        stats = collect(conn, min_n=args.min_n)
    finally:
        conn.close()

    stats["min_n"] = args.min_n
    stats["db"] = str(db_path)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        sr = stats["success_rate"]
        sr_s = f"{sr:.1%}" if sr is not None else "N/A"
        print(f"[first_start_stats] db={stats['db']}")
        print(f"  n_first_start          = {stats['n_first_start']}")
        print(f"  outcome_dist           = {stats['outcome_dist']}")
        print(f"  first_start_success    = {stats['first_start_success']}/{stats['decided']} = {sr_s}")
        fc = stats["failure_causes"]
        print(f"  failure_causes         = 观测不足 {fc['insufficient_obs']} / "
              f"链路断 {fc['link_broken']} / 真死 {fc['real_death']}")
        print(f"  reaper_decision_samples= {stats['reaper_decision_samples']}")
        print(f"  gates                  = n≥{args.min_n}: {stats['gates']['n_ge_min']}, "
              f"reaper≥30: {stats['gates']['reaper_ge_30']}")

    if not stats["gates"]["n_ge_min"]:
        print(f"[first_start_stats] FAIL: n_first_start={stats['n_first_start']} < min_n={args.min_n}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
