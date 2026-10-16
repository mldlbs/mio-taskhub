# -*- coding: utf-8 -*-
"""idle_worker agent 保活心跳测试（P1-OBS/T4.3）。

空闲 worker 若不续 agent 心跳，会被 `_mark_stale_agents` 置 OFFLINE，且 claim 防御
（拒绝非 ONLINE agent）会使其无法领取任务。本测试验证保活机制存在且会调用
`/agents/heartbeat`。
"""
import http.server
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IDLE = REPO / "packaging" / "idle_worker.py"


def test_source_has_agent_keepalive():
    src = IDLE.read_text(encoding="utf-8")
    assert "/agents/heartbeat" in src, "缺 agent 保活心跳调用"
    assert "agent-keepalive" in src, "缺保活线程"


class _FakeHub(http.server.BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _record(self):
        _FakeHub.calls.append(self.path)

    def do_POST(self):
        self._record()
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        if self.path.startswith("/agents/heartbeat"):
            body = json.dumps({"name": "w", "status": "online"}).encode()
            self.send_response(200)
        else:
            body = b""
            self.send_response(204)  # claim → no task
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body:
            self.wfile.write(body)


def test_idle_worker_registers_and_keepalives(tmp_path):
    """运行 idle_worker（缩短保活间隔）对假 hub，验证 register + agent heartbeat。"""
    _FakeHub.calls = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeHub)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env = dict(__import__("os").environ)
        env["MIO_TASKHUB_URL"] = f"http://127.0.0.1:{port}"
        env["IDLE_KEEPALIVE_SECONDS"] = "1"  # 缩短（源码默认 60）
        # --once 会因无任务立即退出；保活线程可能来不及跑满间隔 → 用 --idle-timeout 留驻
        p = subprocess.Popen(
            [sys.executable, str(IDLE), "w", "--interval", "0.3",
             "--idle-timeout", "3", "--project", ""],
            cwd=str(REPO), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(2.5)
        finally:
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
    finally:
        srv.shutdown()
    paths = _FakeHub.calls
    assert any("/agents/register" in c for c in paths), f"未注册: {paths}"
    assert any("/agents/heartbeat" in c for c in paths), f"未发 agent 保活心跳: {paths}"
