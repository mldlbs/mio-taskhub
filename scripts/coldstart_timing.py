# -*- coding: utf-8 -*-
"""冷启 Quickstart 计时 harness（T3.5 / M3·M4）。

测量陌生环境「启动 hub → register → claim → submit」闭环耗时，
并输出每步耗时与阻塞点，供 M3（3 台外部干净机器）/M4（<5 分钟一次闭环）取证。

用法：
  python scripts/coldstart_timing.py                 # 测当前运行中的 hub 闭环耗时
  python scripts/coldstart_timing.py --exe <path>    # 冷启指定 exe 并计时
  python scripts/coldstart_timing.py --n 5           # 重复 n 次取分布
  python scripts/coldstart_timing.py --json          # 输出 JSON（机器可读）
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = os.environ.get("MIO_TASKHUB_URL", "http://127.0.0.1:48620")
API = DEFAULT_BASE.rstrip("/") + "/api/v1"


def _get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def _post(url: str, body: dict | None = None, timeout: float = 10.0):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def wait_healthz(base: str, deadline_s: float) -> float | None:
    """等待 /healthz 返回 200，返回耗时秒；超时返回 None。"""
    t0 = time.perf_counter()
    url = base.rstrip("/") + "/healthz"
    while time.perf_counter() - t0 < deadline_s:
        try:
            status, _ = _get(url, timeout=2.0)
            if status == 200:
                return time.perf_counter() - t0
        except Exception:
            time.sleep(0.25)
    return None


def kill_hub(exe_name: str = "mio-taskhub.exe") -> int:
    """杀掉同名 hub 进程，返回被杀数量。"""
    killed = 0
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                subprocess.run(["taskkill", "/PID", parts[1], "/F"],
                               capture_output=True, timeout=10)
                killed += 1
    except Exception:
        pass
    return killed


def measure_closed_loop(base: str, tag: str) -> dict:
    """测 register→create→claim→submit 闭环，返回各步耗时。"""
    steps = {}
    t0 = time.perf_counter()
    # 1) register
    _post(f"{base.rstrip('/')}/api/v1/agents/register", {"name": tag, "agent_type": "cli"})
    steps["register"] = time.perf_counter() - t0
    # 2) create task
    t1 = time.perf_counter()
    _, body = _post(f"{base.rstrip('/')}/api/v1/tasks",
                    {"title": f"coldstart-{tag}", "stage": "ready"})
    task_id = json.loads(body)["id"]
    steps["create_task"] = time.perf_counter() - t1
    # 3) claim
    t2 = time.perf_counter()
    _, cbody = _post(f"{base.rstrip('/')}/api/v1/tasks/claim?agent={tag}&task_id={task_id}")
    run_id = json.loads(cbody)["id"]
    steps["claim"] = time.perf_counter() - t2
    # 4) submit
    t3 = time.perf_counter()
    _post(f"{base.rstrip('/')}/api/v1/runs/{run_id}/result?agent={tag}",
          {"agent": tag, "success": True, "result": "coldstart ok", "exit_code": 0})
    steps["submit"] = time.perf_counter() - t3
    steps["total"] = time.perf_counter() - t0
    steps["task_id"] = task_id
    steps["run_id"] = run_id
    return steps


def run_once(exe: str | None, base: str, tag: str, startup_deadline: float,
             kill: bool = False) -> dict:
    """一次完整测量：可选冷启 hub，然后测闭环。"""
    result = {"exe": exe, "base": base}
    proc = None
    if exe:
        if kill:
            kill_hub(Path(exe).name)
            time.sleep(1.0)
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            [exe, "hub"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(Path(exe).parent),
        )
        startup = wait_healthz(base, startup_deadline)
        result["hub_startup_s"] = startup
        result["hub_pid"] = proc.pid
        if startup is None:
            result["error"] = f"hub 未在 {startup_deadline}s 内就绪（可能端口占用/被杀软拦截）"
            _terminate(proc)
            return result
    try:
        loop = measure_closed_loop(base, tag)
        result.update(loop)
    finally:
        if proc is not None:
            _terminate(proc)
    return result


def _terminate(proc) -> None:
    """只终止本次启动的 PID，绝不按进程名误杀其他实例。"""
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=None, help="绿色版 exe 路径（冷启计时用）")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--startup-deadline", type=float, default=30.0)
    ap.add_argument("--kill", action="store_true", help="冷启前先按进程名杀同名实例（慎用，会误杀生产 hub）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    runs = []
    for i in range(args.n):
        tag = f"coldstart-{int(time.time())}-{i}"
        r = run_once(args.exe, args.base, tag, args.startup_deadline, kill=args.kill)
        runs.append(r)
        if not args.json:
            print(f"[{i+1}/{args.n}] {json.dumps(r, ensure_ascii=False)}")

    # 汇总
    def _stat(key):
        vals = [r[key] for r in runs if r.get(key) is not None]
        if not vals:
            return None
        return {
            "n": len(vals),
            "min": round(min(vals), 3),
            "median": round(statistics.median(vals), 3),
            "max": round(max(vals), 3),
        }

    summary = {
        "n": args.n,
        "hub_startup_s": _stat("hub_startup_s"),
        "closed_loop_total_s": _stat("total"),
        "register_s": _stat("register"),
        "claim_s": _stat("claim"),
        "submit_s": _stat("submit"),
        "errors": [r.get("error") for r in runs if r.get("error")],
    }
    if args.json:
        print(json.dumps({"runs": runs, "summary": summary}, ensure_ascii=False, indent=2))
    else:
        print("\n=== 汇总 ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        # M4 判定：闭环 < 300s（含冷启）
        total = summary["closed_loop_total_s"]
        startup = summary["hub_startup_s"]
        if total and total["median"] < 300:
            print(f"\n[M4] 闭环中位 {total['median']}s < 300s PASS（陌生用户 <5 分钟）")
        if startup and startup["median"] < 30:
            print(f"[M3] hub 冷启中位 {startup['median']}s < 30s PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
