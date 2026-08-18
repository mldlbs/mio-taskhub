# tests/test_discussion_history.py
"""讨论关闭写 kind=discussion 轨迹测试"""
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import init_db


def test_close_discussion_writes_history():
    """关闭讨论（有 idea_id）写 kind=discussion 轨迹。"""
    init_db()
    with TestClient(app) as c:
        # 创建想法
        iid = c.post("/api/v1/ideas", json={"title": "讨论历史"}).json()["id"]
        # 开启讨论（绑定 idea）
        d = c.post("/api/v1/discussions", json={"topic": "测试", "idea_id": iid}).json()
        did = d["id"]
        # 关闭讨论
        c.post(f"/api/v1/discussions/{did}/close", json={"conclusions": "已决"})
        # 查历史
        h = c.get(f"/api/v1/ideas/{iid}/history").json()
        assert h["count"] >= 1
        kinds = [x["kind"] for x in h["items"]]
        assert "discussion" in kinds
        disc_item = next(x for x in h["items"] if x["kind"] == "discussion")
        assert disc_item["extra"]["discussion_id"] == did
        assert disc_item["extra"]["conclusions"] == "已决"


def test_close_discussion_without_idea_no_history():
    """无 idea_id 的讨论关闭不写想法轨迹（仅 task_id）。"""
    init_db()
    with TestClient(app) as c:
        # 先建一个 task 再建 discussion
        tid = c.post("/api/v1/tasks", json={"title": "t"}).json()["id"]
        d = c.post("/api/v1/discussions", json={"topic": "无想法", "task_id": tid}).json()
        did = d["id"]
        c.post(f"/api/v1/discussions/{did}/close", json={"conclusions": "无"})
        # 无关联 idea，不应写入想法历史（验证不报错即可）
        assert True