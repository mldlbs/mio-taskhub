# tests/test_idea_generate.py
"""模板生成端点：映射到 creativity.generate（runtime 无 mio.idea.generate 工具）。"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.api import idea_templates as it

client = TestClient(app)

HYPS = [
    {"id": "h1", "title": "想法A", "idea": "内容A", "expectedBenefit": "收益A",
     "risk": "风险A", "novelty": 80, "feasibility": 95, "impact": 70,
     "status": "active", "strategy": "explore"},
    {"id": "h2", "title": "想法B", "idea": "内容B", "novelty": 60,
     "feasibility": 70, "impact": 65, "strategy": "signal"},
]


def _post(monkeypatch, raw, **kw):
    monkeypatch.setattr(it, "_generate_ideas", raw)
    return client.post("/api/v1/ideas/templates/generate", json=kw)


def test_generate_maps_creativity_output(monkeypatch):
    captured = {}

    def fake(goal, context, timeout=120.0):
        captured.update(goal=goal, context=context)
        return list(HYPS)

    r = _post(monkeypatch, fake, template_id="feature-request",
              values={"title": "测试目标", "problem": "痛点描述"},
              num_ideas=2, sync_to_hub=False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["generated"] == 2
    idea0 = body["ideas"][0]
    assert idea0["title"] == "想法A"
    assert "内容A" in idea0["description"]
    assert "预期收益：收益A" in idea0["description"]
    assert "风险：风险A" in idea0["description"]
    assert "N/F/I：80/95/70" in idea0["description"]
    assert idea0["provenance"]["strategy"] == "explore"
    assert idea0["scores"]["novelty"] == 80
    assert "测试目标" in captured["goal"] and "2 个不同方向" in captured["goal"]
    assert "痛点描述" in captured["context"]


def test_generate_truncates_to_num_ideas(monkeypatch):
    r = _post(monkeypatch, lambda g, c, timeout=120.0: list(HYPS),
              template_id="feature-request", values={"title": "t"},
              num_ideas=1, sync_to_hub=False)
    assert r.status_code == 200
    assert len(r.json()["ideas"]) == 1


def test_generate_cli_missing_returns_503(monkeypatch):
    monkeypatch.setattr(it.mio_runtime, "mio_cli", lambda: None)
    r = client.post("/api/v1/ideas/templates/generate", json={
        "template_id": "feature-request",
        "values": {"title": "t"}, "sync_to_hub": False})
    assert r.status_code == 503


def test_generate_cli_failure_returns_502(monkeypatch):
    def boom(goal, context, timeout=120.0):
        from fastapi import HTTPException
        raise HTTPException(502, "creativity generate failed: LLM down")

    r = _post(monkeypatch, boom, template_id="feature-request",
              values={"title": "t"}, sync_to_hub=False)
    assert r.status_code == 502


def test_generate_syncs_to_hub(monkeypatch):
    r = _post(monkeypatch,
              lambda g, c, timeout=120.0: [
                  {"title": "同步用想法X", "idea": "内容X", "strategy": "stable"}],
              template_id="feature-request", values={"title": "t"},
              num_ideas=1, sync_to_hub=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synced"] == 1 and body["generated"] == 1

    iid = body["synced_ids"][0]
    lst = client.get("/api/v1/ideas").json()
    row = next(x for x in lst["ideas"] if x["id"] == iid)
    assert row["title"] == "同步用想法X"
    assert "strategy:stable" in (row.get("labels") or [])
