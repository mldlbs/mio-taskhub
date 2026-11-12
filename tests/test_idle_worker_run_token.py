# -*- coding: utf-8 -*-
"""idle_worker per-run token 测试（B22，强模式配套）。

`MIO_TASKHUB_RUN_TOKEN_REQUIRED=1` 上线后（2026-11-12），heartbeat / submit_result
必须携带 `X-Run-Token`（claim 响应返回的 run_token），否则 403 run.token_required。
idle_worker 是 nightrun / 空闲计划的执行侧，必须自动带头。
"""
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IDLE = REPO / "packaging" / "idle_worker.py"

TOKEN = "run-token-e2e-123"


def test_source_carries_run_token():
    src = IDLE.read_text(encoding="utf-8")
    assert "X-Run-Token" in src, "idle_worker 缺 per-run token 头"
    assert 'res.get("run_token")' in src, "claim 响应的 run_token 未被保存"


class _FakeHub(http.server.BaseHTTPRequestHandler):
    calls = []  # (path, headers dict)

    def log_message(self, *a):
        pass

    def _record(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        _FakeHub.calls.append((self.path, dict(self.headers)))

    def _reply(self, code, body=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body:
            self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        self._record()
        self._reply(200, {})

    def do_POST(self):
        self._record()
        if "/agents/register" in self.path:
            self._reply(200, {"name": "w", "status": "online"})
        elif "/tasks/claim" in self.path:
            self._reply(200, {
                "id": "runX", "task_id": "tX", "state": "claimed",
                "agent_name": "w", "run_token": TOKEN, "attempt": 1,
                "required_reads": [], "readiness": {"ready": True},
                "task": {"id": "tX", "title": "强模式配套验证"},
            })
        elif "/runs/runX/heartbeat" in self.path:
            self._reply(200, {"id": "runX", "state": "running"})
        elif "/runs/runX/result" in self.path:
            self._reply(200, {"id": "runX", "state": "finished", "task_state": "completed"})
        else:
            self._reply(200, {})


def test_idle_worker_sends_run_token_headers(tmp_path):
    """端到端：claim 下发 run_token → idle_worker 的 heartbeat / result 调用带头。"""
    _FakeHub.calls = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeHub)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = dict(os.environ)
    env["MIO_TASKHUB_URL"] = f"http://127.0.0.1:{port}/api/v1"
    p = subprocess.Popen(
        [sys.executable, str(IDLE), "w", "--once"],
        cwd=str(REPO), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    try:
        out = p.communicate(timeout=30)[0]
    except Exception:
        p.kill()
        out = p.communicate(timeout=5)[0]
    finally:
        srv.shutdown()
    paths = _FakeHub.calls
    assert any("/tasks/claim" in c for c, _ in paths), f"未 claim: {paths}\n输出:\n{out}"
    result_calls = [(c, h) for c, h in paths if "/runs/runX/result" in c]
    assert result_calls, f"未提交 result: {paths}\n输出:\n{out}"
    _, headers = result_calls[0]
    assert headers.get("X-Run-Token") == TOKEN, \
        f"result 未携带正确 X-Run-Token: {headers}\n输出:\n{out}"
