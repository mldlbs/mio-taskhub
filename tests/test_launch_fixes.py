"""上线前 P1 修复的回归用例（2026-11-07）。

- 路由遮蔽：`/ideas/scores|health|export` 曾被 `GET /ideas/{idea_id}` 吞成 404。
- 输入校验：`?stage=bogus` 抛 500、`?state=bogus` 静默返回全量。
- 文档状态分叉：`set_doc_status` 被质量门拒绝时不得改写文档文件。
- 重复提交：同一 run 并发提交只能落一次 task_result。
"""
import pathlib
import threading

from fastapi.testclient import TestClient

from mio_taskhub.main import app

client = TestClient(app)


def _ready_task(workspace=None) -> str:
    body = {"title": "p1", "stage": "ready"}
    if workspace:
        body["workspace"] = str(workspace)
    r = client.post("/api/v1/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ── 路由遮蔽 ────────────────────────────────────────────────────────────────

def test_ideas_static_routes_not_shadowed():
    for path in ("/api/v1/ideas/scores", "/api/v1/ideas/health", "/api/v1/ideas/export"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])


def test_ideas_scores_shape():
    d = client.get("/api/v1/ideas/scores").json()
    assert "count" in d and "scores" in d


# ── 查询参数校验 ────────────────────────────────────────────────────────────

def test_list_tasks_invalid_stage_returns_400_not_500():
    r = client.get("/api/v1/tasks", params={"stage": "bogus"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task.invalid_stage"


def test_list_tasks_invalid_state_returns_400():
    r = client.get("/api/v1/tasks", params={"state": "bogus"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task.invalid_state"


def test_list_tasks_valid_filters_still_work():
    _ready_task()
    assert client.get("/api/v1/tasks", params={"stage": "ready"}).status_code == 200
    assert client.get("/api/v1/tasks", params={"state": "queued"}).status_code == 200


# ── set_doc_status：门控拒绝时不得改写文件 ──────────────────────────────────

def test_doc_status_rejected_does_not_touch_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tid = _ready_task(ws)
    body = {"path": "spec.md", "content": "# spec\n\n太短，质量分不够。"}
    assert client.put(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"},
                      json=body).status_code == 200
    before = (ws / "spec.md").read_text(encoding="utf-8")

    r = client.post(f"/api/v1/tasks/{tid}/doc/spec/status", json={"state": "approved"})
    assert r.status_code == 422, r.text

    after = (ws / "spec.md").read_text(encoding="utf-8")
    assert after == before, "被质量门拒绝时文档文件不应被改写（状态会与 DB 分叉）"


# ── 重复提交幂等 ────────────────────────────────────────────────────────────

def test_submit_result_concurrent_only_once():
    _ready_task()
    claim = client.post("/api/v1/tasks/claim", params={"agent": "p1-agent"}).json()
    run_id = claim.get("run_id") or claim.get("id")
    assert run_id, claim

    codes = []

    def _submit():
        r = client.post(f"/api/v1/runs/{run_id}/result", params={"agent": "p1-agent"},
                        json={"success": False, "result": "boom", "exit_code": 1})
        codes.append(r.status_code)

    threads = [threading.Thread(target=_submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(codes) == [200, 409], codes


def test_submit_result_sequential_duplicate_409():
    _ready_task()
    claim = client.post("/api/v1/tasks/claim", params={"agent": "p1-agent2"}).json()
    run_id = claim.get("run_id") or claim.get("id")
    first = client.post(f"/api/v1/runs/{run_id}/result", params={"agent": "p1-agent2"},
                        json={"success": False, "result": "x", "exit_code": 1})
    assert first.status_code == 200, first.text
    second = client.post(f"/api/v1/runs/{run_id}/result", params={"agent": "p1-agent2"},
                         json={"success": False, "result": "x", "exit_code": 1})
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "run.result_already_submitted"


# ── 限流：生命周期端点不受全局 IP 配额影响 ──────────────────────────────────

def _rate_limited_app(limit=2):
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from mio_taskhub.middleware import RateLimitMiddleware, _rate_buckets

    _rate_buckets.clear()
    app2 = Starlette(routes=[
        Route("/api/v1/runs/{rid}/heartbeat", lambda r: PlainTextResponse("hb"), methods=["POST"]),
        Route("/api/v1/tasks", lambda r: PlainTextResponse("list"), methods=["GET"]),
    ])
    app2.add_middleware(RateLimitMiddleware, rate_limit=limit)
    return TestClient(app2)


def test_heartbeat_not_rate_limited():
    c = _rate_limited_app(limit=2)
    for _ in range(6):
        assert c.post("/api/v1/runs/abc/heartbeat").status_code == 200


def test_normal_endpoint_still_rate_limited():
    c = _rate_limited_app(limit=2)
    codes = [c.get("/api/v1/tasks").status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and codes[-1] == 429


def test_default_rate_limit_is_not_too_low():
    """默认 120/min 会让两个前端标签页就 429（2026-11-07 线上实测）。"""
    from mio_taskhub.middleware import _DEFAULT_RATE_LIMIT
    assert _DEFAULT_RATE_LIMIT >= 600
