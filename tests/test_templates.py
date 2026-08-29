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

def test_seed_common_templates_idempotent():
    from mio_taskhub.db import init_db
    from sqlmodel import Session, select
    from mio_taskhub.models import TaskTemplate
    from mio_taskhub.seed import seed_common_templates, COMMON_TEMPLATES
    # 当前测试 DB 可能已因其他用例播种过，先清空模板表验证播种逻辑
    with Session(__import__("mio_taskhub.db", fromlist=["engine"]).engine) as s:
        for t in s.exec(select(TaskTemplate)).all():
            s.delete(t)
        s.commit()
    # 第一次播种
    with Session(__import__("mio_taskhub.db", fromlist=["engine"]).engine) as s:
        seed_common_templates(s)
        n1 = len(s.exec(select(TaskTemplate)).all())
    assert n1 == len(COMMON_TEMPLATES)
    # 第二次播种应为幂等（不重复插入）
    with Session(__import__("mio_taskhub.db", fromlist=["engine"]).engine) as s:
        seed_common_templates(s)
        n2 = len(s.exec(select(TaskTemplate)).all())
    assert n2 == n1
    # init_db 不应抛错
    init_db()

def test_template_versions_and_restore():
    r = c.post("/api/v1/tasks/templates", json={"title": "版本测试", "priority": 1})
    tid = r.json()["id"]
    v1 = r.json()["version"]
    # 更新 → 产生 v2
    r2 = c.patch(f"/api/v1/tasks/templates/{tid}", json={"priority": 5})
    assert r2.json()["priority"] == 5
    v2 = r2.json()["version"]
    assert v2 == v1 + 1
    # 列出版本
    r3 = c.get(f"/api/v1/tasks/templates/{tid}/versions")
    assert r3.status_code == 200
    vers = r3.json()
    assert len(vers) >= 2
    # 回滚到 v1
    r4 = c.post(f"/api/v1/tasks/templates/{tid}/restore/{v1}")
    assert r4.status_code == 200
    assert r4.json()["priority"] == 1
    # 回滚后版本号递增
    assert r4.json()["version"] == v2 + 1
    # 版本列表应包含回滚记录
    r5 = c.get(f"/api/v1/tasks/templates/{tid}/versions")
    assert any(vr["description"] == f"restored from v{v1}" for vr in r5.json())
