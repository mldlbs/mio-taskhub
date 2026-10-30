# -*- coding: utf-8 -*-
"""子任务删除与依赖缺失错误。"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app

client = TestClient(app)


def _task(title="t"):
    r = client.post("/api/v1/tasks", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_delete_subtask_removes_it():
    tid = _task("with-subtask")
    r = client.post(f"/api/v1/tasks/{tid}/subtasks",
                    json={"title": "step 1", "order": 1})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]

    r = client.delete(f"/api/v1/tasks/{tid}/subtasks/{sid}")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    task = client.get(f"/api/v1/tasks/{tid}").json()
    assert all(s["id"] != sid for s in task["subtasks"])


def test_delete_missing_subtask_returns_404():
    tid = _task("no-subtask")
    r = client.delete(f"/api/v1/tasks/{tid}/subtasks/does-not-exist")
    assert r.status_code == 404


def test_missing_dependency_returns_structured_422():
    r = client.post("/api/v1/tasks", json={
        "title": "forward-ref",
        "depends_on": ["not-created-yet"],
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "dependency.not_found"
    assert detail["refs"] == ["not-created-yet"]
