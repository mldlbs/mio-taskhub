# -*- coding: utf-8 -*-
"""scheduler_control 端点测试：进程分类 / 状态 payload / 激活幂等 / 停止。

进程扫描与 Popen 全部 monkeypatch，不真起进程、不依赖本机 node。
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mio_taskhub.api import scheduler_control as sc


# ── 进程分类 ──────────────────────────────────────────────

NODE = r"D:\node_global\node_modules\mio-agent-runtime\bin\mio.js"


def _p(pid, cmdline):
    return {"pid": pid, "cmdline": cmdline}


def test_classify_serve_and_daemon():
    procs = [
        _p(100, f'C:\\node.exe {NODE} observer serve --base-dir E:\\x\\.local\\observer'),
        _p(200, f'C:\\node.exe {NODE} observe'),
        _p(300, 'C:\\node.exe something-else.js'),  # 非 mio 进程，忽略
    ]
    r = sc._classify(procs)
    assert r["serve"]["pid"] == 100
    assert r["daemon"]["pid"] == 200


def test_classify_empty_when_no_mio_process():
    r = sc._classify([_p(1, "notepad.exe")])
    assert r["serve"] is None and r["daemon"] is None


def test_classify_daemon_not_confused_by_observer_serve():
    """'observer serve' 不能被误判为 daemon（observe 是 serve 的子串）。"""
    r = sc._classify([_p(100, f'C:\\node.exe {NODE} observer serve --base-dir X')])
    assert r["serve"]["pid"] == 100
    assert r["daemon"] is None


# ── 数据新鲜度 ────────────────────────────────────────────

def test_today_data_missing_file(tmp_path):
    r = sc._today_data(tmp_path)
    assert r["exists"] is False and r["count"] == 0 and r["minutes_since_write"] is None


def test_today_data_counts_and_freshness(tmp_path):
    obs_dir = tmp_path / "observations"
    obs_dir.mkdir()
    today = datetime.now().date().isoformat()
    payload = {"date": today, "observations": [
        {"id": "a", "source": "hackernews", "content": "x", "url": "https://a"},
        {"id": "b", "source": "hackernews", "content": "y", "url": "https://b"},
        {"id": "c", "source": "bilibili", "content": "z"},
    ]}
    f = obs_dir / f"{today}.json"
    f.write_text(__import__("json").dumps(payload, ensure_ascii=False), encoding="utf-8")
    r = sc._today_data(tmp_path)
    assert r["exists"] and r["count"] == 3
    assert r["by_source"] == {"hackernews": 2, "bilibili": 1}
    assert r["minutes_since_write"] is not None and r["minutes_since_write"] < 5


# ── 状态端点 ──────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch, tmp_path):
    from mio_taskhub.main import app
    monkeypatch.setattr(sc, "_node_processes", lambda: [])
    monkeypatch.setattr(sc.mio_runtime, "observer_base_dir", lambda: str(tmp_path))
    return TestClient(app)


def test_status_payload_shape(client, tmp_path):
    r = client.get("/api/v1/mio/scheduler-status")
    assert r.status_code == 200
    body = r.json()
    for key in ("research_scheduler", "observer_daemon", "data", "healthy", "base_dir", "hint"):
        assert key in body
    assert body["research_scheduler"]["running"] is False
    assert body["healthy"] is False


def test_activate_idempotent_when_running(client, monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_node_processes", lambda: [
        _p(100, f'C:\\node.exe {NODE} observer serve --base-dir X'),
        _p(200, f'C:\\node.exe {NODE} observe'),
    ])
    called = []
    monkeypatch.setattr(sc.subprocess, "Popen", lambda *a, **k: called.append(a) or _FakeProc())
    r = client.post("/api/v1/mio/scheduler-activate")
    assert r.status_code == 200
    body = r.json()
    assert body["already_running"] == ["serve", "daemon"]
    assert body["started"] == [] and body["errors"] == []
    assert called == []  # 全在跑，不 spawn


def test_activate_starts_missing_serve(client, monkeypatch, tmp_path):
    spawned = []

    class FakePopen:
        def __init__(self):
            self.pid = 4242

    def fake_popen(cmd, **kwargs):
        spawned.append(cmd)
        return FakePopen()

    monkeypatch.setattr(sc, "_node_processes", lambda: [])
    monkeypatch.setattr(sc.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sc, "_cli_cmd", lambda args: ["C:\\node.exe", "mio.js"] + args)
    r = client.post("/api/v1/mio/scheduler-activate")
    assert r.status_code == 200
    body = r.json()
    # 注意："observe" 含子串 "serve"，判定必须按 token 而非子串
    def has_tok(cmd, tok):
        return tok in cmd
    assert len(spawned) == 2
    assert any(has_tok(c, "serve") for c in spawned)
    daemon_cmd = next(c for c in spawned if "observe" in c and "serve" not in c)
    assert "--start" in daemon_cmd


def test_restart_stops_then_activates(client, monkeypatch):
    alive = {"n": 0}

    def fake_procs():
        alive["n"] += 1
        # restart 流程：stop 扫描时进程在 → kill 后激活扫描时空
        return [_p(100, f'C:\\node.exe {NODE} observer serve --base-dir X')] if alive["n"] == 1 else []

    monkeypatch.setattr(sc, "_node_processes", fake_procs)
    monkeypatch.setattr(sc, "_kill", lambda pid: True)
    monkeypatch.setattr(sc, "_cli_cmd", lambda args: ["C:\\node.exe", "mio.js"] + args)
    monkeypatch.setattr(sc.subprocess, "Popen", lambda cmd, **k: _FakeProc())
    r = client.post("/api/v1/mio/scheduler-restart")
    assert r.status_code == 200
    body = r.json()
    assert body["stopped"]["killed"] == ["serve (pid 100)"]
    assert len(body["activated"]["started"]) == 2


def test_stop_kills_both(client, monkeypatch):
    monkeypatch.setattr(sc, "_node_processes", lambda: [
        _p(100, f'C:\\node.exe {NODE} observer serve --base-dir X'),
        _p(200, f'C:\\node.exe {NODE} observe'),
    ])
    monkeypatch.setattr(sc, "_kill", lambda pid: True)
    r = client.post("/api/v1/mio/scheduler-stop")
    assert r.status_code == 200
    assert sorted(r.json()["killed"]) == ["daemon (pid 200)", "serve (pid 100)"]


class _FakeProc:
    def __init__(self, pid=9999):
        self.pid = pid
