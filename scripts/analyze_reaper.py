# -*- coding: utf-8 -*-
"""reaper 回收决策三分根因分析（T0.8，只读工具）。

输入：event 表中 type='reaper_decision' 的自然样本（生产 DB 默认
~/.mio_taskhub/taskhub.db，或 --db 指定）。**不采集、不合成样本**——
样本只来自 reaper 在真实超时路径上的 emit（background.py::_on_timeout）。

三分规则（每样本唯一归桶，证据按优先级）：
  真死   truly_dead   —— agent_status == 'offline'（Hub 侧注册过期），
                         或 T0.7 process_alive 探针明确 alive=false；
  链路断 link_broken  —— agent_status == 'online'（注册仍在心跳，
                         进程实体存活）而 run 心跳超时 → 交付链路断/卡死；
                         或探针明确 alive=true；
  观测不足 insufficient —— agent_status 缺失且无决定性探针（无法判定）。
探针（T0.7）优先于 agent_status 启发式；同 run 取最新一条探针。

输出：三分占比 + heartbeat_lag_seconds 与 effective_timeout_seconds 关系
（散点直方图 + 分位数）+ 可选 CSV。exit 0 = n≥30（Gate G0 达标），
exit 2 = 样本不足。结论确定性可复跑。

用法：
    python scripts/analyze_reaper.py
    python scripts/analyze_reaper.py --db path/to/taskhub.db --csv out.csv
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

MIN_SAMPLES = 30

BUCKET_TRULY_DEAD = "truly_dead"
BUCKET_LINK_BROKEN = "link_broken"
BUCKET_INSUFFICIENT = "insufficient"
BUCKET_LABELS = {
    BUCKET_TRULY_DEAD: "真死",
    BUCKET_LINK_BROKEN: "链路断",
    BUCKET_INSUFFICIENT: "观测不足",
}


def _default_db() -> Path:
    return Path(os.path.expanduser("~")) / ".mio_taskhub" / "taskhub.db"


def _lifecycle_root() -> Path:
    home = os.environ.get("MIO_HOME")
    base = Path(home) if home else (Path(os.path.expanduser("~")) / ".mio-intelligence")
    return base / "lifecycle"


def load_process_probes(root: Path) -> dict:
    """扫描本地 lifecycle jsonl → {run_id: alive}（同 run 取最新）。"""
    probes: dict = {}
    if not root.exists():
        return probes
    for path in sorted(root.rglob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "agent_lifecycle.process_alive":
                continue
            rid = entry.get("run_id") or entry.get("payload", {}).get("run_id")
            if not rid:
                continue
            alive = entry.get("alive", entry.get("payload", {}).get("alive"))
            if alive is not None:
                probes[rid] = bool(alive)
    return probes


def classify(payload: dict, probes: dict):
    """返回 (bucket, reason)。探针 > agent_status 启发式。"""
    run_id = payload.get("run_id")
    if run_id in probes:
        if probes[run_id] is False:
            return BUCKET_TRULY_DEAD, "process_alive=false (T0.7 probe)"
        return BUCKET_LINK_BROKEN, "process_alive=true (T0.7 probe)"
    status = payload.get("agent_status")
    if status == "offline":
        return BUCKET_TRULY_DEAD, "agent_status=offline (hub registration expired)"
    if status == "online":
        return BUCKET_LINK_BROKEN, (
            "agent_status=online (registration alive) but run heartbeat timed out")
    return BUCKET_INSUFFICIENT, "agent_status missing and no decisive probe"


def load_samples(db_path: Path, since: str | None = None):
    con = sqlite3.connect(str(db_path))
    try:
        q = ("select id, at, payload from event "
             "where type='reaper_decision'")
        params: list = []
        if since:
            q += " and at >= ?"
            params.append(since)
        q += " order by id"
        rows = con.execute(q, params).fetchall()
    finally:
        con.close()
    samples = []
    for eid, at, raw in rows:
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            payload = {}
        samples.append({"id": eid, "at": at, "payload": payload})
    return samples


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def scatter_histogram(rows):
    """rows=[(lag, eff)] → 文本散点（行=lag 桶，列=eff 桶）；跳过缺失值。"""
    rows = [(l, e) for l, e in rows if l is not None]
    lag_edges = [0, 300, 600, 1200, 3600, float("inf")]
    eff_buckets = sorted({r[1] for r in rows if r[1] is not None}) or ["?"]
    # 收敛到最多 6 个 eff 列
    eff_buckets = (eff_buckets + ["?"] * 6)[:6]
    counts = {}
    for lag, eff in rows:
        li = next(i for i in range(len(lag_edges) - 1)
                  if (lag is not None and lag_edges[i] <= lag < lag_edges[i + 1]))
        ekey = eff if eff in eff_buckets else "?"
        counts[(li, ekey)] = counts.get((li, ekey), 0) + 1
    lines = ["lag\\eff | " + " | ".join(f"{e:>8}" for e in eff_buckets)]
    for i in range(len(lag_edges) - 1):
        hi = lag_edges[i + 1]
        label = f"{lag_edges[i]}-{int(hi) if hi != float('inf') else 'inf':>}"
        cells = [counts.get((i, e), 0) for e in eff_buckets]
        if not any(cells):
            continue
        lines.append(f"{label:>8} | " + " | ".join(f"{c:>8}" for c in cells))
    return "\n".join(lines)


def analyze(samples, probes):
    buckets = {BUCKET_TRULY_DEAD: [], BUCKET_LINK_BROKEN: [], BUCKET_INSUFFICIENT: []}
    for s in samples:
        bucket, reason = classify(s["payload"], probes)
        s["bucket"] = bucket
        s["reason"] = reason
        buckets[bucket].append(s)
    return buckets


def main(argv=None):
    ap = argparse.ArgumentParser(description="reaper_decision 三分根因分析（只读）")
    ap.add_argument("--db", default=str(_default_db()))
    ap.add_argument("--csv", default=None, help="导出逐样本分类 CSV")
    ap.add_argument("--since", default=None, help="只分析 at>=此时刻（ISO 或 SQLite 可解析）")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[analyze_reaper] db not found: {db_path}", file=sys.stderr)
        return 1

    probes = load_process_probes(_lifecycle_root())
    samples = load_samples(db_path, args.since)
    buckets = analyze(samples, probes)
    n = len(samples)

    print(f"[analyze_reaper] db={db_path} samples={n} probes_matched="
          f"{sum(1 for s in samples if s['payload'].get('run_id') in probes)}")
    gate = "PASS" if n >= MIN_SAMPLES else "FAIL"
    print(f"[analyze_reaper] gate n>={MIN_SAMPLES}: {gate}")
    print()
    print("== 三分占比 ==")
    for b in (BUCKET_TRULY_DEAD, BUCKET_LINK_BROKEN, BUCKET_INSUFFICIENT):
        items = buckets[b]
        pct = (len(items) / n * 100) if n else 0.0
        top_agents: dict = {}
        for s in items:
            a = s["payload"].get("agent_id") or "?"
            top_agents[a] = top_agents.get(a, 0) + 1
        top = sorted(top_agents.items(), key=lambda kv: -kv[1])[:3]
        print(f"  {BUCKET_LABELS[b]:4} ({b:12}) {len(items):4} ({pct:5.1f}%)  "
              f"top_agents={top}")

    print()
    print("== heartbeat_lag_seconds vs effective_timeout_seconds ==")
    lag_eff = [(s["payload"].get("heartbeat_lag_seconds"),
                s["payload"].get("effective_timeout_seconds")) for s in samples]
    lags = sorted(l for l, _ in lag_eff if l is not None)
    if lags:
        print(f"  lag: n={len(lags)} min={lags[0]:.1f} "
              f"p50={_percentile(lags, 0.5):.1f} "
              f"p95={_percentile(lags, 0.95):.1f} max={lags[-1]:.1f}")
    effs = sorted(e for _, e in lag_eff if e is not None)
    if effs:
        print(f"  eff: n={len(effs)} min={effs[0]:.0f} "
              f"p50={_percentile(effs, 0.5):.0f} max={effs[-1]:.0f}")
    margins = sorted(l - e for l, e in lag_eff if l is not None and e is not None)
    if margins:
        print(f"  margin(lag-eff): p50={_percentile(margins, 0.5):.1f}s "
              f"p95={_percentile(margins, 0.95):.1f}s")
    print(scatter_histogram(lag_eff))

    print()
    print("== 分桶 lag 分位（p50/p95） ==")
    for b in (BUCKET_TRULY_DEAD, BUCKET_LINK_BROKEN, BUCKET_INSUFFICIENT):
        vals = sorted(s["payload"].get("heartbeat_lag_seconds")
                      for s in buckets[b] if s["payload"].get("heartbeat_lag_seconds") is not None)
        if vals:
            print(f"  {BUCKET_LABELS[b]:4}: p50={_percentile(vals, 0.5):.1f} "
                  f"p95={_percentile(vals, 0.95):.1f} n={len(vals)}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["event_id", "at", "run_id", "task_id", "agent_id",
                        "agent_status", "reaper_kind", "outcome",
                        "heartbeat_lag_seconds", "effective_timeout_seconds",
                        "bucket", "reason"])
            for s in samples:
                p = s["payload"]
                w.writerow([s["id"], s["at"], p.get("run_id"), p.get("task_id"),
                            p.get("agent_id"), p.get("agent_status"),
                            p.get("reaper_kind"), p.get("outcome"),
                            p.get("heartbeat_lag_seconds"),
                            p.get("effective_timeout_seconds"),
                            s["bucket"], s["reason"]])
        print(f"\n[analyze_reaper] csv -> {args.csv}")

    print()
    print(f"CONCLUSION n={n} gate={gate} "
          f"truly_dead={len(buckets[BUCKET_TRULY_DEAD])} "
          f"link_broken={len(buckets[BUCKET_LINK_BROKEN])} "
          f"insufficient={len(buckets[BUCKET_INSUFFICIENT])}")
    return 0 if n >= MIN_SAMPLES else 2


if __name__ == "__main__":
    sys.exit(main())
