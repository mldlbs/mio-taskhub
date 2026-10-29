# -*- coding: utf-8 -*-
"""门禁报告与代码/数据库绑定校验。"""
import json
from pathlib import Path

from sqlalchemy import create_engine

from mio_taskhub.materials.bootstrap import init_materials_db
from scripts.materials_gate import run_gate
from scripts.verify_gate_report import verify


def _report(tmp_path):
    db = tmp_path / "gate.db"
    eng = create_engine(f"sqlite:///{db.as_posix()}")
    init_materials_db(eng)
    eng.dispose()
    report = run_gate(db)
    assert report["ok"] is True, report["blocking_failures"]
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return db, path, report


def test_valid_report_verifies(tmp_path):
    db, path, report = _report(tmp_path)
    problems = verify(path, db, report["binding"]["code_commit"],
                      allow_dirty=True)
    assert problems == []


def test_tampered_report_is_rejected(tmp_path):
    db, path, report = _report(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["ok"] = True
    data["metrics"]["materials"] = 999
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    problems = verify(path, db, report["binding"]["code_commit"],
                      allow_dirty=True)
    assert "report_fingerprint_mismatch" in problems


def test_changed_db_is_rejected(tmp_path):
    db, path, report = _report(tmp_path)
    with db.open("ab") as fh:
        fh.write(b"x")
    problems = verify(path, db, report["binding"]["code_commit"],
                      allow_dirty=True)
    assert "db_sha256_mismatch" in problems
    assert "db_size_mismatch" in problems


def test_wrong_commit_is_rejected(tmp_path):
    db, path, report = _report(tmp_path)
    problems = verify(path, db, "0" * 40, allow_dirty=True)
    assert "code_commit_mismatch" in problems


def test_failed_gate_report_is_rejected(tmp_path):
    report = run_gate(tmp_path / "missing.db")
    path = tmp_path / "failed.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    problems = verify(path, Path(report["binding"]["db_path"]),
                      report["binding"]["code_commit"], allow_dirty=True)
    assert "gate_not_ok" in problems
