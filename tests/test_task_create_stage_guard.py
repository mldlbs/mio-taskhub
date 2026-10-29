# -*- coding: utf-8 -*-
"""创建入口不得直接进入 review（必须走合法状态转移）。"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app

client = TestClient(app)


def test_create_task_rejects_direct_review():
    r = client.post("/api/v1/tasks", json={"title": "direct-review",
                                           "stage": "review"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "gate.review_requires_transition"


def test_create_task_allows_default_stage():
    r = client.post("/api/v1/tasks", json={"title": "normal"})
    assert r.status_code == 200
    assert r.json().get("id")
