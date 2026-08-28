"""Task template CRUD + from-template task creation tests."""
import pytest
from fastapi.testclient import TestClient
from mio_taskhub.main import app

c = TestClient(app)

def test_list_templates_empty():
    r = c.get("/api/v1/tasks/templates")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_create_and_list_template():
    r = c.post("/api/v1/tasks/templates", json={
        "title": "数据清洗",
        "description": "清洗脏数据",
        "category": "data",
        "priority": 1,
        "est_duration_min": 60,
        "acceptance_criteria": "输出干净 CSV",
        "files_template": ["src/clean.py"],
        "labels": ["data"],
    })
    assert r.status_code == 200
    tpl = r.json()
    assert tpl["title"] == "数据清洗"
    assert tpl["category"] == "data"
    assert tpl["version"] == 1
    tid = tpl["id"]

    r2 = c.get(f"/api/v1/tasks/templates/{tid}")
    assert r2.status_code == 200
    assert r2.json()["files_template"] == ["src/clean.py"]

    r3 = c.get("/api/v1/tasks/templates?category=data")
    assert len(r3.json()) == 1

def test_update_template():
    r = c.post("/api/v1/tasks/templates", json={"title": "t1"})
    tid = r.json()["id"]
    r2 = c.patch(f"/api/v1/tasks/templates/{tid}", json={
        "title": "t1-updated", "priority": 2, "_change_desc": "bump prio",
    })
    assert r2.status_code == 200
    assert r2.json()["title"] == "t1-updated"
    assert r2.json()["priority"] == 2
    assert r2.json()["version"] == 2

def test_delete_template():
    r = c.post("/api/v1/tasks/templates", json={"title": "to-delete"})
    tid = r.json()["id"]
    r2 = c.delete(f"/api/v1/tasks/templates/{tid}")
    assert r2.status_code == 200
    r3 = c.get(f"/api/v1/tasks/templates/{tid}")
    assert r3.status_code == 404

def test_create_task_from_template():
    r = c.post("/api/v1/tasks/templates", json={
        "title": "模板任务",
        "description": "来自模板",
        "est_duration_min": 45,
        "acceptance_criteria": "完成",
        "labels": ["tpl"],
    })
    tid = r.json()["id"]
    r2 = c.post(f"/api/v1/tasks/from-template/{tid}", json={
        "title": "实际任务",
        "project": "test",
    })
    assert r2.status_code == 200
    task = r2.json()
    assert task["title"] == "实际任务"
    assert task["depends_on"] == []

def test_create_template_from_task():
    # 先建任务
    r = c.post("/api/v1/tasks", json={"title": "源任务", "description": "test"})
    task_id = r.json()["id"]
    r2 = c.post(f"/api/v1/tasks/templates/from-task/{task_id}", json={
        "title": "来自任务的模板",
    })
    assert r2.status_code == 200
    assert r2.json()["title"] == "来自任务的模板"
