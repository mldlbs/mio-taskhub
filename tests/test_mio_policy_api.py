# -*- coding: utf-8 -*-
"""POST /api/v1/mio/policy/check —— Mio 历史风险预检（只读、fail-open）。"""
from fastapi.testclient import TestClient

from mio_taskhub import mio_runtime as mio_rt
from mio_taskhub.main import app

client = TestClient(app)


def test_policy_check_fail_open_when_cli_unavailable(monkeypatch):
    # conftest 把 MIO_HOME 指向不存在目录 → CLI 不可用 → unknown（不阻断）
    r = client.post("/api/v1/mio/policy/check",
                    json={"action": "taskhub:delete-task"})
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is False
    assert d["riskLevel"] == "unknown"
    assert d["action"] == "taskhub:delete-task"
    assert d["hardGate"] is False and d["total"] == 0


def test_policy_check_passthrough_shape(monkeypatch):
    monkeypatch.setattr(
        mio_rt, "policy_check",
        lambda action, project=None, timeout=3.0: {
            "available": True, "action": action, "project": project,
            "riskLevel": "high", "hardGate": True, "total": 11, "failures": 2,
            "suggestion": "确认无误后带 confirm=true 重试",
            "failureExamples": [{"outcome": "failure"}],
        })
    r = client.post("/api/v1/mio/policy/check",
                    json={"action": "taskhub:delete-task", "project": "p1"})
    d = r.json()
    assert d["available"] is True and d["riskLevel"] == "high"
    assert d["hardGate"] is True and d["project"] == "p1"
    assert d["total"] == 11 and d["failures"] == 2


def test_policy_check_requires_action():
    for body in ({}, {"action": "   "}):
        assert client.post("/api/v1/mio/policy/check",
                           json=body).status_code in (400, 422)
