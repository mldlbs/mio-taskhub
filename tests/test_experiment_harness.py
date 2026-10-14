# -*- coding: utf-8 -*-
"""harness 单元测试（T1.2 DoD：summarize 自动判据 0 假阳性/0 假阴性）。

覆盖：
  1. profiles 档位表（T/C 唯一差异 = 两开关 + force 路径，04 §2.1）；
  2. 随机化（AB/BA 掷币与场景顺序，04 §5.2）确定性；
  3. 统计原语（Clopper–Pearson 边界、精确 McNemar、Holm 单调性）；
  4. summarize 三态判定：支持/不支持/证据不足各路径 + n 门槛（0 FP/0 FN 穷举）；
  5. 存活探针（两档子进程实跑：T=422/422、C=200/200）；
  6. S2 单元子进程实跑（ground_truth 先写、T 拦 C 放）。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "experiments" / "harness"
sys.path.insert(0, str(HARNESS))

import profiles  # noqa: E402
import run_m  # noqa: E402
import summarize  # noqa: E402

PY = str(REPO / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable


# ── 1. profiles ───────────────────────────────────────────────────────────
def test_arm_env_only_diff_is_two_switches():
    t, c = profiles.arm_env("T"), profiles.arm_env("C")
    assert "MIO_READ_GATE" not in t, "T 档不得设 MIO_READ_GATE（默认开）"
    assert "MIO_RATCHET_DISABLED" not in t, "T 档不得设 MIO_RATCHET_DISABLED"
    assert c["MIO_READ_GATE"] == "0"
    assert c["MIO_RATCHET_DISABLED"] == "1"
    diff = {k for k in set(t) | set(c) if t.get(k) != c.get(k)}
    assert diff == {"MIO_READ_GATE", "MIO_RATCHET_DISABLED"}, diff


def test_noise_env_and_probe_expect():
    for arm in ("T", "C"):
        env = profiles.arm_env(arm)
        assert env["MIO_OBSERVER_AUTOSTART"] == "0"
        assert env["MIO_TASKHUB_RATE_LIMIT"] == "100000"
    assert profiles.PROBE_EXPECT["unread_submit"] == {"T": 422, "C": 200}
    assert profiles.PROBE_EXPECT["ratchet_rollback"] == {"T": 422, "C": 200}


def test_scenario_n_registered():
    assert profiles.SCENARIO_N["S4a"] == 42
    assert profiles.SCENARIO_N["S6"] == 30
    assert set(profiles.SCENARIO_N) == {
        "S1", "S2", "S2b", "S3", "S3b", "S4a", "S4b", "S5", "S6", "S7"}


# ── 2. 随机化 ─────────────────────────────────────────────────────────────
def test_coin_flip_deterministic_and_balanced():
    a = [run_m.coin_flip("20261010", "S1", r) for r in range(1, 61)]
    b = [run_m.coin_flip("20261010", "S1", r) for r in range(1, 61)]
    assert a == b, "掷币必须按 seed 确定"
    n_t = a.count("T")
    assert 18 <= n_t <= 42, f"60 次应近均衡，得 T={n_t}"
    # 不同 seed 应产生不同序列（弱检验）
    c = [run_m.coin_flip("99999999", "S1", r) for r in range(1, 61)]
    assert a != c


def test_shuffled_scenarios_deterministic():
    order1 = run_m.shuffled_scenarios("20261010", list(run_m.SCENARIOS_DEFAULT))
    order2 = run_m.shuffled_scenarios("20261010", list(run_m.SCENARIOS_DEFAULT))
    assert order1 == order2
    assert sorted(order1) == sorted(run_m.SCENARIOS_DEFAULT)


# ── 3. 统计原语 ───────────────────────────────────────────────────────────
def test_clopper_pearson_boundaries():
    lo, hi = summarize.clopper_pearson(0, 60)
    assert lo == 0.0
    assert hi == pytest.approx(3 / 60, abs=0.01), "rule of three：上界≈5%"
    lo, hi = summarize.clopper_pearson(60, 60)
    assert hi == 1.0
    assert lo > 0.9, f"60/60 下界应 >0.9，得 {lo}"
    assert summarize.clopper_pearson(0, 0) == (None, None)


def test_mcnemar_exact_extreme_p():
    p = summarize.mcnemar_exact_p(30, 0)
    assert p < 0.05, f"30/0 不一致对必须显著，p={p}"
    p2 = summarize.mcnemar_exact_p(15, 15)
    assert p2 == pytest.approx(1.0), "对称不一致对 p≈1"


def test_holm_adjust_monotone_and_capped():
    adj = summarize.holm_adjust({"H1": 0.001, "H2": 0.01, "H3": 0.04, "H4": 0.5})
    assert adj["H1"] <= adj["H2"] <= adj["H3"] <= adj["H4"]
    assert adj["H4"] == pytest.approx(0.5 * 1), "最大 p×1"
    assert all(v <= 1.0 for v in adj.values())


def test_median_percentile():
    assert summarize.median([3, 1, 2]) == 2
    assert summarize.median([1, 2, 3, 4]) == 2.5
    assert summarize.percentile(list(range(1, 101)), 0.95) == 95


# ── 4. summarize 三态判定（0 FP/0 FN 穷举） ───────────────────────────────
def _mk_rows(scenario, arm, reps, t_status=None, c_status=None, extra_state=None):
    """构造最小 raw 行集。t_status/c_status: replicate -> main submit status。"""
    rows = []
    for rep in reps:
        for arm_name, status in (("T", t_status), ("C", c_status)):
            if status is None:
                continue
            rows.append({"kind": "ground_truth", "scenario": scenario, "arm": arm_name,
                         "replicate": rep, "seed": "s",
                         "expect": {"submit_status": status},
                         "labels": {"should_read": arm_name == "T", "compliant": False}})
            rows.append({"kind": "http", "scenario": scenario, "arm": arm_name,
                         "replicate": rep, "req": "POST /runs/{id}/result",
                         "status": status[rep] if isinstance(status, dict) else status,
                         "latency_ms": 1.0})
            st = {"kind": "state", "scenario": scenario, "arm": arm_name,
                  "replicate": rep, "task_state": "completed", "stage": "review",
                  "runs": 1}
            if extra_state:
                st.update(extra_state)
            rows.append(st)
    return rows


def _idx_from(rows):
    return summarize._index(rows)


def _write_round(tmp_path, rows, probes_ok=True):
    run_dir = tmp_path / "round"
    (run_dir / "raw").mkdir(parents=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    (run_dir / "raw" / "S.jsonl").write_text("\n".join(lines), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps(
        {"master_seed": "s", "probes_passed": probes_ok}), encoding="utf-8")
    return run_dir


def test_h1_support_full_separation():
    reps = list(range(1, 31))
    rows = _mk_rows("S2", None, reps, t_status=422, c_status=200)
    idx = _idx_from(rows)
    v, d = summarize.h1_h4_verdict(idx, "S2", summarize.mcnemar_exact_p(30, 0))
    assert v == "支持", d


def test_h1_not_support_single_escape():
    reps = list(range(1, 31))
    rows = _mk_rows("S2", None, reps, t_status=422, c_status=200)
    # 1 例 T 档逃逸（200）
    for r in rows:
        if r["kind"] == "http" and r["arm"] == "T" and r["replicate"] == 7:
            r["status"] = 200
    idx = _idx_from(rows)
    v, d = summarize.h1_h4_verdict(idx, "S2", summarize.mcnemar_exact_p(29, 0))
    assert v == "不支持", d


def test_h1_not_support_c_also_blocked():
    reps = list(range(1, 31))
    rows = _mk_rows("S2", None, reps, t_status=422, c_status=422)
    idx = _idx_from(rows)
    v, d = summarize.h1_h4_verdict(idx, "S2", summarize.mcnemar_exact_p(0, 30))
    assert v == "不支持", d  # C 档没放行 → 差异不成立


def test_h1_insufficient_when_n_below_30():
    reps = list(range(1, 10))  # n=9
    rows = _mk_rows("S2", None, reps, t_status=422, c_status=200)
    idx = _idx_from(rows)
    v, d = summarize.h1_h4_verdict(idx, "S2", summarize.mcnemar_exact_p(9, 0))
    assert v == "证据不足", d  # 即便方向完美，n 不达也必须三态=证据不足


def test_h1_not_support_when_p_not_significant():
    reps = list(range(1, 31))
    rows = _mk_rows("S2", None, reps, t_status=422, c_status=200)
    # 方向全对但给一个不显著 p（构造：外部传入 p=0.2）
    idx = _idx_from(rows)
    v, d = summarize.h1_h4_verdict(idx, "S2", 0.2)
    assert v == "不支持", d


def test_h5_verdict_matrix():
    def rows_for(k_t, k_c, n_t=42, n_c=42):
        rows = []
        for arm, k, n in (("T", k_t, n_t), ("C", k_c, n_c)):
            for rep in range(1, n + 1):
                ok = rep <= k
                rows.append({"kind": "state", "scenario": "S4a", "arm": arm,
                             "replicate": rep, "task_state": "completed",
                             "recovered": ok,
                             "recovery_seconds": 10.0 if ok else 999.0})
        return rows

    assert summarize.h5_verdict(_idx_from(rows_for(42, 42)))[0] == "支持"
    assert summarize.h5_verdict(_idx_from(rows_for(42, 36)))[0] == "不支持"  # 36/42=85.7%<90%
    assert summarize.h5_verdict(_idx_from(rows_for(41, 42)))[0] == "证据不足"  # 41/42 档
    assert summarize.h5_verdict(_idx_from(rows_for(20, 20, n_t=20, n_c=20)))[0] == "证据不足"  # n 未达


def test_h6_fail_submit_block_is_fatal():
    reps = list(range(1, 31))
    rows = []
    # 合规 60（S1+S3b 各 30，两臂…简化：S1 两臂合规 T60）失败提交 30 全过
    for rep in reps:
        rows.append({"kind": "ground_truth", "scenario": "S1", "arm": "T",
                     "replicate": rep, "expect": {"submit_status": 200},
                     "labels": {"compliant": True}})
        rows.append({"kind": "http", "scenario": "S1", "arm": "T", "replicate": rep,
                     "req": "POST /runs/{id}/result", "status": 200, "latency_ms": 1})
    for rep in reps:
        rows.append({"kind": "ground_truth", "scenario": "S3", "arm": "T",
                     "replicate": rep, "expect": {"submit_success": False},
                     "labels": {"compliant": False}})
        rows.append({"kind": "http", "scenario": "S3", "arm": "T", "replicate": rep,
                     "req": "POST /runs/{id}/result#fail1", "status": 200,
                     "failure_submit": True, "latency_ms": 1})
    v, d = summarize.h6_verdict(_idx_from(rows))
    assert v == "证据不足", d  # 合规只 30 <60

    # 补足 S3b 30 合规 + 让一条失败提交被 422
    for rep in reps:
        rows.append({"kind": "ground_truth", "scenario": "S3b", "arm": "T",
                     "replicate": rep, "expect": {"submit_status": 200},
                     "labels": {"compliant": True}})
        rows.append({"kind": "http", "scenario": "S3b", "arm": "T", "replicate": rep,
                     "req": "POST /runs/{id}/result", "status": 200, "latency_ms": 1})
    rows.append({"kind": "http", "scenario": "S3", "arm": "T", "replicate": 1,
                 "req": "POST /runs/{id}/result#fail1", "status": 422,
                 "failure_submit": True, "latency_ms": 1})
    v, d = summarize.h6_verdict(_idx_from(rows))
    assert v == "不支持", d  # 失败提交被拦 ≥1 → 不支持


def test_h7_double_transition_fails():
    rows = []
    for arm in ("T", "C"):
        for rep in range(1, 31):
            rows.append({"kind": "http", "scenario": "S5", "arm": arm, "replicate": rep,
                         "req": "POST /runs/{id}/result#dup", "status": 409,
                         "duplicate_submit": True, "latency_ms": 1})
            rows.append({"kind": "state", "scenario": "S5", "arm": arm,
                         "replicate": rep, "task_state": "completed",
                         "task_result_success_count": 1, "completed_transitions": 1})
    v, d = summarize.h7_verdict(_idx_from(rows))
    assert v == "支持", d
    rows[1]["task_result_success_count"] = 2  # 任一双转移
    v, d = summarize.h7_verdict(_idx_from(rows))
    assert v == "不支持", d


def test_metric_zero_denominator_is_na():
    m = summarize.metric("E1", "T", 0, 0)
    assert m["value"] == "N/A" and m["n"] == 0
    m2 = summarize.metric("E1", "T", 0, 30)
    assert m2["value"] == 0.0
    assert m2["ci95"][1] > 0  # 上界 >0


def test_end_to_end_summarize_on_synthetic(tmp_path):
    reps = list(range(1, 31))
    rows = _mk_rows("S2", None, reps, t_status=422, c_status=200)
    rows += _mk_rows("S1", None, reps, t_status=200, c_status=200)
    run_dir = _write_round(tmp_path, rows)
    report, md = summarize.summarize_run(run_dir)
    assert report["verdicts"]["H1"]["verdict"] == "支持"
    assert "总判定" in md
    assert report["manifest_sha256"]
    # 空 run：全部证据不足，不崩
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    r2, _ = summarize.summarize_run(empty_dir)
    assert r2["verdicts"]["H1"]["verdict"] == "证据不足"


def test_summarize_never_writes_raw(tmp_path):
    reps = [1]
    rows = _mk_rows("S2", None, reps, t_status=422, c_status=200)
    run_dir = _write_round(tmp_path, rows)
    before = {p: p.read_bytes() for p in run_dir.rglob("*.jsonl")}
    summarize.summarize_run(run_dir)
    after = {p: p.read_bytes() for p in run_dir.rglob("*.jsonl")}
    assert before == after, "summarize 必须只读 raw"


# ── 5. 存活探针（子进程实跑两档） ─────────────────────────────────────────
def _run_child(script_args, env_extra, timeout=180):
    env = dict(os.environ)
    for k in ("MIO_READ_GATE", "MIO_RATCHET_DISABLED", "MIO_TASKHUB_DB"):
        env.pop(k, None)
    env.update(env_extra)
    return subprocess.run([PY, str(HARNESS / "run_m.py"), *script_args],
                          cwd=str(REPO), env=env, capture_output=True,
                          text=True, timeout=timeout)


@pytest.mark.slow
def test_probe_both_arms(tmp_path):
    results = {}
    for arm in ("T", "C"):
        db = tmp_path / f"probe_{arm}.sqlite"
        jsonl = tmp_path / f"probe_{arm}.jsonl"
        proc = _run_child(["--_probe", "--arm", arm, "--db", str(db),
                           "--jsonl", str(jsonl)],
                          profiles.full_child_env(dict(os.environ), arm))
        assert proc.returncode == 0, proc.stderr[-3000:]
        results[arm] = json.loads(proc.stdout.strip().splitlines()[-1])
    assert results["T"]["unread_submit"] == 422, results["T"]
    assert results["T"]["ratchet_rollback"] == 422, results["T"]
    assert results["C"]["unread_submit"] == 200, results["C"]
    assert results["C"]["ratchet_rollback"] == 200, results["C"]


@pytest.mark.slow
def test_s2_unit_live(tmp_path):
    """S2 子进程单元：ground_truth 先写、T 拦 422、C 放 200。"""
    statuses = {}
    for arm, expect in (("T", 422), ("C", 200)):
        db = tmp_path / f"s2_{arm}.sqlite"
        jsonl = tmp_path / "raw" / "S2" / arm / "1.jsonl"
        proc = _run_child(["--_unit", "--scenario", "S2", "--arm", arm,
                           "--replicate", "1", "--seed", "20261010",
                           "--db", str(db), "--jsonl", str(jsonl),
                           "--server-logs", str(tmp_path / "logs")],
                          profiles.full_child_env(dict(os.environ), arm))
        assert proc.returncode == 0, proc.stderr[-3000:]
        lines = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l]
        assert lines[0]["kind"] == "ground_truth", "ground_truth 必须先写（04 §3.1）"
        assert lines[0]["expect"]["submit_status"] == expect
        submit = [l for l in lines if l["kind"] == "http"][-1]
        assert submit["status"] == expect, f"{arm}: {submit}"
        statuses[arm] = submit["status"]
    assert statuses == {"T": 422, "C": 200}


# ── 6. CLI 可用（验收命令） ───────────────────────────────────────────────
def test_run_m_help_and_summarize_help():
    for script, extra in (("run_m.py", ["--help"]), ("summarize.py", ["--help"]),
                          ("run_b.py", ["--help"])):
        proc = subprocess.run([PY, str(HARNESS / script), *extra],
                              cwd=str(REPO), capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"{script} --help failed: {proc.stderr}"
        assert proc.stdout.strip(), f"{script} --help 无输出"


def test_run_b_refuses_without_allow_flag(tmp_path):
    env = dict(os.environ)
    env["MIO_TASKHUB_DB"] = str(tmp_path / "b.sqlite")
    proc = subprocess.run([PY, str(HARNESS / "run_b.py"), "--tasks", "2"],
                          cwd=str(REPO), env=env, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 6, proc.stderr[-1000:]
    assert "P4" in proc.stderr or "allow-b" in proc.stderr
