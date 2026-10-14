# -*- coding: utf-8 -*-
"""M 系列执行器（04 §6.1/§6.3；预注册参数，未经 §9 批准不得运行）。

父进程编排：
  - 每轮创建不可变轮目录 + manifest.json（seed/commit/env 表/探针结果）；
  - 档位探针（§2.3）双绿才开跑，否则整轮作废；
  - 每个 (scenario, arm, replicate) 为一个**子进程单元**（进程模型，DB 每档累积）；
  - 配对 AB/BA：seed=SHA256(master||scenario||rep) 掷币；场景顺序按 seed 打乱；
  - S4b 默认拒绝（需 --allow-s4b，对应 §9 P3）。

子进程单元（--_unit）：设置档位 env 后再导入 app，跑场景步骤，按附录 B 写 jsonl
（ground_truth 先写、http 逐请求、state 收尾）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

import profiles  # noqa: E402

PY = str(REPO / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable

SCENARIOS_DEFAULT = ["S1", "S2", "S2b", "S3", "S3b", "S4a", "S5", "S6", "S7"]


# ── 随机化（§5.2，预注册） ────────────────────────────────────────────────
def coin_flip(master_seed: str, scenario: str, replicate: int) -> str:
    """True=T 先（AB），False=C 先（BA）。"""
    h = hashlib.sha256(f"{master_seed}||{scenario}||{replicate}".encode()).digest()
    return "T" if h[0] & 1 else "C"


def shuffled_scenarios(master_seed: str, scen_list: List[str]) -> List[str]:
    items = list(scen_list)
    random.Random(hashlib.sha256(f"{master_seed}||order".encode()).hexdigest()).shuffle(items)
    return items


# ── 父进程 ────────────────────────────────────────────────────────────────
def _git_info() -> dict:
    def _run(cmd: List[str]) -> str:
        import subprocess as sp
        try:
            return sp.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    head = _run(["git", "rev-parse", "HEAD"])
    status = _run(["git", "status", "--porcelain"])
    dirty_lines = [l for l in status.splitlines() if l.strip()]
    return {"head": head, "dirty": bool(dirty_lines), "dirty_files": dirty_lines[:20]}


def _write_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def run_probe(round_dir: Path, arm: str, jsonl: Path) -> Dict[str, bool]:
    """档位探针（§2.3）：未读 submit + 棘轮回退。返回 {name: ok}。"""
    results = {}
    # 探针在子进程内执行（env 生效于进程内 app）
    proc = subprocess.run(
        [PY, str(Path(__file__).resolve()), "--_probe", "--arm", arm,
         "--db", str(round_dir / "raw" / "db" / f"probe_{arm}.sqlite"),
         "--jsonl", str(jsonl)],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
        env={**os.environ, **profiles.full_child_env(dict(os.environ), arm)},
    )
    if proc.returncode != 0:
        return {"probe_err": False}
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    return out


def run_unit(round_dir: Path, scenario: str, arm: str, replicate: int, seed: str,
             jsonl: Path, *, allow_s4b: bool = False) -> int:
    cmd = [PY, str(Path(__file__).resolve()), "--_unit",
           "--scenario", scenario, "--arm", arm, "--replicate", str(replicate),
           "--seed", seed, "--db", str(round_dir / "raw" / "db" / f"{arm}.sqlite"),
           "--jsonl", str(jsonl),
           "--server-logs", str(round_dir / "raw" / "server" / arm)]
    if allow_s4b:
        cmd += ["--allow-s4b"]
    s4 = scenario in ("S4a", "S4b")
    env = {**os.environ, **profiles.full_child_env(dict(os.environ), arm, s4=s4)}
    env["MIO_TASKHUB_DB"] = str(round_dir / "raw" / "db" / f"{arm}.sqlite")
    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True,
                          timeout=300)
    if proc.returncode != 0:
        _write_jsonl(jsonl, {"kind": "err", "scenario": scenario, "arm": arm,
                             "replicate": replicate, "phase": "unit",
                             "error": f"rc={proc.returncode}",
                             "stderr": (proc.stderr or "")[-2000:]})
    return proc.returncode


def cmd_run(args: argparse.Namespace) -> int:
    seed = str(args.seed)
    scen_list = profiles.SCENARIO_N.keys() if args.scenarios == "all" else args.scenarios.split(",")
    scen_list = [s for s in scen_list if profiles.SCENARIO_N.get(s)]
    if "S4b" in scen_list and not args.allow_s4b:
        print("[run_m] S4b 跳过（需 §9 P3 批准，--allow-s4b 显式开启）")
        scen_list = [s for s in scen_list if s != "S4b"]

    n_override = args.replicates if args.replicates else None

    for rnd in range(1, args.rounds + 1):
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        git = _git_info()
        commit8 = (git["head"] or "unknown")[:8]
        round_dir = Path(args.out) / f"{ts}_{commit8}_seed{seed}_round{rnd}"
        (round_dir / "raw" / "db").mkdir(parents=True, exist_ok=True)
        (round_dir / "failures").mkdir(exist_ok=True)
        (round_dir / "snapshots").mkdir(exist_ok=True)

        # manifest（创建后只读；内容哈希由 summarize 记入 report）
        started = datetime.now(timezone.utc).isoformat()
        n_eff = {s: (n_override or profiles.SCENARIO_N[s]) for s in scen_list}
        manifest = {
            "master_seed": seed, "round": rnd, "commit": git, "python": sys.version,
            "n": n_eff, "R": args.rounds, "probe_only": bool(args.probe_only),
            "env": {a: profiles.arm_env(a) for a in profiles.ARM_KEYS},
            "s4_env": profiles.S4_ENV, "t_limit_s": profiles.T_LIMIT_SECONDS,
            "started_at": started, "probes": None, "scenarios": list(scen_list),
        }
        (round_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        # 档位探针（两档各一；§2.3 不满足 → 整轮无效）
        probe_ok = True
        probe_result = {}
        for arm in profiles.ARM_KEYS:
            pj = round_dir / "raw" / f"probe_{arm}.jsonl"
            r = run_probe(round_dir, arm, pj)
            probe_result[arm] = r
            expect = profiles.PROBE_EXPECT
            if r.get("unread_submit") != expect["unread_submit"][arm]:
                probe_ok = False
            if r.get("ratchet_rollback") != expect["ratchet_rollback"][arm]:
                probe_ok = False
        manifest["probes"] = probe_result
        manifest["probes_passed"] = probe_ok
        (round_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if not probe_ok:
            aborted = round_dir.parent / "aborted" / round_dir.name
            aborted.parent.mkdir(parents=True, exist_ok=True)
            round_dir.rename(aborted)
            print(f"[run_m] round {rnd} 作废：档位探针失败 -> {aborted}")
            if args.strict_probes:
                return 3
            continue
        print(f"[run_m] round {rnd} probes GREEN -> {round_dir}")

        if args.probe_only:
            manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
            (round_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[run_m] round {rnd} probe-only complete -> {round_dir}")
            continue

        order = shuffled_scenarios(seed, list(scen_list))
        for scenario in order:
            n = n_eff[scenario]
            reps = list(range(1, n + 1))
            for rep in reps:
                first = coin_flip(seed, scenario, rep)
                for arm in (first, "C" if first == "T" else "T"):
                    jsonl = round_dir / "raw" / scenario / arm / f"{rep}.jsonl"
                    rc = run_unit(round_dir, scenario, arm, rep, seed, jsonl,
                                  allow_s4b=args.allow_s4b)
                    if rc != 0 and args.stop_on_err:
                        print(f"[run_m] unit failed: {scenario}/{arm}/{rep}")
                        return 4
            print(f"[run_m] round {rnd} {scenario} done (n={n}/arm)")

        # 轮末快照（每档 DB 副本）
        for arm in profiles.ARM_KEYS:
            src = round_dir / "raw" / "db" / f"{arm}.sqlite"
            if src.exists():
                (round_dir / "snapshots" / f"db_{arm}.sqlite").write_bytes(src.read_bytes())
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        (round_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[run_m] round {rnd} complete -> {round_dir}")

    return 0


# ── 子进程：探针 ──────────────────────────────────────────────────────────
def _client_ctx():
    """进程内 TestClient（进入时启动 lifespan）。调用方须先设好 env 与 DB。"""
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    return TestClient(app)


def _init_db() -> None:
    from mio_taskhub.db import init_db
    init_db()


def cmd_probe(args: argparse.Namespace) -> int:
    _init_db()
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    out: Dict[str, bool] = {}
    with TestClient(app) as client:
        # 探针 1：未读 submit
        tid = _mk_task(client, title=f"probe-unread-{args.arm}")
        r = client.post(f"/api/v1/tasks/claim", params={"agent": f"probe-{args.arm}", "task_id": tid})
        rid = r.json()["id"]
        sub = client.post(f"/api/v1/runs/{rid}/result",
                          params={"agent": f"probe-{args.arm}"},
                          json={"success": True, "result": "probe", "run_id": rid})
        out["unread_submit"] = sub.status_code
        # 探针 2：棘轮回退（tests/test_ratchet.py 模式：GOOD→review 建基线 → WEAK→approved 拦截）
        tid2 = _mk_task(client, title=f"probe-ratchet-{args.arm}")
        _write_doc(client, tid2, "spec", QUALIFIED_SPEC)
        st1 = client.post(f"/api/v1/tasks/{tid2}/doc/spec/status",
                          json={"state": "review"})
        ok_first = st1.status_code == 200
        _write_doc(client, tid2, "spec", BAD_SPEC)  # 降级到 95 分
        st2 = client.post(f"/api/v1/tasks/{tid2}/doc/spec/status",
                          json={"state": "approved"})
        out["ratchet_rollback"] = st2.status_code
        out["first_review_ok"] = ok_first
        # 记录 st1 状态码便于诊断
        out["st1_status"] = st1.status_code
    print(json.dumps(out))
    return 0


# ── 子进程：场景单元 ──────────────────────────────────────────────────────
# GOOD/WEAK 模式复用 tests/test_ratchet.py：GOOD=100分建基线，WEAK=95分触发回退
DOC_INFO = ("## 文档信息\n| 项 | 值 |\n|---|---|\n| 文档版本 | v0.1 |\n"
            "| 最后更新 | 2026-09-23 |\n| 状态 | draft |\n| 负责人 | 张 |\n| 适用范围 | x |\n")
QUALIFIED_SPEC = f"# Spec\n{DOC_INFO}## 1. 模块职责与边界\n做登录。\n## 2. 详细设计\n用 JWT。\n"
BAD_SPEC = QUALIFIED_SPEC + "## 3. 附注\n<!-- 待补 -->\n"  # 0 error/1 warn → 95 分


def _mk_task(client, *, title: str, workspace: Optional[Path] = None,
             kinds=("spec", "api", "requirement"), qualified: bool = True,
             stage: str = "ready", max_retries: Optional[int] = None) -> str:
    import tempfile
    import uuid
    ws = workspace or Path(tempfile.mkdtemp(prefix="mio_exp_"))
    doc_paths = {}
    for k in kinds:
        if k == "spec" and not qualified:
            content = BAD_SPEC
        elif k == "spec":
            content = QUALIFIED_SPEC
        elif k == "requirement":
            content = "# 需求\nFR-1 需求一。FR-2 需求二。FR-3 需求三。\n"
        else:
            content = f"# {k}\n契约内容 FR-1 FR-2 FR-3。\n"
        (ws / f"{k}.md").write_text(content, encoding="utf-8")
        doc_paths[k] = f"{k}.md"
    body = {
        "title": f"{title}-{uuid.uuid4().hex[:8]}", "stage": stage,
        "workspace": str(ws), "doc_paths": doc_paths,
    }
    if max_retries is not None:
        body["max_retries"] = max_retries
    r = client.post("/api/v1/tasks", json=body)
    r.raise_for_status()
    return r.json()["id"]


def _write_doc(client, tid: str, kind: str, content: str) -> None:
    # kind 是 query 参数（task_documents.write_task_doc 签名）
    r = client.put(f"/api/v1/tasks/{tid}/doc", params={"kind": kind},
                   json={"content": content})
    r.raise_for_status()


def _read_all(client, tid: str, rid: str, agent: str, kinds=profiles.REQUIRED_KINDS) -> List[int]:
    codes = []
    for k in kinds:
        r = client.get(f"/api/v1/tasks/{tid}/doc",
                       params={"kind": k, "run_id": rid, "agent": agent})
        codes.append(r.status_code)
    return codes


def _submit(client, rid: str, agent: str, *, success: bool = True, result: str = "ok",
            duplicate: bool = False, failure_submit: bool = False):
    body = {"success": success, "result": result, "run_id": rid}
    t0 = time.perf_counter()
    r = client.post(f"/api/v1/runs/{rid}/result", params={"agent": agent}, json=body)
    latency_ms = (time.perf_counter() - t0) * 1000
    detail_keys = []
    try:
        detail_keys = list(r.json().keys()) if r.text else []
    except Exception:
        pass
    return r, latency_ms, detail_keys


def _state_line(client, tid: str, arm: str, scenario: str, rep: int, **extra) -> dict:
    r = client.get(f"/api/v1/tasks/{tid}")
    t = r.json()
    line = {"kind": "state", "scenario": scenario, "arm": arm, "replicate": rep,
            "task_state": t.get("state"), "stage": t.get("stage"),
            "runs": len(t.get("runs") or [])}
    line.update(extra)
    return line


def _gt(scenario: str, arm: str, rep: int, seed: str, expect: dict, labels: dict) -> dict:
    return {"kind": "ground_truth", "scenario": scenario, "arm": arm, "replicate": rep,
            "seed": seed, "expect": expect, "labels": labels}


def _http(scenario: str, arm: str, rep: int, req: str, status, latency_ms,
          **extra) -> dict:
    line = {"kind": "http", "scenario": scenario, "arm": arm, "replicate": rep,
            "req": req, "status": status, "latency_ms": round(latency_ms, 3),
            "server_ts": datetime.now(timezone.utc).isoformat()}
    line.update(extra)
    return line


def cmd_unit(args: argparse.Namespace) -> int:
    _init_db()
    jsonl = Path(args.jsonl)
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    if jsonl.exists():
        jsonl.unlink()  # 单元重跑不留旧线（首轮创建后 append-only 由 harness 保证）
    scen, arm, rep, seed = args.scenario, args.arm, int(args.replicate), args.seed
    try:
        if scen == "S4a":
            return _unit_s4a(args)
        from fastapi.testclient import TestClient
        from mio_taskhub.main import app
        with TestClient(app) as client:
            return _dispatch(client, args, jsonl, scen, arm, rep, seed)
    except Exception as e:  # §5.4-1：harness 自身失败 → ERR 计入分母
        _write_jsonl(jsonl, {"kind": "err", "scenario": scen, "arm": arm,
                             "replicate": rep, "phase": "unit",
                             "error": f"{type(e).__name__}: {e}"})
        return 5


def _dispatch(client, args, jsonl: Path, scen: str, arm: str, rep: int, seed: str) -> int:
    force = arm == "C"  # C 档走 force 路径（§2.1 已知混杂）
    fn = {
        "S1": _s1, "S2": _s2, "S2b": _s2b, "S3": _s3, "S3b": _s3b,
        "S5": _s5, "S6": _s6, "S7": _s7,
    }.get(scen)
    if fn is None:
        raise ValueError(f"scenario not implemented: {scen}")
    return fn(client, args, jsonl, arm, rep, seed, force)


def _s1(client, args, jsonl, arm, rep, seed, force):
    agent = f"s1-{arm}-{rep}-{seed}"
    _write_jsonl(jsonl, _gt("S1", arm, rep, seed,
                            {"submit_status": 200, "terminal": "completed"},
                            {"should_read": True, "compliant": True}))
    tid = _mk_task(client, title=f"s1-{arm}-{rep}")
    r = client.post("/api/v1/tasks/claim", params={"agent": agent, "task_id": tid})
    if r.status_code != 200:
        _write_jsonl(jsonl, _http("S1", arm, rep, "POST /tasks/claim", r.status_code, 0))
        return 0
    rid = r.json()["id"]
    _read_all(client, tid, rid, agent)
    client.post(f"/api/v1/runs/{rid}/heartbeat", params={"agent": agent},
                json={"progress": 80})
    sub, lat, keys = _submit(client, rid, agent)
    _write_jsonl(jsonl, _http("S1", arm, rep, "POST /runs/{id}/result", sub.status_code, lat,
                              detail_keys=keys))
    _write_jsonl(jsonl, _state_line(client, tid, arm, "S1", rep))
    return 0


def _s2(client, args, jsonl, arm, rep, seed, force):
    agent = f"s2-{arm}-{rep}-{seed}"
    _write_jsonl(jsonl, _gt("S2", arm, rep, seed,
                            {"submit_status": 422 if arm == "T" else 200, "terminal": "n/a"},
                            {"should_read": False, "compliant": False}))
    tid = _mk_task(client, title=f"s2-{arm}-{rep}")
    r = client.post("/api/v1/tasks/claim", params={"agent": agent, "task_id": tid})
    rid = r.json()["id"]
    sub, lat, keys = _submit(client, rid, agent)  # 跳过 read
    _write_jsonl(jsonl, _http("S2", arm, rep, "POST /runs/{id}/result", sub.status_code, lat,
                              detail_keys=keys))
    _write_jsonl(jsonl, _state_line(client, tid, arm, "S2", rep))
    return 0


def _s2b(client, args, jsonl, arm, rep, seed, force):
    agent = f"s2b-{arm}-{rep}-{seed}"
    _write_jsonl(jsonl, _gt("S2b", arm, rep, seed,
                            {"submit_status": 422 if arm == "T" else 200, "terminal": "n/a"},
                            {"should_read": True, "doc_mutated": True, "compliant": False}))
    tid = _mk_task(client, title=f"s2b-{arm}-{rep}")
    r = client.post("/api/v1/tasks/claim", params={"agent": agent, "task_id": tid})
    rid = r.json()["id"]
    _read_all(client, tid, rid, agent)
    # 读证后改写 spec.md（指纹过期）：从 task 详情取 workspace 直接改文件
    tws = client.get(f"/api/v1/tasks/{tid}").json().get("workspace", "")
    if tws:
        sp = Path(tws) / "spec.md"
        if sp.exists():
            sp.write_text(sp.read_text(encoding="utf-8") + "\n<!-- mutated -->\n",
                          encoding="utf-8")
    sub, lat, keys = _submit(client, rid, agent)
    _write_jsonl(jsonl, _http("S2b", arm, rep, "POST /runs/{id}/result", sub.status_code, lat,
                              detail_keys=keys))
    _write_jsonl(jsonl, _state_line(client, tid, arm, "S2b", rep))
    return 0


def _wait_requeue(client, tid: str, timeout: float) -> bool:
    """等待 RETRYING→requeue（queued/ready），服务器状态为准。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/v1/tasks/{tid}").json()
        if t.get("state") in ("queued", "ready", "claimed"):
            return True
        if t.get("state") == "failed":
            return False
        time.sleep(1.0)
    return False


def _s3(client, args, jsonl, arm, rep, seed, force):
    agent1 = f"s3-{arm}-{rep}-{seed}-a"
    _write_jsonl(jsonl, _gt("S3", arm, rep, seed,
                            {"submit_status": "first=200,fail=200", "terminal": "failed"},
                            {"should_read": True, "compliant": True}))
    tid = _mk_task(client, title=f"s3-{arm}-{rep}", max_retries=2)
    r = client.post("/api/v1/tasks/claim", params={"agent": agent1, "task_id": tid})
    rid = r.json()["id"]
    _read_all(client, tid, rid, agent1)
    sub, lat, keys = _submit(client, rid, agent1, success=False, result="inject fail 1",
                             failure_submit=True)
    _write_jsonl(jsonl, _http("S3", arm, rep, "POST /runs/{id}/result#fail1", sub.status_code,
                              lat, detail_keys=keys, failure_submit=True))
    if not _wait_requeue(client, tid, timeout=40):
        _write_jsonl(jsonl, _state_line(client, tid, arm, "S3", rep, requeue=False))
        return 0
    agent2 = f"s3-{arm}-{rep}-{seed}-b"
    r2 = client.post("/api/v1/tasks/claim", params={"agent": agent2, "task_id": tid})
    if r2.status_code != 200:
        _write_jsonl(jsonl, _state_line(client, tid, arm, "S3", rep, requeue=True, reclaim=r2.status_code))
        return 0
    rid2 = r2.json()["id"]
    _read_all(client, tid, rid2, agent2)
    sub2, lat2, keys2 = _submit(client, rid2, agent2, success=False, result="inject fail 2",
                                failure_submit=True)
    _write_jsonl(jsonl, _http("S3", arm, rep, "POST /runs/{id}/result#fail2", sub2.status_code,
                              lat2, detail_keys=keys2, failure_submit=True))
    _write_jsonl(jsonl, _state_line(client, tid, arm, "S3", rep, requeue=True))
    return 0


def _s3b(client, args, jsonl, arm, rep, seed, force):
    agent1 = f"s3b-{arm}-{rep}-{seed}-a"
    _write_jsonl(jsonl, _gt("S3b", arm, rep, seed,
                            {"submit_status": "fail=200,retry=200", "terminal": "completed"},
                            {"should_read": True, "compliant": True}))
    tid = _mk_task(client, title=f"s3b-{arm}-{rep}")
    r = client.post("/api/v1/tasks/claim", params={"agent": agent1, "task_id": tid})
    rid = r.json()["id"]
    _read_all(client, tid, rid, agent1)
    sub, lat, keys = _submit(client, rid, agent1, success=False, result="inject fail",
                             failure_submit=True)
    _write_jsonl(jsonl, _http("S3b", arm, rep, "POST /runs/{id}/result#fail", sub.status_code,
                              lat, detail_keys=keys, failure_submit=True))
    if not _wait_requeue(client, tid, timeout=40):
        _write_jsonl(jsonl, _state_line(client, tid, arm, "S3b", rep, requeue=False))
        return 0
    agent2 = f"s3b-{arm}-{rep}-{seed}-b"
    r2 = client.post("/api/v1/tasks/claim", params={"agent": agent2, "task_id": tid})
    rid2 = r2.json()["id"]
    _read_all(client, tid, rid2, agent2)  # 新 run 证据按 run 隔离（audit B3）
    sub2, lat2, keys2 = _submit(client, rid2, agent2, success=True, result="recovered")
    _write_jsonl(jsonl, _http("S3b", arm, rep, "POST /runs/{id}/result#ok", sub2.status_code,
                              lat2, detail_keys=keys2))
    _write_jsonl(jsonl, _state_line(client, tid, arm, "S3b", rep, requeue=True))
    return 0


def _s5(client, args, jsonl, arm, rep, seed, force):
    agent = f"s5-{arm}-{rep}-{seed}"
    _write_jsonl(jsonl, _gt("S5", arm, rep, seed,
                            {"submit_status": 200, "duplicate_status": 409, "terminal": "completed"},
                            {"should_read": True, "compliant": True}))
    tid = _mk_task(client, title=f"s5-{arm}-{rep}")
    r = client.post("/api/v1/tasks/claim", params={"agent": agent, "task_id": tid})
    rid = r.json()["id"]
    _read_all(client, tid, rid, agent)
    sub, lat, keys = _submit(client, rid, agent)
    _write_jsonl(jsonl, _http("S5", arm, rep, "POST /runs/{id}/result", sub.status_code, lat,
                              detail_keys=keys))
    sub2, lat2, keys2 = _submit(client, rid, agent, duplicate=True)
    _write_jsonl(jsonl, _http("S5", arm, rep, "POST /runs/{id}/result#dup", sub2.status_code,
                              lat2, detail_keys=keys2, duplicate_submit=True))
    # DB 事件计数（双状态转移）
    n_success, n_completed = _event_counts(tid)
    _write_jsonl(jsonl, _state_line(client, tid, arm, "S5", rep,
                                    task_result_success_count=n_success,
                                    completed_transitions=n_completed))
    return 0


def _event_counts(tid: str) -> tuple:
    """直接查 sqlite event 表（task_result(success=true) 与 completed 转移计数）。"""
    db = os.environ.get("MIO_TASKHUB_DB") or ""
    if not db or not Path(db).exists():
        return -1, -1
    import sqlite3
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT type, payload FROM event WHERE entity_id=?", (tid,)).fetchall()
        conn.close()
    except Exception:
        return -1, -1
    n_success = 0
    n_completed = 0
    for t, p in rows:
        try:
            payload = json.loads(p or "{}")
        except Exception:
            payload = {}
        if t == "task_result" and payload.get("success") is True:
            n_success += 1
        if t in ("task_stage", "task_transition") and (
                payload.get("target") in ("done", "completed")
                or payload.get("to") in ("done", "completed")):
            n_completed += 1
    return n_success, n_completed


def _s6(client, args, jsonl, arm, rep, seed, force):
    """①低分进 approved；②合格 approved 后降级回退。T=422/422，C=force 200/200。"""
    _write_jsonl(jsonl, _gt("S6", arm, rep, seed,
                            {"step1_status": 422 if arm == "T" else 200,
                             "step2_status": 422 if arm == "T" else 200,
                             "terminal": "n/a"},
                            {"should_read": False, "compliant": False}))
    # ①不合格 spec → review → approved
    tid1 = _mk_task(client, title=f"s6a-{arm}-{rep}", qualified=False)
    client.post(f"/api/v1/tasks/{tid1}/doc/spec/status", json={"state": "review"})
    r1 = client.post(f"/api/v1/tasks/{tid1}/doc/spec/status",
                     json={"state": "approved", **({"force": True} if force else {})})
    _write_jsonl(jsonl, _http("S6", arm, rep, "POST /doc/spec/status#low-quality", r1.status_code, 0,
                              doc_gate_step=1))
    # ②合格 approved 后降级内容 → 再 approved（棘轮回退）
    tid2 = _mk_task(client, title=f"s6b-{arm}-{rep}", qualified=True)
    client.post(f"/api/v1/tasks/{tid2}/doc/spec/status", json={"state": "review"})
    client.post(f"/api/v1/tasks/{tid2}/doc/spec/status", json={"state": "approved"})
    _write_doc(client, tid2, "spec", BAD_SPEC)
    r2 = client.post(f"/api/v1/tasks/{tid2}/doc/spec/status",
                     json={"state": "approved", **({"force": True} if force else {})})
    _write_jsonl(jsonl, _http("S6", arm, rep, "POST /doc/spec/status#rollback", r2.status_code, 0,
                              doc_gate_step=1))
    _write_jsonl(jsonl, _state_line(client, tid2, arm, "S6", rep))
    return 0


def _s7(client, args, jsonl, arm, rep, seed, force):
    """requirement=draft → 到 design。两条路径各半（move/advance）。T=422，C=force 200。"""
    path = "move" if rep % 2 else "advance"
    expect = 422 if arm == "T" else 200
    _write_jsonl(jsonl, _gt("S7", arm, rep, seed,
                            {"submit_status": expect, "terminal": "n/a", "path": path},
                            {"should_read": False, "compliant": False}))
    tid = _mk_task(client, title=f"s7-{arm}-{rep}", stage="implementing",
                   kinds=("requirement", "spec"))
    # requirement 置 draft（生命周期门检验对象）
    client.post(f"/api/v1/tasks/{tid}/doc/requirement/status", json={"state": "draft"})
    # spec_path 必须提供（design 产出物门），使 T/C 唯一差异 = requirement 生命周期门
    body = {"target_stage": "design", "spec_path": "spec.md",
            **({"force": True} if force else {})}
    if path == "move":
        r = client.post(f"/api/v1/tasks/{tid}/stage/move", json=body)
    else:
        r = client.post(f"/api/v1/tasks/{tid}/stage", json=body)
    _write_jsonl(jsonl, _http("S7", arm, rep, f"POST /stage/{path}", r.status_code, 0,
                              doc_gate_step=1))
    _write_jsonl(jsonl, _state_line(client, tid, arm, "S7", rep))
    return 0


def _unit_s4a(args) -> int:
    """S4a：真服务 + 短超时；亚型①从不心跳 ②progress=50 后停止。"""
    import shutil
    import socket
    import tempfile
    jsonl = Path(args.jsonl)
    scen, arm, rep, seed = "S4a", args.arm, int(args.replicate), args.seed
    subtype = 1 if rep <= (profiles.SCENARIO_N["S4a"] // 2) else 2
    _write_jsonl(jsonl, _gt("S4a", arm, rep, seed,
                            {"terminal": "completed", "recovered_within": profiles.T_LIMIT_SECONDS},
                            {"should_read": True, "subtype": subtype}))

    def free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    port = free_port()
    root = f"http://127.0.0.1:{port}"
    datadir = args.server_logs or tempfile.mkdtemp(prefix="mio_s4a_")
    Path(datadir).mkdir(parents=True, exist_ok=True)
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    logf = open(Path(datadir) / f"s4a_{arm}_{rep}.err", "w")
    env = dict(os.environ)
    env["MIO_TASKHUB_DB"] = str(db)
    import subprocess as sp
    proc = sp.Popen([PY, "-m", "uvicorn", "mio_taskhub.main:app",
                     "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                    cwd=str(REPO), env=env, stdout=sp.DEVNULL, stderr=logf)
    agent = f"s4a-{arm}-{rep}-{seed}"
    try:
        # 就绪
        import urllib.request as ur
        deadline = time.time() + 30
        ready = False
        while time.time() < deadline:
            try:
                ur.urlopen(root + "/healthz", timeout=5).read()
                ready = True
                break
            except Exception:
                time.sleep(0.3)
        if not ready:
            raise RuntimeError("s4a server not ready")

        def call(method, path, body=None):
            url = root + "/api/v1" + path
            data = json.dumps(body).encode() if body is not None else None
            req = ur.Request(url, data=data, method=method)
            req.add_header("Content-Type", "application/json")
            try:
                with ur.urlopen(req, timeout=30) as resp:
                    return resp.status, (json.loads(resp.read() or b"null"))
            except urllib.error.HTTPError as e:
                return e.code, (json.loads(e.read() or b"null"))

        tid = None
        import tempfile as tf
        ws = Path(tf.mkdtemp(prefix="mio_s4aws_"))
        for k in profiles.REQUIRED_KINDS:
            (ws / f"{k}.md").write_text(QUALIFIED_SPEC, encoding="utf-8")
        st, t = call("POST", "/tasks", {"title": f"s4a-{arm}-{rep}-{seed}", "stage": "ready",
                                        "workspace": str(ws),
                                        "doc_paths": {k: f"{k}.md" for k in profiles.REQUIRED_KINDS}})
        tid = t["id"]
        st, c = call("POST", f"/tasks/claim?agent={agent}&task_id={tid}")
        rid = c["id"]
        t0 = time.perf_counter()
        if subtype == 1:
            pass  # 从不 heartbeat
        else:
            call("POST", f"/runs/{rid}/heartbeat?agent={agent}", {"progress": 50})
            time.sleep(0.5)
        # 等待 reaper 回收（T_limit=30s + 余量）
        recovered = False
        inject_ts = datetime.now(timezone.utc).isoformat()
        requeue_at_server = None
        deadline = time.time() + profiles.T_LIMIT_SECONDS + 15
        while time.time() < deadline:
            st, t = call("GET", f"/tasks/{tid}")
            runs = t.get("runs") or []
            reaped = any(r.get("state") == "finished" and r.get("id") == rid for r in runs)
            if reaped:
                requeue_at_server = next((r.get("finished_at") for r in runs
                                          if r.get("id") == rid), None)
                # 回收后可再 claim → 完成
                st2, c2 = call("POST", f"/tasks/claim?agent={agent}2&task_id={tid}")
                if st2 == 200:
                    rid2 = c2["id"]
                    for k in profiles.REQUIRED_KINDS:
                        call("GET", f"/tasks/{tid}/doc?kind={k}&run_id={rid2}&agent={agent}2")
                    call("POST", f"/runs/{rid2}/result?agent={agent}2",
                         {"success": True, "result": "recovered", "run_id": rid2})
                    recovered = True
                    break
            time.sleep(1.0)
        recovery_seconds = time.perf_counter() - t0
        st, t = call("GET", f"/tasks/{tid}")
        _write_jsonl(jsonl, _http("S4a", arm, rep, "POST /runs/{id}/result(after-requeue)",
                                  st, recovery_seconds * 1000,
                                  subtype=subtype, inject_ts=inject_ts))
        _write_jsonl(jsonl, {"kind": "state", "scenario": "S4a", "arm": arm, "replicate": rep,
                             "task_state": t.get("state"), "stage": t.get("stage"),
                             "runs": len(t.get("runs") or []),
                             "subtype": subtype, "recovered": recovered,
                             "recovery_seconds": round(recovery_seconds, 3),
                             "reaper_ts": requeue_at_server, "inject_ts": inject_ts})
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except sp.TimeoutExpired:
                proc.kill()
        logf.close()


def cmd_allow_s4b(args) -> int:
    """S4b：hub 侧 kill。默认拒绝（§9 P3 未批）。"""
    print("[run_m] S4b refused: requires --allow-s4b (04 §9 P3 approval)", file=sys.stderr)
    return 6


# ── CLI ───────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mio-taskhub M 系列执行器（04 §6.3，需 §9 批准运行）")
    ap.add_argument("--seed", default=profiles.MASTER_SEED_DEFAULT)
    ap.add_argument("--rounds", type=int, default=profiles.ROUNDS_DEFAULT)
    ap.add_argument("--scenarios", default="all", help="逗号分隔或 all")
    ap.add_argument("--out", default=str(REPO / "experiments" / "runs"))
    ap.add_argument("--allow-s4b", action="store_true")
    ap.add_argument("--probe-only", action="store_true",
                    help="只跑档位探针后结束（T1.3 探针校验）")
    ap.add_argument("--replicates", type=int, default=0,
                    help="覆盖每场景每档重复数（0=用 SCENARIO_N 预注册值）")
    ap.add_argument("--strict-probes", action="store_true",
                    help="探针失败立即退出（默认作废该轮继续）")
    ap.add_argument("--stop-on-err", action="store_true")
    # 内部：子进程
    ap.add_argument("--_unit", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--_probe", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--scenario", default="", help=argparse.SUPPRESS)
    ap.add_argument("--arm", default="T", help=argparse.SUPPRESS)
    ap.add_argument("--replicate", type=int, default=1, help=argparse.SUPPRESS)
    ap.add_argument("--db", default="", help=argparse.SUPPRESS)
    ap.add_argument("--jsonl", default="", help=argparse.SUPPRESS)
    ap.add_argument("--server-logs", default="", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    # 子进程：env 必须在导入 mio_taskhub 之前生效
    if args._probe or args._unit:
        os.environ["MIO_TASKHUB_DB"] = args.db
        for k in ("MIO_READ_GATE", "MIO_RATCHET_DISABLED",
                  "MIO_TASKHUB_TIMEOUT_SECONDS", "MIO_TASKHUB_AGENT_OFFLINE_SECONDS"):
            os.environ.pop(k, None)
        os.environ.update(profiles.arm_env(args.arm))
        if args.scenario in ("S4a", "S4b"):
            os.environ.update(profiles.S4_ENV)
        os.environ.setdefault("MIO_HOME", str(Path(args.db).parent / "mio_home"))

        def _lazy():
            if args._probe:
                return cmd_probe(args)
            if args.scenario == "S4b":
                return cmd_allow_s4b(args)
            return cmd_unit(args)
        # 延迟到子进程内导入（保证 env 生效在前）
        return _lazy()

    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
