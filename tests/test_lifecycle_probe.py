# -*- coding: utf-8 -*-
"""Agent 生命周期观测脚手架测试（task 5b4fa957 / P1-OBS-2 / A6 验收）。

覆盖 R282 的 8 个验收重点：
1. 重复 ingest 不产生重复事件；
2. worker 异常退出后本地记录仍存在；
3. 多进程不互相破坏 JSONL（按 pid 分文件）；
4. process_alive 是结果态回填（不 sleep grace）；
5. agent_ready 只认 Hub 侧（本模块以传入信号为准）；
6. heartbeat_sent_count 可与 Hub 收到的心跳对照；
7. 关闭观测开关时执行逻辑完全不变（零副作用）；
8. （全量套件）原有测试保持通过。

**纯观测**：不真拉 agent 进程。
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest


def _load_probe():
    p = Path(__file__).resolve().parents[1] / "mio_taskhub" / "lifecycle_probe.py"
    spec = importlib.util.spec_from_file_location("lifecycle_probe_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_ingest():
    p = Path(__file__).resolve().parents[1] / "scripts" / "ingest_lifecycle.py"
    spec = importlib.util.spec_from_file_location("ingest_lifecycle_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def probe(monkeypatch, tmp_path):
    mod = _load_probe()
    monkeypatch.setattr(mod, "probe_dir", lambda: str(tmp_path))
    monkeypatch.setenv(mod.PROBE_ENV, "1")
    return mod


# ---------- 1. 开关语义（验收 7）----------

def test_disabled_means_zero_side_effect(monkeypatch, tmp_path):
    mod = _load_probe()
    monkeypatch.setattr(mod, "probe_dir", lambda: str(tmp_path))
    monkeypatch.delenv(mod.PROBE_ENV, raising=False)
    rec = mod.emit(mod.EV_PROCESS_START, {"run_id": "r1"})
    assert rec.get("_probe_disabled") is True
    assert rec.get("_local_ok") is False
    # 关闭时连本地都不落盘
    assert list(tmp_path.glob("*.jsonl")) == []


def test_enabled_writes_local(probe, tmp_path):
    probe.emit(probe.EV_PROCESS_START, {"run_id": "r1", "pid": 111})
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text(encoding="utf-8").strip()
    assert json.loads(line)["run_id"] == "r1"


# ---------- 2. 按 pid 分文件（验收 3：多进程不互相破坏）----------

def test_per_pid_files_are_isolated(probe, tmp_path):
    probe._append_local({"run_id": "a"}, pid=1001)
    probe._append_local({"run_id": "b"}, pid=1002)
    assert (tmp_path / "1001.jsonl").exists()
    assert (tmp_path / "1002.jsonl").exists()
    assert json.loads((tmp_path / "1001.jsonl").read_text(encoding="utf-8"))["run_id"] == "a"
    assert json.loads((tmp_path / "1002.jsonl").read_text(encoding="utf-8"))["run_id"] == "b"


# ---------- 3. process_alive 结果态回填（验收 4：不 sleep）----------

def test_process_alive_is_backfilled_not_slept(probe, monkeypatch):
    # grace 很大 → 存活时长远小于它 → process_alive=False，且 finish 立即返回（无 sleep）
    monkeypatch.setenv(probe.GRACE_ENV, "3600")
    rec = probe.LifecycleRecorder("runX")
    rec.process_start()
    out = rec.finish(exit_code=0, hub_seen_heartbeat=True)
    assert out["process_alive"] is False
    assert out["alive_seconds"] is not None

    # 显式构造存活时长 > grace：直接注入 started_at（-10s）避免依赖真实时钟步进
    import time as _t
    monkeypatch.setenv(probe.GRACE_ENV, "5")
    rec2 = probe.LifecycleRecorder("runY")
    rec2.process_start()
    rec2.started_at = _t.monotonic() - 10.0
    out2 = rec2.finish(exit_code=0, hub_seen_heartbeat=True)
    assert out2["process_alive"] is True


# ---------- 4. heartbeat_sent_count 对照（验收 6）----------

def test_heartbeat_sent_count_counts(probe):
    rec = probe.LifecycleRecorder("runZ")
    rec.process_start()
    rec.heartbeat_sent()
    rec.heartbeat_sent()
    rec.heartbeat_sent()
    out = rec.finish(exit_code=0, hub_seen_heartbeat=True)
    assert out["heartbeat_sent_count"] == 3
    assert out["first_heartbeat_at"] is not None
    assert out["last_heartbeat_at"] is not None


# ---------- 5. agent_ready 与判定矩阵 ----------

def test_classify_heartbeat_broken_needs_sent(probe):
    # 本地发过心跳，但 Hub 未 ready（alive ∧ ¬ready ∧ sent>0）→ 坐实链路 C
    assert probe.classify(
        exit_rec={"process_alive": True, "agent_ready": False,
                  "heartbeat_sent_count": 2}) == "heartbeat_broken"
    # 本地没发过 → alive_no_heartbeat（起来没工作），非坐实
    assert probe.classify(
        exit_rec={"process_alive": True, "agent_ready": False,
                  "heartbeat_sent_count": 0}) == "alive_no_heartbeat"


def test_classify_startup_failure(probe):
    assert probe.classify(failure_rec={"x": 1}, success_rec=None) == "startup_failure"


def test_classify_died_after_start(probe):
    assert probe.classify(
        success_rec={"agent_ready": True},
        exit_rec={"process_alive": True, "agent_ready": True,
                  "heartbeat_sent_count": 1}) == "died_after_start"


# ---------- 6. 完整生命周期可还原（验收 1）----------

def test_full_lifecycle_reconstructable(probe, tmp_path):
    rec = probe.LifecycleRecorder("runFull", task_id="t1", agent_id="oc")
    rec.process_start(launched="opencode run ...")
    rec.heartbeat_sent()
    rec.startup_success(hub_first_heartbeat_at="2026-01-01T00:00:05Z")
    rec.finish(exit_code=0, hub_seen_heartbeat=True)

    lines = []
    for f in tmp_path.glob("*.jsonl"):
        lines += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    types = [l["event_type"] for l in lines]
    assert probe.EV_PROCESS_START in types
    assert probe.EV_STARTUP_SUCCESS in types
    assert probe.EV_PROCESS_EXIT in types
    # 全部关联同一 run_id
    assert all(l["run_id"] == "runFull" for l in lines)


# ---------- 7. ingest 幂等（验收 1 核心）----------

def _mk_db(tmp_path):
    import sqlite3
    db = tmp_path / "hub.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE event (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, "
              "entity TEXT, entity_id TEXT, run_id TEXT, payload TEXT, at TEXT)")
    c.commit()
    c.close()
    return str(db)


def test_ingest_is_idempotent(probe, tmp_path):
    ing = _load_ingest()
    pdir = tmp_path / "probe"
    pdir.mkdir()
    (pdir / "999.jsonl").write_text("\n".join([
        json.dumps({"run_id": "R", "event_type": "agent_lifecycle.process_start", "ts": "T1"}),
        json.dumps({"run_id": "R", "event_type": "agent_lifecycle.process_exit", "ts": "T2"}),
    ]), encoding="utf-8")
    db = _mk_db(tmp_path)

    s1 = ing.ingest(str(pdir), db)
    assert s1["imported"] == 2
    s2 = ing.ingest(str(pdir), db)          # 再跑一次
    assert s2["imported"] == 0
    assert s2["skipped_dup"] == 2

    import sqlite3
    c = sqlite3.connect(db)
    n = c.execute("SELECT COUNT(*) FROM event WHERE type LIKE 'agent_lifecycle.%'").fetchone()[0]
    c.close()
    assert n == 2                            # 未翻倍


def test_ingest_skips_bad_lines(probe, tmp_path):
    ing = _load_ingest()
    pdir = tmp_path / "probe2"
    pdir.mkdir()
    (pdir / "1.jsonl").write_text(
        "{bad json\n" + json.dumps({"run_id": "R", "event_type": "agent_lifecycle.process_start",
                                    "ts": "T"}) + "\n", encoding="utf-8")
    db = _mk_db(tmp_path)
    s = ing.ingest(str(pdir), db)
    assert s["imported"] == 1                # 坏行跳过，好行导入


# ---------- 8. namespace 隔离（裁决需显式排除）----------

def test_event_types_are_namespaced(probe):
    for et in probe.EVENT_TYPES:
        assert et.startswith("agent_lifecycle.")
