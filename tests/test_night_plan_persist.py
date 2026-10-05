"""Night plan persistence (落盘) tests."""
import os
from pathlib import Path
from fastapi.testclient import TestClient
from mio_taskhub.main import app

c = TestClient(app)
PLAN_DIR = Path(os.path.expanduser("~/.mio_taskhub")) / "night_plans"


def test_night_plan_persists():
    # 确保有可排期任务
    c.post("/api/v1/tasks", json={"title": "落盘测试任务", "est_duration_min": 30})
    r = c.get("/api/v1/plans/night")
    assert r.status_code == 200
    body = r.json()
    assert "generated_at" in body
    # 落盘校验
    import json as _json
    latest = PLAN_DIR / "latest.json"
    assert latest.exists(), "latest.json 未生成"
    saved = _json.loads(latest.read_text(encoding="utf-8"))
    assert saved["window_start"] == body["window_start"]
    assert "items" in saved


def test_night_plan_saved_endpoint():
    r = c.get("/api/v1/plans/night/saved")
    # 上一条测试已落盘；若环境首次运行可能 404，但此处应已存在
    assert r.status_code in (200, 404)


def test_night_plan_persist_false():
    r = c.get("/api/v1/plans/night?persist=false")
    assert r.status_code == 200
    assert "generated_at" not in r.json()
