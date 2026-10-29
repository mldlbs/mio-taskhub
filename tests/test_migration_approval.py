# -*- coding: utf-8 -*-
"""迁移审批：服务端绑定、签名、过期、伪造。"""
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlmodel import Session

from mio_taskhub.models import SQLModel, Task, TaskStage, TaskState
from scripts import migration_approval as appr


def _db(path):
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Task(id="t1", title="approval", state=TaskState.QUEUED,
                   stage=TaskStage.REVIEW))
        s.add(Task(id="t2", title="not review", state=TaskState.QUEUED,
                   stage=TaskStage.READY))
        s.commit()
    eng.dispose()


def test_approve_requires_review_stage(tmp_path):
    db = tmp_path / "hub.db"
    _db(db)
    result = appr.approve(db, "t2", db, "secret")
    assert result["ok"] is False
    assert result["error"] == "task_not_in_review"


def test_approve_and_verify(tmp_path):
    db = tmp_path / "hub.db"
    _db(db)
    result = appr.approve(db, "t1", db, "secret", ttl_hours=24)
    assert result["ok"] is True
    verified = appr.verify("secret", result["artifacts"],
                           result["db_content_hash"], result["code_commit"], "t1")
    assert verified["ok"] is True


def test_expired_approval_is_rejected(tmp_path):
    db = tmp_path / "hub.db"
    _db(db)
    result = appr.approve(db, "t1", db, "secret", ttl_hours=-1)
    assert result["ok"] is True
    verified = appr.verify("secret", result["artifacts"],
                           result["db_content_hash"], result["code_commit"], "t1")
    assert verified["ok"] is False
    assert verified["error"] == "approval_expired"


def test_forged_signature_is_rejected(tmp_path):
    db = tmp_path / "hub.db"
    _db(db)
    result = appr.approve(db, "t1", db, "secret")
    artifacts = [a.replace("signature:", "signature:dead") if a.startswith("signature:") else a
                 for a in result["artifacts"]]
    verified = appr.verify("secret", artifacts, result["db_content_hash"],
                           result["code_commit"], "t1")
    assert verified["ok"] is False
    assert verified["error"] == "signature_invalid"


def test_wrong_target_hash_is_rejected(tmp_path):
    db = tmp_path / "hub.db"
    _db(db)
    result = appr.approve(db, "t1", db, "secret")
    verified = appr.verify("secret", result["artifacts"], "0" * 64,
                           result["code_commit"], "t1")
    assert verified["ok"] is False
    assert verified["error"] == "db_hash_mismatch"
