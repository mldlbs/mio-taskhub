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


def test_approval_requires_secret(monkeypatch):
    monkeypatch.delenv("MIO_APPROVAL_SECRET", raising=False)
    result = flow.approval_check("http://hub", "t1", "h", "c")
    assert result["ok"] is False
    assert result["error"] == "approval_secret_missing"


def test_approval_rejects_forged_artifacts(monkeypatch):
    monkeypatch.setenv("MIO_APPROVAL_SECRET", "secret")
    monkeypatch.setattr(flow, "_fetch_reviews", lambda hub, task: [{
        "id": "r1", "decision": "approve", "reviewer": "x",
        "artifacts": [
            "scope:task.updated_at", "db_path:C:/x.db",
            "db_content_hash:h", "code_commit:c",
            "expires_at:2099-01-01T00:00:00+00:00", "signature:bad",
        ],
    }])
    result = flow.approval_check("http://hub", "t1", "h", "c")
    assert result["ok"] is False
    assert result["error"] == "signature_invalid"


def test_toctou_db_change_aborts_without_write(tmp_path, monkeypatch):
    db = tmp_path / "taskhub.db"
    _make_db(db)
    expect = _sha(db)

    def tamper(*args, **kwargs):
        conn = sqlite3.connect(db)
        conn.execute("update task set title='tampered' where rowid=1")
        conn.commit()
        conn.close()
        return {"ok": True}

    monkeypatch.setattr(flow, "approval_check", tamper)
    report = flow.run_flow(db, expect, tmp_path / "pre.db", "http://hub",
                           "t1", app_read_check=False)
    assert report["ok"] is False
    assert report["stage"] == "apply_failed"
    assert "toctou_db_changed" in str(report["checks"])
    conn = sqlite3.connect(db)
    assert "updated_at" not in [r[1] for r in conn.execute("pragma table_info(task)")]
    assert conn.execute("select title from task where rowid=1").fetchone()[0] == "tampered"
    conn.close()


def test_toctou_code_change_aborts(tmp_path, monkeypatch):
    db = tmp_path / "taskhub.db"
    _make_db(db)
    _approve(monkeypatch)
    heads = iter(["a" * 40, "b" * 40])
    monkeypatch.setattr(flow, "_head", lambda: next(heads))
    report = flow.run_flow(db, _sha(db), tmp_path / "pre.db", "http://hub",
                           "t1", app_read_check=False)
    assert report["ok"] is False
    assert "toctou_code_changed" in str(report["checks"])


def test_rollback_preserves_concurrent_write(tmp_path, monkeypatch):
    db = tmp_path / "taskhub.db"
    _make_db(db)
    backup = tmp_path / "pre.db"
    _approve(monkeypatch)

    def concurrent_then_fail(path):
        conn = sqlite3.connect(path)
        conn.execute("update task set title='concurrent' where rowid=1")
        conn.commit()
        conn.close()
        return False, "forced failure"

    monkeypatch.setattr(flow, "app_read", concurrent_then_fail)
    report = flow.run_flow(db, _sha(db), backup, "http://hub", "t1",
                           app_read_check=True)
    assert report["ok"] is False
    assert report["rolled_back"] is True
    conn = sqlite3.connect(db)
    assert "updated_at" not in [r[1] for r in conn.execute("pragma table_info(task)")]
    assert conn.execute("select title from task where rowid=1").fetchone()[0] == "concurrent"
    assert conn.execute("select count(*) from task").fetchone()[0] == 2
    conn.close()
