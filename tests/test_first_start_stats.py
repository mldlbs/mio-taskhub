# -*- coding: utf-8 -*-
"""T1.4 first_start_stats（D1 决策门数据采集）单测。

覆盖：
  1. _classify_failure 三分根因穷举；
  2. collect 合成 DB：n/结局/成功率/根因/reaper 样本/门控；
  3. --min-n 门控 exit code（n 不足 → 1）。
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "first_start_stats.py"
sys.path.insert(0, str(REPO / "scripts"))

import first_start_stats  # noqa: E402

PY = str(REPO / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable


def _mk_db(tmp_path, n_tasks=5):
    """合成最小 run/event/task 三表。返回 db path。"""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE run (id TEXT PRIMARY KEY, task_id TEXT, agent_name TEXT,
            state TEXT, attempt INTEGER, checkpoint TEXT, progress INTEGER,
            started_at TEXT, last_heartbeat TEXT, finished_at TEXT,
            result TEXT, exit_code INTEGER);
        CREATE TABLE task (id TEXT PRIMARY KEY, state TEXT, bounce_count INTEGER DEFAULT 0);
        CREATE TABLE event (id INTEGER PRIMARY KEY, run_id TEXT, type TEXT,
            payload TEXT, at TEXT, entity TEXT, entity_id TEXT);
    """)
    for i in range(n_tasks):
        tid = f"task{i:04d}"
        # 首启 run（每个任务一个 attempt=1 run；started_at 排序保证 MIN 正确）
        conn.execute(
            "INSERT INTO run (id, task_id, agent_name, state, attempt, progress, started_at) "
            "VALUES (?, ?, 'agent', 'FINISHED', 1, ?, ?)",
            (f"run{i:04d}", tid, 50 if i % 2 else 0,
             f"2026-10-01T00:{i:02d}:00Z"))
    conn.commit()
    return db, conn


def test_classify_failure_exhaustive():
    # outcome=fail → real_death（无论 kind）
    assert first_start_stats._classify_failure("never_started", "fail", 0) == "real_death"
    assert first_start_stats._classify_failure("agent_offline", "fail", 50) == "real_death"
    # never_started + requeue → 观测不足
    assert first_start_stats._classify_failure("never_started", "requeue", 0) == "insufficient_obs"
    # agent_offline + requeue → 链路断
    assert first_start_stats._classify_failure("agent_offline", "requeue", 50) == "link_broken"
    # 无 reaper 事件 → real_death（显式失败提交）
    assert first_start_stats._classify_failure(None, None, 100) == "real_death"


def test_collect_synthetic(tmp_path):
    db, conn = _mk_db(tmp_path, n_tasks=6)
    try:
        # 任务结局：0-1 COMPLETED, 2-3 FAILED, 4 CANCELLED, 5 QUEUED
        states = ["completed", "completed", "failed", "failed", "cancelled", "queued"]
        for i, st in enumerate(states):
            conn.execute("INSERT INTO task (id, state) VALUES (?, ?)",
                         (f"task{i:04d}", st))
        # reaper 事件：task2 never_started/requeue, task3 agent_offline/fail
        conn.execute(
            "INSERT INTO event (run_id, type, payload, at) VALUES "
            "('run0002', 'reaper_decision', ?, '2026-10-01T01:00:00Z')",
            (json.dumps({"run_id": "run0002", "reaper_kind": "never_started",
                         "outcome": "requeue"}),))
        conn.execute(
            "INSERT INTO event (run_id, type, payload, at) VALUES "
            "('run0003', 'reaper_decision', ?, '2026-10-01T01:00:00Z')",
            (json.dumps({"run_id": "run0003", "reaper_kind": "agent_offline",
                         "outcome": "fail"}),))
        conn.commit()
        stats = first_start_stats.collect(conn)
        assert stats["n_first_start"] == 6
        assert stats["outcome_dist"] == {"COMPLETED": 2, "FAILED": 2,
                                         "CANCELLED": 1, "QUEUED": 1}
        assert stats["first_start_success"] == 2
        assert stats["decided"] == 4  # COMPLETED+FAILED
        assert stats["success_rate"] == pytest.approx(0.5)
        fc = stats["failure_causes"]
        assert fc["insufficient_obs"] == 1
        assert fc["real_death"] == 1  # task3 agent_offline+fail
        assert fc["link_broken"] == 0
        assert stats["reaper_decision_samples"] == 2
        assert stats["gates"]["n_ge_min"] is False
        assert stats["gates"]["reaper_ge_30"] is False
    finally:
        conn.close()


def test_collect_gate_pass_synthetic(tmp_path):
    """n≥100 + reaper≥30 时门控全绿。"""
    db, conn = _mk_db(tmp_path, n_tasks=120)
    try:
        for i in range(120):
            st = "completed" if i < 100 else ("failed" if i < 110 else "cancelled")
            conn.execute("INSERT INTO task (id, state) VALUES (?, ?)",
                         (f"task{i:04d}", st))
            if i >= 100:  # 30 个 reaper 样本（挂到 FAILED 首启上）
                conn.execute(
                    "INSERT INTO event (run_id, type, payload, at) VALUES "
                    "(?, 'reaper_decision', ?, ?)",
                    (f"run{i:04d}",
                     json.dumps({"run_id": f"run{i:04d}",
                                 "reaper_kind": "never_started",
                                 "outcome": "requeue"}),
                     f"2026-10-01T02:{i:02d}:00Z"))
        conn.commit()
        stats = first_start_stats.collect(conn)
        assert stats["n_first_start"] == 120
        assert stats["gates"]["n_ge_min"] is True  # min_n 默认 100
        assert stats["reaper_decision_samples"] == 20  # i=100..119 全挂了
        # 10 个 FAILED 都挂了 never_started/requeue → 全归观测不足
        assert stats["failure_causes"]["insufficient_obs"] == 10
        assert stats["failure_causes"]["real_death"] == 0
    finally:
        conn.close()


def test_cli_min_n_gate(tmp_path):
    """n<min-n → exit 1；n≥min-n → exit 0。"""
    db, conn = _mk_db(tmp_path, n_tasks=3)
    try:
        for i in range(3):
            conn.execute("INSERT INTO task (id, state) VALUES (?, ?)",
                         (f"task{i:04d}", "completed"))
        conn.commit()
    finally:
        conn.close()
    # n=3 < min-n=100 → exit 1
    proc = subprocess.run([PY, str(SCRIPT), "--db", str(db), "--min-n", "100"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 1, proc.stderr
    assert "FAIL" in proc.stderr
    # min-n=3 → exit 0
    proc2 = subprocess.run([PY, str(SCRIPT), "--db", str(db), "--min-n", "3", "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert proc2.returncode == 0, proc2.stderr
    data = json.loads(proc2.stdout)
    assert data["n_first_start"] == 3
    assert data["gates"]["n_ge_min"] is True


def test_cli_real_db_smoke():
    """真库冒烟：脚本可跑、JSON 含关键键（若 DB 存在）。"""
    db = Path.home() / ".mio_taskhub" / "taskhub.db"
    if not db.exists():
        pytest.skip("real db not found")
    proc = subprocess.run([PY, str(SCRIPT), "--db", str(db), "--json"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode in (0, 1), proc.stderr
    data = json.loads(proc.stdout)
    assert "n_first_start" in data
    assert "failure_causes" in data
    assert set(data["failure_causes"]) == {"insufficient_obs", "link_broken", "real_death"}
