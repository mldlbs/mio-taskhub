# tests/test_idea_generate.py
"""模板生成端点：映射到 creativity.generate（runtime 无 mio.idea.generate 工具）。"""
import json

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
    """raw: 返回 ideas 列表的桩；适配端点现在走 _generate_with_degrade（返回 (ideas, strategy)）。"""
    def _stub(goal, context, recently_seen=None, strategy="", sources_limit=5,
              cross_domain=True, **kwargs):
        out = raw(goal, context, **kwargs) if callable(raw) else raw
        return (out, "")
    monkeypatch.setattr(it, "_generate_with_degrade", _stub)
    return client.post("/api/v1/ideas/templates/generate", json=kw)


def test_generate_maps_creativity_output(monkeypatch):
    captured = {}

    def fake(goal, context, timeout=120.0, recently_seen=None, strategy="",
             sources_limit=5, cross_domain=True):
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
    # 中文门禁必须随 sources 下发（上游 system 为英文、无语言指令）
    assert "简体中文" in captured["goal"] and "简体中文" in captured["context"]


def test_generate_truncates_to_num_ideas(monkeypatch):
    r = _post(monkeypatch, lambda *a, **k: list(HYPS),
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
    def boom(goal, context, timeout=120.0, recently_seen=None, strategy="", sources_limit=5, cross_domain=True):
        from fastapi import HTTPException
        raise HTTPException(502, "creativity generate failed: LLM down")

    r = _post(monkeypatch, boom, template_id="feature-request",
              values={"title": "t"}, sync_to_hub=False)
    assert r.status_code == 502


def test_generate_syncs_to_hub(monkeypatch):
    r = _post(monkeypatch,
              lambda *a, **k: [
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


class _Proc:
    def __init__(self, out):
        self.returncode = 0
        self.stdout = out
        self.stderr = ""


def _patch_cli(monkeypatch, outputs):
    """outputs: list of raw stdout strings consumed one per subprocess.run call."""
    monkeypatch.setattr(it.mio_runtime, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(it, "_fetch_observer_insights", lambda *a, **k: [])
    calls = {"n": 0}

    def fake_run(*a, **k):
        out = outputs[min(calls["n"], len(outputs) - 1)]
        calls["n"] += 1
        return _Proc(out)

    monkeypatch.setattr(it.subprocess, "run", fake_run)
    return calls


def test_generate_empty_retries_then_succeeds(monkeypatch):
    calls = _patch_cli(monkeypatch, ['{"ideas": []}',
                                     '{"ideas": [{"title": "重试成功", "idea": "内容R", "strategy": "explore"}]}'])
    r = client.post("/api/v1/ideas/templates/generate", json={
        "template_id": "feature-request", "values": {"title": "t"},
        "num_ideas": 1, "sync_to_hub": False})
    assert r.status_code == 200, r.text
    assert calls["n"] == 2
    assert r.json()["ideas"][0]["title"] == "重试成功"


def test_generate_empty_with_reason_409_no_retry(monkeypatch):
    """409/all-pairs：不再单次失败即返回，而是按 explore→signal→stable 各试一次后报 409。"""
    calls = _patch_cli(monkeypatch, ['{"ideas": [], "reason": "all pairs already explored"}'])
    r = client.post("/api/v1/ideas/templates/generate", json={
        "template_id": "feature-request", "values": {"title": "t"}, "sync_to_hub": False})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "explore/signal/stable" in detail or "all pairs already explored" in detail
    assert calls["n"] == 3   # explore + signal + stable 三档降级


def test_generate_double_empty_502(monkeypatch):
    calls = _patch_cli(monkeypatch, ['{"ideas": []}'])
    r = client.post("/api/v1/ideas/templates/generate", json={
        "template_id": "feature-request", "values": {"title": "t"}, "sync_to_hub": False})
    assert r.status_code == 502
    assert "连续两次返回空" in r.json()["detail"]
    assert calls["n"] == 2


def test_fetch_insights_parses_and_bounds(monkeypatch):
    monkeypatch.setattr(it.mio_runtime, "mio_cli", lambda: ["mio"])
    payload = (json.dumps([
        {"id": "i1", "topic": "平台", "sections": [
            {"title": "发生了什么", "content": "洞察正文A"}, {"title": "背后结构", "content": "洞察B"}]},
        {"id": "i2", "topic": "T2", "sections": []},
        {"id": "i3", "topic": "T3", "sections": []},
    ], ensure_ascii=False))
    monkeypatch.setattr(it.subprocess, "run", lambda *a, **k: _Proc(payload))
    out = it._fetch_observer_insights(limit=2)
    assert len(out) == 2
    assert out[0]["name"] == "observer-insight:平台"
    assert "主题：平台" in out[0]["content"] and "洞察正文A" in out[0]["content"]


def test_fetch_insights_fail_open(monkeypatch):
    monkeypatch.setattr(it.mio_runtime, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(it.subprocess, "run", lambda *a, **k: _Proc("not-json{{"))
    assert it._fetch_observer_insights() == []
    monkeypatch.setattr(it.mio_runtime, "mio_cli", lambda: None)
    assert it._fetch_observer_insights() == []


def test_generate_injects_observer_sources(monkeypatch):
    captured = {}

    def fake_run(args, **k):
        captured["args"] = args
        return _Proc('{"ideas": [{"title": "带洞察的想法", "idea": "内容", "strategy": "explore"}]}')

    monkeypatch.setattr(it.mio_runtime, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(it, "_fetch_observer_insights",
                        lambda *a, **k: [{"name": "observer-insight:平台", "content": "洞察X"}])
    monkeypatch.setattr(it.subprocess, "run", fake_run)
    r = client.post("/api/v1/ideas/templates/generate", json={
        "template_id": "feature-request", "values": {"title": "t"},
        "num_ideas": 1, "sync_to_hub": False})
    assert r.status_code == 200, r.text
    joined = " ".join(captured["args"])
    assert "observer-insight:平台" in joined and "洞察X" in joined
    assert r.json()["ideas"][0]["title"] == "带洞察的想法"
