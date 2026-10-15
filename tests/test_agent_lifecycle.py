# tests/test_agent_lifecycle.py — T0.7 agent_lifecycle.* 三类埋点
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.observability import lifecycle as lc

client = TestClient(app)
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ingest_lifecycle.py"


@pytest.fixture(autouse=True)
def _isolated_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_HOME", str(tmp_path / "mio_home"))
    with lc._probe_gate_lock:
        lc._probed_pid.clear()
    with lc._sent_lock:
        lc._sent_counts.clear()
    yield
    lc.flush(timeout=3)


def _entries(home: Path, run_id: str = None):
    root = home / "lifecycle"
    out = []
    if not root.exists():
        return out
    for p in sorted(root.rglob("*.jsonl")):
        if run_id is not None and p.stem != run_id:
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _home() -> Path:
    return Path(os.environ["MIO_HOME"])


def _claim(run_name="lc-agent"):
    client.post("/api/v1/tasks",
                json={"title": f"lifecycle {run_name}", "stage": "ready"})
    client.post("/api/v1/agents/register",
                json={"name": run_name, "agent_type": "test"})
    r = client.post("/api/v1/tasks/claim", params={"agent": run_name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_record_writes_jsonl_per_pid_run():
    lc.record(lc.TYPE_AGENT_READY, "runrec1", {"progress": 5})
    assert lc.flush()
    path = _home() / "lifecycle" / str(os.getpid()) / "runrec1.jsonl"
    assert path.exists()
    e = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert e["type"] == "agent_lifecycle.agent_ready"
    assert e["run_id"] == "runrec1"
    assert e["pid"] == os.getpid()
    assert e["progress"] == 5
    assert e["ts"]


def test_record_never_raises_and_worker_survives_write_failure(monkeypatch):
    calls = {"n": 0}
    real_write = lc._write_entry

    def flaky(entry):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full (simulated)")
        real_write(entry)

    monkeypatch.setattr(lc, "_write_entry", flaky)
    lc.record(lc.TYPE_AGENT_READY, "runflaky", {})   # 第一条：写失败被丢弃
    lc.record(lc.TYPE_AGENT_READY, "runflaky", {})   # 第二条：worker 存活写出
    assert lc.flush()
    path = _home() / "lifecycle" / str(os.getpid()) / "runflaky.jsonl"
    assert path.exists()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_first_heartbeat_emits_agent_ready_and_process_alive():
    rid = _claim("lc-hb1")
    r = client.post(f"/api/v1/runs/{rid}/heartbeat",
                    json={"progress": 10, "pid": os.getpid()})
    assert r.status_code == 200
    assert lc.flush()
    entries = _entries(_home(), rid)
    types = [e["type"] for e in entries]
    assert "agent_lifecycle.agent_ready" in types
    ready = [e for e in entries if e["type"] == "agent_lifecycle.agent_ready"]
    assert ready[0]["task_id"]
    assert ready[0]["agent_id"] == "lc-hb1"

    # 探测异步回填：等 process_alive 落盘
    import time
    alive = []
    for _ in range(50):
        alive = [e for e in _entries(_home(), rid)
                 if e["type"] == "agent_lifecycle.process_alive"]
        if alive:
            break
        time.sleep(0.05)
    assert alive, "process_alive 未回填"
    assert alive[0]["pid"] == os.getpid()
    assert alive[0]["alive"] is True

    # 第二次心跳（同 pid）：agent_ready 不重复、probe 不重复
    client.post(f"/api/v1/runs/{rid}/heartbeat",
                json={"progress": 20, "pid": os.getpid()})
    assert lc.flush()
    time.sleep(0.1)
    entries = _entries(_home(), rid)
    assert sum(1 for e in entries
               if e["type"] == "agent_lifecycle.agent_ready") == 1
    assert sum(1 for e in entries
               if e["type"] == "agent_lifecycle.process_alive") == 1


def test_process_alive_dead_pid_backfills_false():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    dead_pid = proc.pid
    assert lc.probe_process_alive("rundead", dead_pid, force=True)
    import time
    got = []
    for _ in range(50):
        got = [e for e in _entries(_home(), "rundead")
               if e["type"] == "agent_lifecycle.process_alive"]
        if got:
            break
        time.sleep(0.05)
    assert got, "dead probe 未回填"
    assert got[0]["pid"] == dead_pid
    assert got[0]["alive"] is False


def test_note_heartbeat_sent_counter_and_delivered():
    n1 = lc.note_heartbeat_sent("runsent", delivered=True)
    n2 = lc.note_heartbeat_sent("runsent", delivered=False)
    n3 = lc.note_heartbeat_sent("runsent", delivered=True)
    assert (n1, n2, n3) == (1, 2, 3)
    assert lc.flush()
    entries = _entries(_home(), "runsent")
    assert [e["sent_count"] for e in entries] == [1, 2, 3]
    assert [e["delivered"] for e in entries] == [True, False, True]
    assert all(e["type"] == "agent_lifecycle.heartbeat_sent_count"
               for e in entries)


def test_probe_gate_skips_same_pid():
    assert lc.probe_process_alive("rungate", os.getpid()) is True
    assert lc.probe_process_alive("rungate", os.getpid()) is False  # 同 pid 去重
    assert lc.probe_process_alive("rungate", 0) is False            # 非法 pid


def test_mcp_heartbeat_sends_pid_and_records_sent(monkeypatch):
    from mio_taskhub import mcp_server
    captured = []

    async def fake_request(method, path, params=None, body=None):
        captured.append({"method": method, "path": path, "body": body})
        return {"id": "rx", "state": "running", "progress": 50}

    monkeypatch.setattr(mcp_server, "_request", fake_request)
    out = asyncio.run(mcp_server.taskhub_heartbeat(run_id="runmcp", progress=50))
    assert captured[0]["body"]["pid"] == os.getpid()
    assert captured[0]["path"] == "/runs/runmcp/heartbeat"
    assert lc.flush()
    entries = _entries(_home(), "runmcp")
    assert entries and entries[0]["type"] == "agent_lifecycle.heartbeat_sent_count"
    assert entries[0]["sent_count"] == 1
    assert entries[0]["delivered"] is True

    # 传输失败：delivered=false（网络分区对账证据）
    async def failing_request(method, path, params=None, body=None):
        return {"error": "无法连接 hub"}
    monkeypatch.setattr(mcp_server, "_request", failing_request)
    asyncio.run(mcp_server.taskhub_heartbeat(run_id="runmcp", progress=51))
    assert lc.flush()
    entries = _entries(_home(), "runmcp")
    assert entries[-1]["sent_count"] == 2
    assert entries[-1]["delivered"] is False


def test_heartbeat_unaffected_when_lifecycle_broken(monkeypatch):
    rid = _claim("lc-broken")

    def boom(_entry):
        raise RuntimeError("lifecycle dir gone")

    monkeypatch.setattr(lc, "_write_entry", boom)
    r = client.post(f"/api/v1/runs/{rid}/heartbeat",
                    json={"progress": 30, "pid": os.getpid()})
    assert r.status_code == 200
    assert r.json()["state"] == "running"


def test_ingest_lifecycle_idempotent(tmp_path):
    db = tmp_path / "ing_test.db"
    # 本地落三类证据（写入 fixture 的 MIO_HOME）
    for t, run in [(lc.TYPE_AGENT_READY, "runing"),
                   (lc.TYPE_HEARTBEAT_SENT, "runing"),
                   (lc.TYPE_PROCESS_ALIVE, "runing")]:
        lc.record(t, run, {"ts_hint": 1})
    assert lc.flush()

    env = {**os.environ,  # MIO_HOME 沿用 fixture 隔离目录
           "MIO_TASKHUB_DB": str(db),
           "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    run = lambda *a: subprocess.run(  # noqa: E731
        [sys.executable, str(SCRIPT), *a],
        capture_output=True, text=True, env=env, timeout=60)

    dry = run("--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert "new=3" in dry.stdout
    assert not db.exists() or _count_events(db) == 0

    a1 = run("--apply")
    assert a1.returncode == 0, a1.stderr
    assert "new=3" in a1.stdout
    assert _count_events(db) == 3

    a2 = run("--apply")   # 重放：幂等，0 新增
    assert a2.returncode == 0, a2.stderr
    assert "new=0" in a2.stdout
    assert "dup_skipped=3" in a2.stdout
    assert _count_events(db) == 3


def _count_events(db_path) -> int:
    import sqlite3
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "select count(*) from event where type like 'agent_lifecycle%'"
        ).fetchone()[0]
    finally:
        con.close()
