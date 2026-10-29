# -*- coding: utf-8 -*-
"""迁移流程：预检、授权、执行、验证、失败回滚、幂等。"""
import sqlite3

from sqlalchemy import create_engine
from sqlmodel import Session

from mio_taskhub.models import SQLModel, Task, TaskStage, TaskState
from scripts import migrate_task_updated_at_flow as flow


def _make_db(path):
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Task(title="a", state=TaskState.QUEUED, stage=TaskStage.READY))
        s.add(Task(title="b", state=TaskState.QUEUED, stage=TaskStage.READY))
        s.commit()
    eng.dispose()
    conn = sqlite3.connect(path)
    conn.execute("alter table task drop column updated_at")
    conn.commit()
    conn.close()


def _sha(path):
    return flow._content_hash(path)


def _approve(monkeypatch):
    monkeypatch.setattr(flow, "approval_check",
                        lambda *a, **k: {"ok": True, "review_id": "r1"})


def test_flow_applies_and_is_idempotent(tmp_path, monkeypatch):
    db = tmp_path / "taskhub.db"
    _make_db(db)
    backup = tmp_path / "pre.db"
    _approve(monkeypatch)
    first = flow.run_flow(db, _sha(db), backup, "http://hub", "t1",
                          app_read_check=True)
    assert first["ok"] is True, first["checks"]
    assert first["stage"] == "verified"
    conn = sqlite3.connect(db)
    assert "updated_at" in [r[1] for r in conn.execute("pragma table_info(task)")]
    assert conn.execute(
        "select count(*) from task where updated_at is null").fetchone()[0] == 0
    conn.close()

    second = flow.run_flow(db, _sha(db), backup, "http://hub", "t1",
                           app_read_check=True)
    assert second["ok"] is True
    assert second["stage"] == "already_applied"


def test_flow_rolls_back_on_postcheck_failure(tmp_path, monkeypatch):
    db = tmp_path / "taskhub.db"
    _make_db(db)
    backup = tmp_path / "pre.db"
    _approve(monkeypatch)
    monkeypatch.setattr(flow, "app_read", lambda path: (False, "forced failure"))
    report = flow.run_flow(db, _sha(db), backup, "http://hub", "t1",
                           app_read_check=True)
    assert report["ok"] is False
    assert report["rolled_back"] is True
    assert report["stage"] == "rolled_back"
    conn = sqlite3.connect(db)
    assert "updated_at" not in [r[1] for r in conn.execute("pragma table_info(task)")]
    assert conn.execute("select count(*) from task").fetchone()[0] == 2
    conn.close()


def test_flow_aborts_on_identity_mismatch(tmp_path, monkeypatch):
    db = tmp_path / "taskhub.db"
    _make_db(db)
    _approve(monkeypatch)
    report = flow.run_flow(db, "0" * 64, tmp_path / "pre.db", "http://hub",
                           "t1", app_read_check=False)
    assert report["ok"] is False
    assert report["stage"] == "preflight"
    assert not (tmp_path / "pre.db").exists()


def test_flow_requires_matching_approval(tmp_path, monkeypatch):
    db = tmp_path / "taskhub.db"
    _make_db(db)
    monkeypatch.setattr(flow, "approval_check",
                        lambda *a, **k: {"ok": False, "error": "no_matching_approval"})
    report = flow.run_flow(db, _sha(db), tmp_path / "pre.db", "http://hub",
                           "t1", app_read_check=False)
    assert report["ok"] is False
    assert report["stage"] == "approval"
    conn = sqlite3.connect(db)
    assert "updated_at" not in [r[1] for r in conn.execute("pragma table_info(task)")]
    conn.close()
