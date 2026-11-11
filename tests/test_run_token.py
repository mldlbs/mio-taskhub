"""per-run token（F-005 第 2 步，2026-11-11）回归用例。

覆盖：
- claim 下发 run_token（明文只出现一次），Run 只存 sha256；
- heartbeat / submit_result 带 X-Run-Token：对 → 放行，错 → 403 run.token_mismatch；
- 不带 token：默认放行（宽松兼容），MIO_TASKHUB_RUN_TOKEN_REQUIRED=1 时
  403 run.token_required；
- 无 token_hash 的旧 run（迁移前数据）在任何模式下都放行（无法凭空补发）；
- 幂等续领（同 agent 二次 claim）：带对 token 回显、带错 403；
- 旧库迁移补列（run_migrations 幂等）。
"""
import hashlib
import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect
from sqlmodel import SQLModel, Session

from mio_taskhub.main import app
from mio_taskhub.models import Run, RunState
from mio_taskhub.db import engine, init_db

client = TestClient(app)


def _claim(agent: str = "tok-agent", **params):
    r = client.post("/api/v1/tasks/claim", params={"agent": agent, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _mk_claimed(workspace: pathlib.Path, agent: str = "tok-agent"):
    """建任务 → claim，返回 (claim resp, task_id)。"""
    r = client.post("/api/v1/tasks", json={"title": "run-token", "workspace": str(workspace)})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]
    resp = _claim(agent, task_id=tid)
    return resp, tid


def test_claim_issues_run_token(tmp_path):
    resp, _tid = _mk_claimed(tmp_path)
    assert resp.get("run_token"), sorted(resp.keys())
    with Session(engine) as s:
        run = s.get(Run, resp["id"])
        assert run.token_hash
        assert run.token_hash == hashlib.sha256(resp["run_token"].encode()).hexdigest()
        # 明文绝不落库
        assert resp["run_token"] not in (run.token_hash or "")


def test_heartbeat_with_correct_token(tmp_path):
    resp, _tid = _mk_claimed(tmp_path)
    r = client.post(f"/api/v1/runs/{resp['id']}/heartbeat",
                    params={"agent": "tok-agent"},
                    headers={"X-Run-Token": resp["run_token"]},
                    json={"progress": 10})
    assert r.status_code == 200, r.text


def test_heartbeat_with_wrong_token_403(tmp_path):
    resp, _tid = _mk_claimed(tmp_path)
    r = client.post(f"/api/v1/runs/{resp['id']}/heartbeat",
                    params={"agent": "tok-agent"},
                    headers={"X-Run-Token": "wrong-token"},
                    json={"progress": 10})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "run.token_mismatch"


def test_heartbeat_without_token_compat(tmp_path):
    """默认宽松：不带 token 放行（存量调用方不破坏）。"""
    resp, _tid = _mk_claimed(tmp_path)
    r = client.post(f"/api/v1/runs/{resp['id']}/heartbeat",
                    params={"agent": "tok-agent"}, json={"progress": 10})
    assert r.status_code == 200, r.text


def test_heartbeat_without_token_required_403(tmp_path, monkeypatch):
    resp, _tid = _mk_claimed(tmp_path)
    monkeypatch.setenv("MIO_TASKHUB_RUN_TOKEN_REQUIRED", "1")
    r = client.post(f"/api/v1/runs/{resp['id']}/heartbeat",
                    params={"agent": "tok-agent"}, json={"progress": 10})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "run.token_required"


def test_legacy_run_without_hash_allowed_under_required(tmp_path, monkeypatch):
    """迁移前的旧 run 没有 token_hash：强模式下也放行（无法凭空补发）。"""
    monkeypatch.setenv("MIO_TASKHUB_RUN_TOKEN_REQUIRED", "1")
    with Session(engine) as s:
        run = Run(id="legacy01", task_id="no-task", agent_name="legacy-agent",
                  state=RunState.CLAIMED)
        s.add(run)
        s.commit()
    r = client.post("/api/v1/runs/legacy01/heartbeat",
                    params={"agent": "legacy-agent"}, json={"progress": 5})
    assert r.status_code == 200, r.text


def test_submit_result_wrong_token_403(tmp_path):
    resp, _tid = _mk_claimed(tmp_path)
    r = client.post(f"/api/v1/runs/{resp['id']}/result",
                    params={"agent": "tok-agent"},
                    headers={"X-Run-Token": "bad"},
                    json={"success": False, "result": "boom"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "run.token_mismatch"


def test_submit_result_failure_correct_token_passes_token_gate(tmp_path):
    """失败提交不带 ReadEvidence 门控：token 正确时应越过凭据关（进入业务流转）。"""
    resp, tid = _mk_claimed(tmp_path)
    r = client.post(f"/api/v1/runs/{resp['id']}/result",
                    params={"agent": "tok-agent"},
                    headers={"X-Run-Token": resp["run_token"]},
                    json={"success": False, "result": "boom", "exit_code": 1})
    assert r.status_code == 200, r.text
    # 失败路径进入重试或 failed：响应的 state 是 run 状态（finished），
    # task_state 才是任务侧流转结果；到这里即证明已越过 per-run 凭据关。
    body = r.json()
    assert body.get("task_state") in ("retrying", "failed", "completed"), body


def test_idempotent_reclaim_token_roundtrip(tmp_path):
    resp, _tid = _mk_claimed(tmp_path)
    # 同 agent 再 claim → 幂等续领
    r2 = client.post("/api/v1/tasks/claim",
                     params={"agent": "tok-agent", "run_token": resp["run_token"]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == resp["id"]
    assert r2.json()["run_token"] == resp["run_token"]


def test_idempotent_reclaim_wrong_token_403(tmp_path):
    resp, _tid = _mk_claimed(tmp_path)
    r = client.post("/api/v1/tasks/claim",
                    params={"agent": "tok-agent", "run_token": "nope"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "run.token_mismatch"


def test_idempotent_reclaim_without_token_required_403(tmp_path, monkeypatch):
    _mk_claimed(tmp_path)
    monkeypatch.setenv("MIO_TASKHUB_RUN_TOKEN_REQUIRED", "1")
    r = client.post("/api/v1/tasks/claim", params={"agent": "tok-agent"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "run.token_required"


def test_migration_adds_token_hash_column(tmp_path):
    """旧库（无 token_hash 列）经 run_migrations 补列，且幂等。"""
    from mio_taskhub.migrations import run_migrations
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    # 老结构 run 表：只有核心列，无 token_hash
    conn.execute("""
        CREATE TABLE run (
            id VARCHAR PRIMARY KEY,
            task_id VARCHAR,
            agent_name VARCHAR,
            state VARCHAR,
            attempt INTEGER,
            progress INTEGER,
            started_at TIMESTAMP,
            last_heartbeat TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO run (id, task_id, agent_name, state) VALUES ('r1','t1','a','claimed')")
    conn.commit()
    conn.close()
    # SQLModel 建表先跑（新结构含 token_hash），migrations 对老库补列才是有效路径；
    # 这里直接对老结构文件验证 migrate 的幂等补列逻辑。
    from sqlalchemy import create_engine, text as sa_text
    from sqlmodel import SQLModel as _SM
    legacy_engine = create_engine(f"sqlite:///{db_path}")
    # migrations.run_migrations 需要表存在；老表已在。跑两遍验证幂等。
    # run_migrations 内部建缺失表用 SQLModel.metadata.create_all —— 会把新表加进来，
    # 但 ALTER 只对缺列的既有表生效，不影响幂等性。
    run_migrations(legacy_engine)
    run_migrations(legacy_engine)
    with legacy_engine.connect() as c:
        cols = {row[1] for row in c.execute(sa_text("PRAGMA table_info(run)"))}
    assert "token_hash" in cols
