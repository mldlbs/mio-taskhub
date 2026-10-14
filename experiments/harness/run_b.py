# -*- coding: utf-8 -*-
"""B 系列执行器（条件性；04 §6.1/§9 P4 批准后方可运行）。

职责（M 系列之外的行为层）：
  - 起 2 个真实 hub 实例（T 端口 / C 端口，各自 DB）；
  - 按 seed 把 N 个任务随机分配到实例（agent 不感知档位，MIO_TASKHUB_URL 指向被分配实例）；
  - 创建任务集（合格文档模板与 M 系列同构，标题含 seed/replicate 后缀）；
  - 等待真实 agent 完成 → 落 assignment.json + raw jsonl（ground_truth 先写）。

默认拒绝运行：需 --allow-b（对应 §9 P4 资源审批）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

import profiles  # noqa: E402

PY = str(REPO / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HubInstance:
    """真实 hub 子进程（独立端口 + 独立 DB + 档位 env）。"""

    def __init__(self, arm: str, datadir: Path, extra_env: Optional[dict] = None):
        self.arm = arm
        self.port = _free_port()
        self.root = f"http://127.0.0.1:{self.port}"
        self.datadir = datadir
        datadir.mkdir(parents=True, exist_ok=True)
        self.db = str(datadir / f"hub_{arm}.db")
        self.env = profiles.full_child_env(dict(os.environ), arm)
        self.env.update({
            "MIO_TASKHUB_DB": self.db,
            "MIO_HOME": str(datadir / "mio_home"),
        })
        if extra_env:
            self.env.update(extra_env)
        self.proc: Optional[subprocess.Popen] = None

    def start(self, timeout: float = 30) -> None:
        err = open(self.datadir / f"hub_{self.arm}.err", "w")
        self.proc = subprocess.Popen(
            [PY, "-m", "uvicorn", "mio_taskhub.main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=str(REPO), env=self.env, stdout=subprocess.DEVNULL, stderr=err)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"hub {self.arm} exited rc={self.proc.returncode}")
            try:
                urllib.request.urlopen(self.root + "/healthz", timeout=3).read()
                return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError(f"hub {self.arm} not ready")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def assign_arm(master_seed: str, replicate: int) -> str:
    """任务→实例分配（agent 不感知档位）。"""
    h = hashlib.sha256(f"{master_seed}||assign||{replicate}".encode()).digest()
    return "T" if h[0] & 1 else "C"


# ── T1.6 B 系列 defer 决定（2026-10-14） ─────────────────────────────────
# B 系列（真实 agent 可用性双 hub 实验）与 V1 判定均 defer（§9 P4 未批）。
# 在 P4 批准并完成 B 系列前，V1 判定为「证据不足」而非「不支持」。
# 双实例独立性（独立端口/独立 DB/独立档位 env）已有单测覆盖（HubInstance 不 start）。
# P4 批准后：HubInstance.start() 可用（已实现），assign_arm 可用（已实现）。
#
# ── T2.2 V1 可用性 defer 确认（2026-10-14） ──────────────────────────────
# T2.2 DoD：承 T1.6 defer 结论——V1=证据不足（P4 未批、真实 agent 不可用）。
# 验收命令 run_b.py --tasks 60 --assign seed=... 无 --allow-b 时 exit 6 拒绝。
# V1 判定需 n≥30/组 + McNemar/二项精确 + Holm 校正；当前样本 0 → 证据不足。
# P4 批准后此 defer 自动失效，届时执行 run_b 完整流程并 summarize V1。


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mio-taskhub B 系列执行器（04 §6.1；需 §9 P4 批准，--allow-b 开启）")
    ap.add_argument("--tasks", type=int, default=60, help="任务总数（≥30/组）")
    ap.add_argument("--assign", default="", help="seed=XXXX 形式或已有 assignment.json 路径")
    ap.add_argument("--seed", default=profiles.MASTER_SEED_DEFAULT)
    ap.add_argument("--out", default=str(REPO / "experiments" / "runs"))
    ap.add_argument("--allow-b", action="store_true")
    ap.add_argument("--wait", type=int, default=0, help="等待真实 agent 完成的秒数（0=只建不等）")
    args = ap.parse_args(argv)

    if not args.allow_b:
        print("[run_b] refused: requires --allow-b (04 §9 P4 资源审批)", file=sys.stderr)
        return 6

    if args.assign.startswith("seed="):
        args.seed = args.assign.split("=", 1)[1]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.out) / f"{ts}_B_seed{args.seed}"
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)

    # 分配表（先写 ground_truth 再起服务/建任务）
    assignment = {str(i): assign_arm(args.seed, i) for i in range(1, args.tasks + 1)}
    (run_dir / "assignment.json").write_text(
        json.dumps({"seed": args.seed, "assignment": assignment,
                    "created_at": datetime.now(timezone.utc).isoformat()},
                   indent=2), encoding="utf-8")
    for i, arm in assignment.items():
        with (run_dir / "raw" / f"assign_{i}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "ground_truth", "scenario": "B",
                                "replicate": int(i), "arm": arm,
                                "seed": args.seed,
                                "labels": {"agent_blind": True}},
                               ensure_ascii=False) + "\n")

    hubs = {arm: HubInstance(arm, run_dir / "hubs") for arm in profiles.ARM_KEYS}
    try:
        for h in hubs.values():
            h.start()
        # 任务创建（分布到对应实例；合格文档模板与 M 同构）
        sys.path.insert(0, str(HERE))
        from run_m import QUALIFIED_SPEC  # noqa: E402
        REQUIRED_KINDS = profiles.REQUIRED_KINDS
        for i, arm in assignment.items():
            root = hubs[arm].root
            ws = run_dir / "ws" / f"b_{i}"
            ws.mkdir(parents=True, exist_ok=True)
            for k in REQUIRED_KINDS:
                (ws / f"{k}.md").write_text(
                    QUALIFIED_SPEC if k == "spec" else f"# {k}\nFR-1 FR-2 FR-3\n",
                    encoding="utf-8")
            body = {"title": f"b-{args.seed}-{i}", "stage": "ready",
                    "workspace": str(ws),
                    "doc_paths": {k: f"{k}.md" for k in REQUIRED_KINDS}}
            req = urllib.request.Request(
                root + "/api/v1/tasks", data=json.dumps(body).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                tid = json.loads(resp.read())["id"]
            with (run_dir / "raw" / f"assign_{i}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "state", "scenario": "B", "arm": arm,
                                    "replicate": int(i), "task_id": tid,
                                    "hub": hubs[arm].root}, ensure_ascii=False) + "\n")
        print(f"[run_b] {args.tasks} tasks created across "
              f"T={hubs['T'].root} C={hubs['C'].root}")
        # 真实 agent 需手工/外部指向对应 hub；--wait 期间只轮询终态
        if args.wait > 0:
            deadline = time.time() + args.wait
            while time.time() < deadline:
                time.sleep(10)
        print(f"[run_b] assignment -> {run_dir / 'assignment.json'}")
        print("[run_b] 提醒：真实 agent 以 MIO_TASKHUB_URL=<对应 hub> 参与；"
              "汇总用 summarize.py --run 指向本目录（B 系列 V1 判定在 04 §7.1）。")
        return 0
    finally:
        for h in hubs.values():
            h.stop()


if __name__ == "__main__":
    raise SystemExit(main())
