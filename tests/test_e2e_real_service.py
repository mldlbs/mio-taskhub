# -*- coding: utf-8 -*-
"""真服务 E2E + 并发 claim 压测（task f72fde6e / P1-4）。

与 tests/test_integration.py（TestClient 进程内桩）不同，本模块**起真实 uvicorn 进程**
（独立端口 + 独立 DB），通过真实 HTTP 驱动，验证：
  ① 全生命周期：建单→注册 agent→claim→heartbeat→submit_result→完成；
  ② 并发不重复认领：N 个 agent 并发 claim M 个任务，零重复、零丢失（run↔task 一一对应）；
  ③ 长任务不被 120s 窗口误杀（与 P1-2 联动，验证心跳窗口自适应）；
  ④ 量化基线：成功率、P50/P95 时延。

标记 `e2e`：默认单测不跑；用 `pytest -m e2e` 单独运行。
    pytest -m e2e tests/test_e2e_real_service.py -v
"""
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not os.path.exists(PY):
    PY = sys.executable


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RealServer:
    """在独立端口 + 独立 DB 上跑真实 uvicorn。"""

    def __init__(self, datadir: str):
        self.port = _free_port()
        self.root = f"http://127.0.0.1:{self.port}"
        self.base = f"{self.root}/api/v1"
        self.datadir = datadir
        self.db_path = os.path.join(datadir, "e2e.db")
        self.proc = None

    def __enter__(self):
        env = dict(os.environ)
        env["MIO_TASKHUB_DB"] = self.db_path
        env["MIO_TASKHUB_TOKEN"] = ""            # 不启用 token，简化 E2E
        env["MIO_OBSERVER_AUTOSTART"] = "0"      # 不起观测台
        env["MIO_TASKHUB_RATE_LIMIT"] = "100000"  # 压测不触发限流
        env["MIO_HOME"] = os.path.join(self.datadir, "mio_missing")
        self.proc = subprocess.Popen(
            [PY, "-m", "uvicorn", "mio_taskhub.main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=open(os.path.join(self.datadir, "server.err"), "w"),
        )
        self._wait_ready()
        return self

    def _wait_ready(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early rc={self.proc.returncode}")
            try:
                # 健康检查在根路径 /healthz（不在 /api/v1 下）
                urllib.request.urlopen(self.root + "/healthz", timeout=5).read()
                return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError("server did not become ready in time")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # ---- HTTP helpers ----
    def _req(self, method, path, body=None, timeout=30):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json; charset=utf-8")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)

    def get(self, path, **kw):
        return self._req("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self._req("POST", path, body=body or {}, **kw)

    def claim(self, agent, task_id=None):
        """claim 走 query 参数（不是 JSON body）。返回 (status, json|None)。"""
        q = f"?agent={agent}"
        if task_id:
            q += f"&task_id={task_id}"
        try:
            return self._req("POST", "/tasks/claim" + q, None)
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return 204, None
            raise


@pytest.fixture(scope="module")
def server():
    # Windows：uvicorn 子进程可能仍持有 DB 文件句柄，TemporaryDirectory 清理会 PermissionError。
    # 用 mkdtemp + 尽力清理，避免把测试结果误判为失败。
    d = tempfile.mkdtemp(prefix="mio_e2e_")
    srv = RealServer(d)
    try:
        with srv:
            yield srv
    finally:
        import shutil
        for _ in range(10):
            try:
                shutil.rmtree(d, ignore_errors=False)
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            shutil.rmtree(d, ignore_errors=True)


def _register(srv, name):
    srv.post("/agents/register", {"name": name, "agent_type": "cli"})


# ---------- ① 全生命周期 E2E ----------

def test_full_lifecycle_over_real_http(server):
    srv = server
    _register(srv, "e2e-agent")
    st, task = srv.post("/tasks", {"title": "E2E-lifecycle", "stage": "ready", "priority": 1})
    assert st == 200
    tid = task["id"]

    st, claim = srv.claim("e2e-agent", tid)
    assert st == 200, claim
    run_id = claim["id"]

    st, hb = srv.post(f"/runs/{run_id}/heartbeat", {"progress": 50, "checkpoint": "mid"})
    assert st == 200

    st, res = srv.post(f"/runs/{run_id}/result",
                       {"success": True, "result": "done over real http"})
    assert st == 200

    st, fetched = srv.get(f"/tasks/{tid}")
    assert fetched["state"] == "completed"


# ---------- ② 并发 claim：零重复、零丢失 ----------

def test_concurrent_claim_no_duplicates(server):
    """并发不重复认领：N 个 agent 同时在 M 个任务上抢，每个 (task) 至多被认领一次。

    关键：claim 对同一 agent 会复用其未完成 run，因此让每个 agent「认领→立即提交→再认领」
    循环，直到队列清空；并发发生在每个 agent 的认领瞬间。
    """
    srv = server
    N_AGENTS = 10
    M_TASKS = 100
    for i in range(N_AGENTS):
        _register(srv, f"cc-agent-{i}")

    tids = []
    for i in range(M_TASKS):
        st, t = srv.post("/tasks", {"title": f"cc-task-{i}", "stage": "ready"})
        assert st == 200
        tids.append(t["id"])
    task_set = set(tids)

    claimed = []  # (agent, run_id, task_id)
    lock = threading.Lock()
    start = threading.Barrier(N_AGENTS)

    def worker(agent):
        start.wait()  # 尽量同时开抢
        mine = []
        while True:
            st, r = srv.claim(agent)
            if st == 204 or not r:
                break
            mine.append((agent, r["id"], r["task_id"]))
            # 释放：立即提交该 run，使 agent 可再次认领
            srv.post(f"/runs/{r['id']}/result", {"success": True, "result": "ok"})
        with lock:
            claimed.extend(mine)

    with ThreadPoolExecutor(max_workers=N_AGENTS) as ex:
        list(ex.map(worker, [f"cc-agent-{i}" for i in range(N_AGENTS)]))

    claimed_tids = [c[2] for c in claimed]
    dup = len(claimed_tids) - len(set(claimed_tids))
    assert dup == 0, f"重复认领 {dup} 条（应 0）"
    assert set(claimed_tids) == task_set, "存在丢失或越界认领"
    assert len(claimed) == M_TASKS, f"认领总数 {len(claimed)} != {M_TASKS}"


# ---------- ③ 长任务不被 120s 窗口误杀 ----------

def test_long_task_survives_beyond_old_120s_window(server):
    """est=60min 的任务在 agent 心跳后跨过 120s 仍不被回收（心跳窗口自适应）。"""
    srv = server
    _register(srv, "long-agent")
    st, t = srv.post("/tasks", {"title": "long-task", "stage": "ready",
                                "est_duration_min": 60})
    tid = t["id"]
    st, claim = srv.claim("long-agent", tid)
    run_id = claim["id"]
    srv.post(f"/runs/{run_id}/heartbeat", {"progress": 10})

    # 直接调用心跳窗口判定（不真等 120s）：构造 RunInfo，progress=10 + agent_offline
    from mio_taskhub.heartbeat import HeartbeatSweep, RunInfo
    from mio_taskhub.models import RunState
    sweep = HeartbeatSweep()
    ri = RunInfo(run_id=run_id, task_id=tid, agent_name="long-agent", state=RunState.RUNNING,
                 last_heartbeat=time.time() - 130, attempt=1, max_retries=3,
                 timeout_seconds=60 * 60, agent_offline=True, progress=10)
    assert sweep.effective_timeout(ri) > 130, "长任务在工作时不应在 ~120-130s 被判死"

    # 仍能正常提交完成
    st, _ = srv.post(f"/runs/{run_id}/result", {"success": True, "result": "long done"})
    assert st == 200


# ---------- ④ 量化基线：成功率 + P50/P95 ----------

def test_quantified_baseline(server):
    srv = server
    _register(srv, "bench-agent")
    n = 20
    lat = []
    ok = 0
    for i in range(n):
        st, t = srv.post("/tasks", {"title": f"bench-{i}", "stage": "ready"})
        tid = t["id"]
        t0 = time.perf_counter()
        st, claim = srv.claim("bench-agent", tid)
        lat.append((time.perf_counter() - t0) * 1000)
        st, _ = srv.post(f"/runs/{claim['id']}/result", {"success": True, "result": "ok"})
        st, fetched = srv.get(f"/tasks/{tid}")
        if fetched["state"] == "completed":
            ok += 1

    lat.sort()
    p50 = lat[len(lat) // 2]
    p95 = lat[int(len(lat) * 0.95) - 1]
    success_rate = ok / n

    # 输出到报告可读的位置
    report = {
        "tasks": n, "success": ok, "success_rate": round(success_rate, 4),
        "claim_p50_ms": round(p50, 2), "claim_p95_ms": round(p95, 2),
    }
    out = os.path.join(tempfile.gettempdir(), "mio_e2e_baseline.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    assert success_rate >= 0.99, f"成功率 {success_rate} 低于 99%"
    assert p95 < 2000, f"P95 认领时延 {p95}ms 过高"
