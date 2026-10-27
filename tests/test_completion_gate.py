# -*- coding: utf-8 -*-
"""Phase 1 完成门禁核心测试。"""
import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Run, Task

client = TestClient(app)


def _agent() -> str:
    return "p1-" + uuid.uuid4().hex[:8]


def _mk(**body):
    payload = {"title": "p1-task", "stage": "ready"}
    payload.update(body)
    r = client.post("/api/v1/tasks", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _claim(tid, agent=None):
    agent = agent or _agent()
    r = client.post("/api/v1/tasks/claim", params={"agent": agent, "task_id": tid})
    assert r.status_code == 200, r.text
    return r.json(), agent


def _submit(rid, body):
    return client.post(f"/api/v1/runs/{rid}/result", json=body)


def _codes(resp):
    detail = resp.json().get("detail")
    if isinstance(detail, dict):
        return detail.get("code") or {e.get("code") for e in detail.get("errors", [])}
    return detail


def test_light_result_and_artifact_completes(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_COMPLETION_GATE", "block")
    monkeypatch.setenv("MIO_READ_GATE", "0")
    t = _mk(workspace=str(tmp_path))
    run, _ = _claim(t["id"])
    r = _submit(run["id"], {"success": True, "result": "实现了 X 并跑通测试",
                            "evidence": {"verification": {"artifacts": ["a.py"], "tests": [{"name": "t", "passed": True}]}}})
    assert r.status_code == 200, r.text
    with Session(engine) as db:
        task = db.get(Task, t["id"])
        assert task.completion_run_id == run["id"]
        rr = db.get(Run, run["id"])
        assert rr.evidence["verification"]["artifacts"] == ["a.py"]
        assert rr.evidence["captured_at"]


def test_thin_result_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_COMPLETION_GATE", "block")
    monkeypatch.setenv("MIO_READ_GATE", "0")
    t = _mk(workspace=str(tmp_path))
    run, _ = _claim(t["id"])
    r = _submit(run["id"], {"success": True, "result": "x",
                            "evidence": {"verification": {"artifacts": ["a.py"], "tests": [{"name": "t", "passed": True}]}}})
    assert r.status_code == 422
    assert _codes(r) == "gate.result_too_thin"


def test_standard_registered_topic_missing_file_blocks_claim(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_CLAIM_GATE", "block")
    t = _mk(workspace=str(tmp_path), spec_path="docs/spec.md")
    r = client.post("/api/v1/tasks/claim", params={"agent": _agent(), "task_id": t["id"]})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "gate.doc_missing"
    assert r.json()["detail"]["missing"][0]["kind"] == "spec"


def test_verification_failed_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_COMPLETION_GATE", "block")
    monkeypatch.setenv("MIO_READ_GATE", "0")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "spec.md").write_text("# spec", encoding="utf-8")
    t = _mk(workspace=str(tmp_path), spec_path="docs/spec.md")
    run, _ = _claim(t["id"])
    r = _submit(run["id"], {
        "success": True, "result": "实现了 X 并跑通测试",
        "evidence": {"verification": {"tests": [{"name": "t", "passed": False}]}},
    })
    assert r.status_code == 422
    assert _codes(r) == "gate.verification_failed"


def test_deliverable_missing_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_COMPLETION_GATE", "block")
    monkeypatch.setenv("MIO_READ_GATE", "0")
    t = _mk(workspace=str(tmp_path), deliverables=["nope.py"])
    run, _ = _claim(t["id"])
    r = _submit(run["id"], {"success": True, "result": "实现了 X 并跑通测试",
                            "evidence": {"verification": {"artifacts": ["a.py"], "tests": [{"name": "t", "passed": True}]}}})
    assert r.status_code == 422
    assert _codes(r) == "gate.deliverable_missing"


def test_high_deviation_needs_decision(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_COMPLETION_GATE", "block")
    monkeypatch.setenv("MIO_READ_GATE", "0")
    t = _mk(workspace=str(tmp_path))
    d = client.post(f"/api/v1/tasks/{t['id']}/deviations", json={
        "description": "少了一个错误码", "severity": "high"})
    assert d.status_code == 200
    run, _ = _claim(t["id"])
    r = _submit(run["id"], {"success": True, "result": "实现了 X 并跑通测试",
                            "evidence": {"verification": {"artifacts": ["a.py"], "tests": [{"name": "t", "passed": True}]}}})
    assert r.status_code == 422
    assert _codes(r) == "gate.deviation_needs_decision"
    bad = client.post(f"/api/v1/tasks/{t['id']}/deviations/{d.json()['id']}/resolve",
                      json={"status": "accepted"})
    assert bad.status_code == 422
    ok = client.post(f"/api/v1/tasks/{t['id']}/deviations/{d.json()['id']}/resolve",
                     json={"status": "accepted", "decision_ref": "discussion:x",
                           "decided_by": "user"})
    assert ok.status_code == 200
    r2 = _submit(run["id"], {"success": True, "result": "实现了 X 并跑通测试",
                             "evidence": {"verification": {"artifacts": ["a.py"], "tests": [{"name": "t", "passed": True}]}}})
    assert r2.status_code == 200, r2.text


def test_high_tier_confirmation_and_self_confirm(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_COMPLETION_GATE", "block")
    monkeypatch.setenv("MIO_CLAIM_GATE", "warn")
    monkeypatch.setenv("MIO_READ_GATE", "0")
    t = _mk(workspace=str(tmp_path), labels=["migration"])
    run, agent = _claim(t["id"])
    r = _submit(run["id"], {"success": True, "result": "实现了 X 并跑通测试",
                            "evidence": {"verification": {"artifacts": ["a.py"], "tests": [{"name": "t", "passed": True}]}}})
    assert r.status_code == 409
    assert _codes(r) == "gate.human_confirmation_required"
    self_confirm = client.post(f"/api/v1/tasks/{t['id']}/confirm",
                               json={"confirmed_by": agent})
    assert self_confirm.status_code == 409
    assert self_confirm.json()["detail"]["code"] == "gate.self_confirmation"
    ok = client.post(f"/api/v1/tasks/{t['id']}/confirm",
                     json={"confirmed_by": "user", "scope": "high_risk"})
    assert ok.status_code == 200
    r2 = _submit(run["id"], {"success": True, "result": "实现了 X 并跑通测试",
                             "evidence": {"verification": {"artifacts": ["a.py"], "tests": [{"name": "t", "passed": True}]}}})
    assert r2.status_code == 200, r2.text


def test_initial_done_stage_rejected():
    r = client.post("/api/v1/tasks", json={"title": "bad", "stage": "done"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "gate.invalid_initial_stage"


def test_claim_includes_tier_and_readiness(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_CLAIM_GATE", "warn")
    t = _mk(workspace=str(tmp_path), spec_path="docs/spec.md")
    body, _ = _claim(t["id"])
    assert body["gate_tier"] == "standard"
    assert "readiness" in body
    assert body["readiness"]["completion_checks"]
    assert body["readiness"]["ready"] is False  # spec registered, file missing
    detail = client.get(f"/api/v1/tasks/{t['id']}").json()
    assert detail["gate_tier"] == "standard"
    assert detail["completion_state"] == "pending"


def test_tier_inference():
    high = _mk(labels=["migration"])
    core = _mk(files=["mio_taskhub/api/tasks.py"])
    light = _mk()
    assert high["gate_tier"] == "high"
    assert core["gate_tier"] == "standard"
    assert light["gate_tier"] == "light"

def test_done_gate_block_requires_completion_run(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_COMPLETION_GATE", "block")
    t = _mk(workspace=str(tmp_path), stage="review")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage", json={"target_stage": "done"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "gate.completion_missing"


def test_done_gate_block_passes_with_completion_run(monkeypatch, tmp_path):
    monkeypatch.setenv("MIO_COMPLETION_GATE", "block")
    monkeypatch.setenv("MIO_READ_GATE", "0")
    t = _mk(workspace=str(tmp_path))
    run, _ = _claim(t["id"])
    r = _submit(run["id"], {"success": True, "result": "实现了 X 并跑通测试",
                            "evidence": {"verification": {"tests": [{"name": "t", "passed": True}]}}})
    assert r.status_code == 200, r.text
    moved = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                        json={"target_stage": "done"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["stage"] == "done"


def test_legacy_done_is_readable(monkeypatch):
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskStage
    from sqlmodel import Session
    with Session(engine) as db:
        task = Task(title="legacy", stage=TaskStage.DONE)
        db.add(task)
        db.commit()
        db.refresh(task)
        tid = task.id
    detail = client.get(f"/api/v1/tasks/{tid}").json()
    assert detail["completion_state"] == "legacy_done"
    assert detail["completion_run_id"] == ""
