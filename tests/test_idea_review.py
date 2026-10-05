# tests/test_idea_review.py
"""想法评审 API 测试：/ideas/{id}/review 端点"""
from mio_taskhub.db import get_session, init_db
from mio_taskhub.models import Idea, IdeaHistory, IdeaStatus
from sqlmodel import select


def _history(client, idea_id):
    r = client.get(f"/api/v1/ideas/{idea_id}/history")
    return r.json()


def test_review_advances_and_records():
    """review 推进状态 + 写 kind=review 轨迹 + 更新元数据（同一事务）。"""
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    init_db()
    with TestClient(app) as c:
        iid = c.post("/api/v1/ideas", json={"title": "评审推进"}).json()["id"]
        r = c.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "ferment", "reasoning": "描述完整"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "fermenting"
        assert data["review_count"] == 1
        assert data["last_reviewed_at"] is not None
        h = _history(c, iid)
        assert h["count"] == 2  # review + status
        kinds = [x["kind"] for x in h["items"]]
        assert "review" in kinds and "status" in kinds


def test_review_nothing_records_without_transition():
    """recommend=nothing：不推进状态，但写 kind=review 轨迹 + 更新 last_reviewed_at/count。"""
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    init_db()
    with TestClient(app) as c:
        iid = c.post("/api/v1/ideas", json={"title": "nothing"}).json()["id"]
        r = c.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "nothing", "reasoning": "暂不推进"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "new"
        assert data["review_count"] == 1
        assert data["last_reviewed_at"] is not None
        h = _history(c, iid)
        assert h["count"] == 1
        assert h["items"][0]["kind"] == "review"
        assert h["items"][0]["extra"]["recommend"] == "nothing"


def test_review_invalid_recommend_400():
    """非法 recommend（如 cancel）返回 400。"""
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    init_db()
    with TestClient(app) as c:
        iid = c.post("/api/v1/ideas", json={"title": "bad"}).json()["id"]
        r = c.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "cancel"})
        assert r.status_code == 400


def test_review_invalid_target_422_and_unchanged():
    """非法目标状态（如 new 推 ferment 再推 ferment）→ 422，状态与元数据不变。"""
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    init_db()
    with TestClient(app) as c:
        iid = c.post("/api/v1/ideas", json={"title": "bad target"}).json()["id"]
        # 先推到 fermenting
        c.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "ferment"})
        # 再推 ferment（已经是 fermenting）→ 422
        r = c.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "ferment"})
        assert r.status_code == 422
        d = c.get(f"/api/v1/ideas/{iid}").json()
        assert d["status"] == "fermenting"
        assert d["review_count"] == 1
        h = _history(c, iid)
        assert h["count"] == 2  # 只有第一次成功的 review + status


def test_review_atomic_on_failure():
    """数据库异常回滚：抛出异常，事务回滚（状态/new、count=0、history 空）。"""
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    import mio_taskhub.api.ideas as ideas_mod
    import unittest.mock as mock
    init_db()
    with TestClient(app) as c:
        iid = c.post("/api/v1/ideas", json={"title": "atomic"}).json()["id"]
        # monkeypatch IdeaHistory 构造抛错
        with mock.patch.object(ideas_mod, "IdeaHistory", side_effect=Exception("db error")):
            try:
                c.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "ferment"})
            except Exception:
                pass  # TestClient 在线程池中抛出异常而非返回 500
        d = c.get(f"/api/v1/ideas/{iid}").json()
        assert d["status"] == "new"
        assert d["review_count"] == 0
        assert d["last_reviewed_at"] is None
        h = _history(c, iid)
        assert h["count"] == 0


def test_get_idea_does_not_inline_history():
    """GET /ideas/{id} 不内嵌 history（仅通过独立 /history 获取）。"""
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    init_db()
    with TestClient(app) as c:
        iid = c.post("/api/v1/ideas", json={"title": "no inline"}).json()["id"]
        c.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "ferment"})
        d = c.get(f"/api/v1/ideas/{iid}").json()
        assert "history" not in d
        assert "items" not in d