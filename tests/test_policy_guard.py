# -*- coding: utf-8 -*-
"""#5 policy check：Mio 历史风险 → 危险调用点 409 门控（fail-open）。"""
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from mio_taskhub import mio_runtime
from mio_taskhub.main import app
from mio_taskhub.policy_guard import guard_action

client = TestClient(app)


def _payload(risk="low", hard_gate=False, action="taskhub:delete-task"):
    return {
        "action": action, "project": "agent-dev",
        "total": 5, "failures": 1, "risk": 20, "riskLevel": risk,
        "outcomeCounts": {"success": 4, "failure": 1},
        "failureExamples": [{"error": "boom"}],
        "suggestion": f"Suggestion for {risk}",
        "guidance": {"level": "advisory", "hardGate": hard_gate},
    }


def _patch_run(monkeypatch, payload, ok=True):
    def fake(args, timeout=300.0):
        return {"ok": ok, "code": 0 if ok else 1,
                "stdout": json.dumps(payload) if payload is not None else "",
                "stderr": "" if ok else "cli error"}
    monkeypatch.setattr(mio_runtime, "run_mio", fake)
    monkeypatch.setattr(mio_runtime, "available", lambda: True)


def _patch_policy(monkeypatch, risk="low", hard_gate=False):
    """API 级测试直接换掉 policy_check（不依赖子进程）。"""
    out = {"available": True, "action": "a", "project": "agent-dev",
           "riskLevel": risk, "hardGate": hard_gate, "total": 1,
           "failures": 1 if risk == "high" else 0,
           "suggestion": "sug", "failureExamples": []}
    monkeypatch.setattr(mio_runtime, "policy_check",
                        lambda *a, **k: dict(out))
    return out


# ── mio_runtime.policy_check ────────────────────────────────────────────────

def test_policy_check_parses_risk_level(monkeypatch):
    _patch_run(monkeypatch, _payload("moderate"))
    p = mio_runtime.policy_check("taskhub:delete-task")
    assert p["available"] is True
    assert p["riskLevel"] == "moderate"
    assert p["hardGate"] is False
    assert p["total"] == 5 and p["failures"] == 1
    assert p["suggestion"]


def test_policy_check_hard_gate_flag(monkeypatch):
    _patch_run(monkeypatch, _payload("high", hard_gate=True))
    p = mio_runtime.policy_check("x")
    assert p["riskLevel"] == "high" and p["hardGate"] is True


def test_policy_check_fail_open_on_cli_error(monkeypatch):
    _patch_run(monkeypatch, None, ok=False)
    p = mio_runtime.policy_check("x")
    assert p["available"] is False and p["riskLevel"] == "unknown"
    assert p["hardGate"] is False


def test_policy_check_fail_open_when_home_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_HOME", str(tmp_path / "nope"))
    p = mio_runtime.policy_check("x")
    assert p["available"] is False and p["riskLevel"] == "unknown"


# ── guard_action 门控 ───────────────────────────────────────────────────────

def test_guard_blocks_high_without_confirm(monkeypatch):
    _patch_run(monkeypatch, _payload("high"))
    with pytest.raises(HTTPException) as ei:
        guard_action("taskhub:delete-task", confirm=False)
    assert ei.value.status_code == 409
    assert ei.value.detail["error"] == "policy_risk_high"
    assert ei.value.detail["risk"] == "high"
    assert ei.value.detail["policy"]["riskLevel"] == "high"


def test_guard_allows_high_with_confirm(monkeypatch):
    _patch_run(monkeypatch, _payload("high"))
    p = guard_action("taskhub:delete-task", confirm=True)
    assert p["riskLevel"] == "high"


def test_guard_allows_low_and_unknown(monkeypatch):
    _patch_run(monkeypatch, _payload("low"))
    assert guard_action("a")["riskLevel"] == "low"
    _patch_run(monkeypatch, None, ok=False)   # Mio 挂了 → fail-open
    assert guard_action("a")["riskLevel"] == "unknown"


def test_guard_blocks_moderate_with_hard_gate(monkeypatch):
    _patch_run(monkeypatch, _payload("moderate", hard_gate=True))
    with pytest.raises(HTTPException):
        guard_action("a")
    _patch_run(monkeypatch, _payload("moderate", hard_gate=False))
    assert guard_action("a")["riskLevel"] == "moderate"


# ── API 调用点 ──────────────────────────────────────────────────────────────

def test_delete_task_gated(monkeypatch):
    _patch_policy(monkeypatch, risk="high")
    tid = client.post("/api/v1/tasks", json={"title": "T"}).json()["id"]

    r = client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["error"] == "policy_risk_high" and d["risk"] == "high"

    r2 = client.delete(f"/api/v1/tasks/{tid}?confirm=true")
    assert r2.status_code == 200
    assert r2.json()["state"] == "cancelled"
    assert r2.json()["policy"]["riskLevel"] == "high"


def test_delete_task_low_risk_no_confirm_needed(monkeypatch):
    _patch_policy(monkeypatch, risk="low")
    tid = client.post("/api/v1/tasks", json={"title": "T2"}).json()["id"]
    r = client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 200
    assert r.json()["policy"]["riskLevel"] == "low"


def test_delete_template_gated(monkeypatch):
    _patch_policy(monkeypatch, risk="high")
    tpl = client.post("/api/v1/tasks/templates", json={"title": "to-delete"}).json()
    r = client.delete(f"/api/v1/tasks/templates/{tpl['id']}")
    assert r.status_code == 409
    r2 = client.delete(f"/api/v1/tasks/templates/{tpl['id']}?confirm=true")
    assert r2.status_code == 200 and r2.json()["ok"] is True


def test_update_apply_gated(monkeypatch):
    from mio_taskhub.api import update as update_api

    _patch_policy(monkeypatch, risk="high")

    class Svc:
        called = False

        def apply(self):
            Svc.called = True
            return {"ok": True, "state": "scheduled"}

        def status(self):
            return {}

    monkeypatch.setattr(update_api, "_service_override", Svc())

    r = client.post("/api/v1/update/apply")
    assert r.status_code == 409
    assert Svc.called is False          # 高风险时 apply 不得执行

    r2 = client.post("/api/v1/update/apply?confirm=true")
    assert r2.status_code == 200
    assert Svc.called is True
    assert r2.json()["policy"]["riskLevel"] == "high"
