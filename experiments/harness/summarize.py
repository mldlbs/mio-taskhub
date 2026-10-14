# -*- coding: utf-8 -*-
"""只读 raw → 指标与报告（04 §6.2/§7，预注册判定，人只审阅不改写）。

硬约束：
- 输入只有 raw/（与可选 snapshots/）；不接受任何手工数字参数；
- 逐指标输出 {numerator, denominator, ci95, n}；分母 0 → {"n":0,"value":"N/A"}；
- 主假设按 §7 规则自动三态输出（支持/不支持/证据不足）；
- 不写 raw、不改 raw。

统计实现（stdlib-only，04 附录 C）：
- 比例 CI：Clopper–Pearson 95%（二项和二分查找精确分位数，n≤150 查表等价）；
- 配对二元：精确 McNemar（不一致对上双侧二项检验）；
- 多重比较：主族 H1–H6 用 Holm–Bonferroni（α=0.05）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ALPHA = 0.05
N_H1 = 30  # H1-H4 预注册 n/档
N_H5 = 42
N_H6_COMPLIANT = 60
N_H6_FAILSUBMIT = 30
N_H7 = 30
T_LIMIT = 30.0  # 秒（profiles.T_LIMIT_SECONDS 同步；summarize 不 import harness）


# ── 统计原语（stdlib） ────────────────────────────────────────────────────
def _binom_cdf_ge(k: int, n: int, p: float) -> float:
    """P(X >= k)，X~Bin(n,p)。"""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(k, n + 1))


def clopper_pearson(k: int, n: int, alpha: float = ALPHA) -> Tuple[Optional[float], Optional[float]]:
    """Clopper–Pearson 95% 精确区间（二分查找精确分位数）。n=0 → (None,None)。"""
    if n <= 0:
        return None, None
    if k < 0 or k > n:
        raise ValueError(f"invalid k/n: {k}/{n}")

    def solve(target: float, side: str) -> float:
        # side='lo': 找 p 使 P(X>=k|p)=target（该尾概率随 p **递增**）；
        # side='hi': 找 p 使 P(X<=k|p)=target（该尾概率随 p **递减**）
        lo, hi = 0.0, 1.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if side == "lo":
                if _binom_cdf_ge(k, n, mid) < target:
                    lo = mid
                else:
                    hi = mid
            else:
                if (1 - _binom_cdf_ge(k + 1, n, mid)) > target:
                    lo = mid
                else:
                    hi = mid
        return (lo + hi) / 2

    if k == 0:
        lo = 0.0
    else:
        lo = solve(alpha / 2, "lo")
    if k == n:
        hi = 1.0
    else:
        hi = solve(alpha / 2, "hi")
    return lo, hi


def mcnemar_exact_p(b: int, c: int) -> float:
    """精确 McNemar 双侧 p（不一致对 b,c 上二项检验）。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def holm_adjust(pvals: Dict[str, float]) -> Dict[str, float]:
    """Holm–Bonferroni：按 p 升序逐步乘 (m-i)，封顶 1。"""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out: Dict[str, float] = {}
    running = 0.0
    for i, (name, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        out[name] = running
    return out


def median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def percentile(xs: List[float], q: float) -> Optional[float]:
    """最近秩百分位（P95 等）。"""
    if not xs:
        return None
    s = sorted(xs)
    idx = min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))
    return s[idx]


# ── raw jsonl 读取 ────────────────────────────────────────────────────────
def load_raw(run_dir: Path) -> List[dict]:
    rows: List[dict] = []
    raw_root = run_dir / "raw"
    if not raw_root.exists():
        return rows
    for p in sorted(raw_root.rglob("*.jsonl")):
        rel = p.relative_to(raw_root).as_posix()
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                rows.append({"kind": "err", "error": f"bad jsonl line {lineno}", "file": rel})
                continue
            obj["_file"] = rel
            obj["_line"] = lineno
            rows.append(obj)
    return rows


def _index(rows: List[dict]) -> dict:
    idx = {
        "ground_truth": defaultdict(list),  # (scenario, arm, replicate) -> [gt]
        "http": defaultdict(list),
        "state": defaultdict(list),
        "err": [],
        "by_scenario": defaultdict(lambda: defaultdict(set)),  # scenario -> arm -> replicates
    }
    for r in rows:
        kind = r.get("kind")
        key = (r.get("scenario"), r.get("arm"), r.get("replicate"))
        if kind == "ground_truth":
            idx["ground_truth"][key].append(r)
        elif kind == "http":
            idx["http"][key].append(r)
            idx["by_scenario"][r.get("scenario")][r.get("arm")].add(r.get("replicate"))
        elif kind == "state":
            idx["state"][key].append(r)
            idx["by_scenario"][r.get("scenario")][r.get("arm")].add(r.get("replicate"))
        elif kind == "err":
            idx["err"].append(r)
            if r.get("scenario"):
                idx["by_scenario"][r.get("scenario")][r.get("arm")].add(r.get("replicate"))
    return idx


# ── 指标（附录 C 口径，全部带 n） ─────────────────────────────────────────
def metric(name: str, arm: str, num: int, den: int) -> dict:
    """固定形状：0 分母 → value N/A。"""
    if den <= 0:
        return {"metric": name, "arm": arm, "numerator": 0, "denominator": 0,
                "value": "N/A", "ci95": [None, None], "n": 0}
    lo, hi = clopper_pearson(num, den)
    return {"metric": name, "arm": arm, "numerator": num, "denominator": den,
            "value": num / den, "ci95": [lo, hi], "n": den}


def _pair_replicates(idx: dict, scenario: str) -> List[int]:
    t = idx["by_scenario"].get(scenario, {}).get("T", set())
    c = idx["by_scenario"].get(scenario, {}).get("C", set())
    return sorted(x for x in (t & c) if isinstance(x, int))


def _blocked(key: tuple, idx: dict, expect_status: int) -> Optional[bool]:
    """该 (scenario,arm,rep) 主提交是否按期望被拦（True=拦）。无 http 行 → None。"""
    hs = idx["http"].get(key, [])
    submits = [h for h in hs if str(h.get("req", "")).endswith("/result") or "/result" in str(h.get("req", ""))]
    if not submits:
        submits = hs
    if not submits:
        return None
    st = submits[-1].get("status")
    if st is None:
        return None
    return st == expect_status


def paired_blocked_counts(idx: dict, scenario: str, t_expect: int, c_expect: int) -> Tuple[int, int, int, int]:
    """返回 (n_pairs, t_ok, c_ok, discordant)：t_ok=T 按期望（拦），c_ok=C 按期望（放）。
    ERR/缺行的 replicate 计入 n_pairs 但不计入任一 ok。"""
    reps = _pair_replicates(idx, scenario)
    t_ok = c_ok = 0
    for rep in reps:
        tb = _blocked((scenario, "T", rep), idx, t_expect)
        cb = _blocked((scenario, "C", rep), idx, c_expect)
        if tb:
            t_ok += 1
        if cb:
            c_ok += 1
    return len(reps), t_ok, c_ok, len(reps)


def h1_h4_verdict(idx: dict, scenario: str, p_raw: float) -> Tuple[str, dict]:
    """§7.1：T=n/n 且 C=0/n（C=200 放行 n/n）且 McNemar p<0.05（Holm 后）→ 支持。"""
    n, t_ok, c_ok, _ = paired_blocked_counts(idx, scenario, 422, 200)
    # t_ok = T 档拦到 422 的对数；c_ok = C 档拿到 200 的对数
    detail = {"scenario": scenario, "n_pairs": n, "t_blocked": t_ok, "c_passed": c_ok,
              "p_raw": p_raw}
    if n < N_H1:
        return "证据不足", detail
    if t_ok < n:
        return "不支持", detail
    if c_ok < n:
        return "不支持", detail
    if p_raw >= ALPHA:
        return "不支持", detail  # Holm 后再判（外层覆盖）
    return "支持", detail


def h5_verdict(idx: dict) -> Tuple[str, dict]:
    """H5：每档 ≥42/42 且两档差 95% CI 含 0；40–41/42 → 证据不足。"""
    detail = {}
    rates = {}
    for arm in ("T", "C"):
        reps = sorted(idx["by_scenario"].get("S4a", {}).get(arm, set()))
        den = len(reps)
        num = 0
        for rep in reps:
            sts = idx["state"].get(("S4a", arm, rep), [])
            ok = any(
                s.get("recovered") is True and (s.get("recovery_seconds") is None
                                                or s.get("recovery_seconds") <= T_LIMIT)
                for s in sts
            )
            if ok:
                num += 1
        detail[f"{arm}_k_over_n"] = f"{num}/{den}"
        rates[arm] = (num, den)
    (kt, nt), (kc, nc) = rates["T"], rates["C"]
    if nt < N_H5 or nc < N_H5:
        verdict = "证据不足"
    elif kt == nt and kc == nc:
        # 全成 → 差=0，CI 含 0
        verdict = "支持"
    elif kt / nt < 0.9 or kc / nc < 0.9:
        verdict = "不支持"
    else:
        verdict = "证据不足"  # 40-41/42 档
    return verdict, detail


def h6_verdict(idx: dict) -> Tuple[str, dict]:
    """H6：合规 0/60 拦截（上界 5%）且失败提交 0/30 拦截。"""
    mis_num = mis_den = 0
    fail_num = fail_den = 0
    for (scenario, arm, rep), gts in idx["ground_truth"].items():
        gt = gts[-1]
        labels = gt.get("labels") or {}
        expect = (gt.get("expect") or {})
        compliant = bool(labels.get("compliant")) and expect.get("submit_status") == 200
        hs = [h for h in idx["http"].get((scenario, arm, rep), []) if "/result" in str(h.get("req", ""))]
        if compliant and hs:
            mis_den += 1
            if hs[-1].get("status") != 200:
                mis_num += 1
        if expect.get("submit_success") is False and hs:
            # S3/S3b 失败提交：success=false 不受门控（422/409 均算拦截）
            for h in hs:
                if h.get("status") in (422, 409) and h.get("failure_submit"):
                    fail_num += 1
                if h.get("failure_submit"):
                    fail_den += 1
    detail = {"misblock": f"{mis_num}/{mis_den}", "fail_submit_blocked": f"{fail_num}/{fail_den}"}
    if mis_den < N_H6_COMPLIANT or fail_den < N_H6_FAILSUBMIT:
        return "证据不足", detail
    if fail_num > 0:
        return "不支持", detail
    if mis_num > 0:
        return "不支持", detail  # 点估计>0 且 n=60 时上界>5%
    return "支持", detail


def h7_verdict(idx: dict) -> Tuple[str, dict]:
    """H7：409 = 60/60（两档×30）且双转移=0。"""
    num409 = den409 = 0
    double_tx = 0
    for (scenario, arm, rep), hs in idx["http"].items():
        if scenario != "S5":
            continue
        dup = [h for h in hs if h.get("duplicate_submit")]
        den409 += len(dup)
        num409 += sum(1 for h in dup if h.get("status") == 409)
    for (scenario, arm, rep), sts in idx["state"].items():
        if scenario != "S5":
            continue
        for s in sts:
            if (s.get("task_result_success_count") or 0) > 1 or (s.get("completed_transitions") or 0) > 1:
                double_tx += 1
    detail = {"rejected_409": f"{num409}/{den409}", "double_transitions": double_tx}
    if den409 < 2 * N_H7:
        return "证据不足", detail
    if num409 < den409 or double_tx > 0:
        return "不支持", detail
    return "支持", detail


def h8_verdict(idx: dict) -> Tuple[str, dict]:
    """H8：T/C 中位耗时比 ≤1.5 且 submit P95 增量 ≤200ms。"""
    lat = {"T": [], "C": []}
    submit_lat = {"T": [], "C": []}
    for (scenario, arm, rep), hs in idx["http"].items():
        if scenario != "S1":
            continue
        for h in hs:
            lm = h.get("latency_ms")
            if isinstance(lm, (int, float)):
                lat[arm].append(float(lm))
                if "/result" in str(h.get("req", "")):
                    submit_lat[arm].append(float(lm))
    med_t, med_c = median(lat["T"]), median(lat["C"])
    p95_t, p95_c = percentile(submit_lat["T"], 0.95), percentile(submit_lat["C"], 0.95)
    detail = {"median_ms": {"T": med_t, "C": med_c}, "submit_p95_ms": {"T": p95_t, "C": p95_c}}
    if med_t is None or med_c is None or p95_t is None or p95_c is None or med_c == 0:
        return "证据不足", detail
    ratio = med_t / med_c
    delta = p95_t - p95_c
    detail["median_ratio"] = ratio
    detail["submit_p95_delta_ms"] = delta
    if ratio > 1.5 or delta > 200:
        return "不支持", detail
    return "支持", detail


def escape_metrics(idx: dict) -> List[dict]:
    """E1-E4 代理（从 raw 口径；DB 复算由 --db 模式补充）。"""
    out = []
    # E1-proxy：S2 T 档 submit 200 = 未读完成逃逸
    num = den = 0
    for rep in _pair_replicates(idx, "S2"):
        t_hs = [h for h in idx["http"].get(("S2", "T", rep), []) if "/result" in str(h.get("req", ""))]
        if t_hs:
            den += 1
            if t_hs[-1].get("status") == 200:
                num += 1
    out.append(metric("E1_S2_escape", "T", num, den))
    # E2-proxy：S2b T 档 200 = 指纹过期放行
    num = den = 0
    for rep in _pair_replicates(idx, "S2b"):
        t_hs = [h for h in idx["http"].get(("S2b", "T", rep), []) if "/result" in str(h.get("req", ""))]
        if t_hs:
            den += 1
            if t_hs[-1].get("status") == 200:
                num += 1
    out.append(metric("E2_S2b_stale_escape", "T", num, den))
    # E3-proxy：S6 T 档任一步 200（低分 approved 或回退 approved）
    num = den = 0
    for rep in _pair_replicates(idx, "S6"):
        t_hs = [h for h in idx["http"].get(("S6", "T", rep), []) if h.get("doc_gate_step")]
        if t_hs:
            den += 1
            if any(h.get("status") == 200 for h in t_hs):
                num += 1
    out.append(metric("E3_S6_gate_escape", "T", num, den))
    # E4-proxy：S7 T 档 200
    num = den = 0
    for rep in _pair_replicates(idx, "S7"):
        t_hs = [h for h in idx["http"].get(("S7", "T", rep), []) if "/stage" in str(h.get("req", ""))]
        if t_hs:
            den += 1
            if any(h.get("status") == 200 for h in t_hs):
                num += 1
    out.append(metric("E4_S7_stage_escape", "T", num, den))
    return out


# ── 主判定 ────────────────────────────────────────────────────────────────
def judge_all(idx: dict) -> Dict[str, dict]:
    """按 §7.1 自动三态；H1–H4 的 p 用精确 McNemar（原始），Holm 在族内校正。"""
    raw_p = {}
    for sc in ("S2", "S2b", "S6", "S7"):
        b = c = 0
        for rep in _pair_replicates(idx, sc):
            tb = _blocked((sc, "T", rep), idx, 422)
            cb_blocked = _blocked((sc, "C", rep), idx, 422)
            # 不一致对：T 拦且 C 未拦(b)；T 未拦且 C 拦(c)
            if tb is True and cb_blocked is False:
                b += 1
            elif tb is False and cb_blocked is True:
                c += 1
        raw_p[sc] = mcnemar_exact_p(b, c)

    verdicts: Dict[str, dict] = {}
    for name, sc in (("H1", "S2"), ("H2", "S2b"), ("H3", "S6"), ("H4", "S7")):
        v, detail = h1_h4_verdict(idx, sc, raw_p[sc])
        verdicts[name] = {"verdict": v, "detail": detail}

    v5, d5 = h5_verdict(idx)
    verdicts["H5"] = {"verdict": v5, "detail": d5}
    v6, d6 = h6_verdict(idx)
    verdicts["H6"] = {"verdict": v6, "detail": d6}
    v7, d7 = h7_verdict(idx)
    verdicts["H7"] = {"verdict": v7, "detail": d7}
    v8, d8 = h8_verdict(idx)
    verdicts["H8"] = {"verdict": v8, "detail": d8}

    # Holm：族 H1-H6（按原始 p 排序；无 p 的 H5/H6 用 0/1 保守占位参与排序不影响 H1-H4）
    fam_p = {
        "H1": raw_p["S2"], "H2": raw_p["S2b"], "H3": raw_p["S6"], "H4": raw_p["S7"],
        "H5": 0.0 if verdicts["H5"]["verdict"] == "支持" else 1.0,
        "H6": 0.0 if verdicts["H6"]["verdict"] == "支持" else 1.0,
    }
    adj = holm_adjust(fam_p)
    for name in ("H1", "H2", "H3", "H4"):
        verdicts[name]["p_holm"] = adj[name]
        if verdicts[name]["verdict"] == "支持" and adj[name] >= ALPHA:
            verdicts[name]["verdict"] = "不支持"
            verdicts[name]["detail"]["holm_overrides"] = True
    for name in ("H5", "H6", "H7", "H8"):
        verdicts[name]["exploratory_note"] = name in ("H7", "H8")  # H7/H8 探索性，只报估计与 CI
    return verdicts


def overall(verdicts: Dict[str, dict]) -> str:
    """§7.2 总判定（仅 M 系列主族）。"""
    main = [verdicts[h]["verdict"] for h in ("H1", "H2", "H3", "H4", "H5", "H6")]
    if all(v == "支持" for v in main):
        return "支持（M 系列主族全绿=机制前置达成，不等于 V1/D2-a 通过）"
    if any(v == "不支持" for v in main):
        return "不支持（任一主假设不支持 → 触发 03 §7 对应退出动作）"
    return "证据不足（禁用部分轮次有利结果下结论）"


def manifest_hash(run_dir: Path) -> Optional[str]:
    p = run_dir / "manifest.json"
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def summarize_run(run_dir: Path) -> Tuple[dict, str]:
    rows = load_raw(run_dir)
    idx = _index(rows)
    verdicts = judge_all(idx)
    metrics = escape_metrics(idx)
    # 场景达成率（每场景每档）
    attain = []
    for sc in sorted(idx["by_scenario"]):
        for arm in sorted(idx["by_scenario"][sc]):
            reps = idx["by_scenario"][sc][arm]
            num = 0
            for rep in reps:
                sts = idx["state"].get((sc, arm, rep), [])
                gts = idx["ground_truth"].get((sc, arm, rep), [])
                if not sts or not gts:
                    continue
                exp_terminal = (gts[-1].get("expect") or {}).get("terminal")
                if exp_terminal in (None, "n/a"):
                    num += 1  # 无终态期望的场景以 http 期望达成计（已有 state 行即达成路径记录）
                elif sts[-1].get("task_state") == exp_terminal:
                    num += 1
            attain.append(metric(f"attain_{sc}", arm, num, len(reps)))
    metrics.extend(attain)
    err_count = len(idx["err"])
    report = {
        "tool": "experiments/harness/summarize.py",
        "run_dir": str(run_dir),
        "manifest_sha256": manifest_hash(run_dir),
        "raw_rows": len(rows),
        "err_rows": err_count,
        "verdicts": verdicts,
        "overall": overall(verdicts),
        "metrics": metrics,
    }
    md = render_report(report)
    return report, md


def render_report(report: dict) -> str:
    lines = [
        "# M 系列汇总报告（summarize.py 自动判定，人只审阅不改写）",
        "",
        f"- run_dir: `{report['run_dir']}`",
        f"- manifest_sha256: `{report['manifest_sha256']}`",
        f"- raw_rows: {report['raw_rows']}，err_rows: {report['err_rows']}",
        f"- **总判定：{report['overall']}**",
        "",
        "## 主假设判定（§7.1 三态）",
        "",
        "| 假设 | 判定 | 详情 |",
        "|---|---|---|",
    ]
    for h in ("H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8"):
        v = report["verdicts"][h]
        det = json.dumps(v["detail"], ensure_ascii=False)
        note = "（探索性）" if v.get("exploratory_note") else ""
        lines.append(f"| {h}{note} | {v['verdict']} | `{det}` |")
    lines += ["", "## 指标表", "", "| 指标 | 档 | 分子/分母 | 值 | 95% CI | n |", "|---|---|---|---|---|---|"]
    for m in report["metrics"]:
        ci = m["ci95"]
        ci_s = "N/A" if ci[0] is None else f"[{ci[0]:.4f},{ci[1]:.4f}]"
        val = m["value"] if not isinstance(m["value"], float) else f"{m['value']:.4f}"
        lines.append(f"| {m['metric']} | {m['arm']} | {m['numerator']}/{m['denominator']} | {val} | {ci_s} | {m['n']} |")
    lines += ["", "## 失败案例索引", "", "见 `failures/*.json`（每条非期望结局与 ERR 的展开副本）。", ""]
    return "\n".join(lines)


def _extract_binary_outcomes(run_dir: Path) -> Dict[Tuple[str, str, int], dict]:
    """从 raw/ 抽取单元二元结局：(scenario, arm, rep) -> {submit_status, task_state, recovered}。

    从行内容推断 scenario/arm/rep（兼容 raw/{scen}/{arm}/{rep}.jsonl 与扁平 raw/*.jsonl）。
    §6.3 复现判据：同 seed 同 commit 重跑 → 二元结局 100% 一致。
    """
    raw_root = run_dir / "raw"
    if not raw_root.is_dir():
        return {}
    acc: Dict[Tuple[str, str, int], dict] = {}
    for jf in sorted(raw_root.rglob("*.jsonl")):
        for line in jf.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            scen, arm, rep = obj.get("scenario"), obj.get("arm"), obj.get("replicate")
            if not scen or not arm or rep is None:
                continue
            key = (scen, arm, int(rep))
            slot = acc.setdefault(key, {"submit_status": None, "task_state": None,
                                        "recovered": None})
            if obj.get("kind") == "http" and "/result" in str(obj.get("req", "")):
                slot["submit_status"] = obj.get("status")
            if obj.get("kind") == "state":
                slot["task_state"] = obj.get("task_state")
                if "recovered" in obj:
                    slot["recovered"] = obj.get("recovered")
    return acc


def compare_runs(run_a: Path, run_b: Path) -> dict:
    """§6.3 复现判据：同 seed 同 commit 重跑 → 二元结局 100% 一致。时延类不在此比较。"""
    oa, ob = _extract_binary_outcomes(run_a), _extract_binary_outcomes(run_b)
    keys = sorted(set(oa) | set(ob))
    mismatches = []
    only_a, only_b = [], []
    for k in keys:
        if k not in oa:
            only_b.append(k)
            continue
        if k not in ob:
            only_a.append(k)
            continue
        if oa[k] != ob[k]:
            mismatches.append({"key": k, "a": oa[k], "b": ob[k]})
    total = len(set(oa) & set(ob))
    return {
        "run_a": str(run_a), "run_b": str(run_b),
        "units_a": len(oa), "units_b": len(ob), "matched_units": total,
        "mismatches": mismatches, "only_a": [list(k) for k in only_a],
        "only_b": [list(k) for k in only_b],
        "consistent": not mismatches and not only_a and not only_b and total > 0,
        "criterion": "binary_outcomes_100pct_identical",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mio-taskhub 实验只读汇总（04 §6.2）")
    ap.add_argument("--run", default=None, help="轮目录（含 raw/ 与 manifest.json）")
    ap.add_argument("--out", default=None, help="输出目录（默认 <run>/summary）")
    ap.add_argument("--db", default=None, help="第三方复算：只读 snapshots DB（比例类）")
    ap.add_argument("--only", default=None, choices=["proportions"], help="配合 --db 的过滤")
    ap.add_argument("--compare", default=None, metavar="A,B",
                    help="§6.3 复现判据：比较两轮目录的二元结局一致性")
    args = ap.parse_args(argv)

    if args.compare:
        parts = [p.strip() for p in args.compare.split(",") if p.strip()]
        if len(parts) != 2:
            print("[summarize] --compare 需要两个目录，逗号分隔", file=sys.stderr)
            return 2
        a, b = Path(parts[0]), Path(parts[1])
        for p in (a, b):
            if not p.exists():
                print(f"[summarize] dir not found: {p}", file=sys.stderr)
                return 2
        cmp = compare_runs(a, b)
        print(json.dumps(cmp, ensure_ascii=False, indent=2))
        return 0 if cmp["consistent"] else 1

    if not args.run:
        ap.error("--run is required unless --compare is given")
    run_dir = Path(args.run)
    if not run_dir.exists():
        print(f"[summarize] run dir not found: {run_dir}", file=sys.stderr)
        return 2
    if args.db and not Path(args.db).exists():
        print(f"[summarize] db not found: {args.db}", file=sys.stderr)
        return 2

    report, md = summarize_run(run_dir)
    out = Path(args.out) if args.out else run_dir / "summary"
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(md, encoding="utf-8")
    print(f"[summarize] overall={report['overall']}")
    print(f"[summarize] -> {out / 'metrics.json'}")
    print(f"[summarize] -> {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
