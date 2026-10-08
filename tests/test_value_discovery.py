# tests/test_value_discovery.py
# -*- coding: utf-8 -*-
"""价值发现实验端点：素材装载/清洗、LLM 失败显式暴露、允许 0 机会、存档与统计。"""
import json

from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub import mio_runtime as mio

client = TestClient(app)


def _world(tmp_path, monkeypatch):
    base = tmp_path / "observer"
    (base / "observations").mkdir(parents=True)
    (base / "observations" / "2026-10-08.json").write_text(json.dumps({
        "date": "2026-10-08",
        "observations": [
            {"source": "github-trending",
             "content": "【GitHub】login?return_to=%2Ffoo%2Fbar ⭐<span class=\"x\"> — Reverse engineer anything with agents"},
            {"source": "github-trending", "content": "【GitHub】? — A native graphical debugger"},
            {"source": "hackernews", "content": "Show HN: Bigwords.page – Turn any screen into a sign"},
            {"source": "bilibili", "content": "【B站热门】娱乐噪声不应出现"},
        ]}, ensure_ascii=False), encoding="utf-8")
    home = tmp_path / "miohome"
    home.mkdir()
    monkeypatch.setenv("MIO_HOME", str(home))
    monkeypatch.setattr(mio, "observer_base_dir", lambda: str(base))
    return home


ISSUES = ["observer 选题池被标题前缀 token 污染", "mio.cmd 吞中文参数（已修 node 直调）"]

OK_LLM = json.dumps({
    "opportunities": [{
        "title": "标题清洗预处理",
        "origin": "internal-led",
        "evidence": ["素材 1: 【GitHub】? — Reverse engineer..."],
        "what": "抓取层未做归一化",
        "why_important": "输入污染不可逆", "internal_link": "选题池被前缀 token 污染",
        "why_now": "样本在手", "one_week_experiment": "normalize_title() 回归集，判据=前缀 token 归零",
    }], "verdict": "素材多为噪声，两条内部问题有证据链"})


def _patch_llm(monkeypatch, payload=None, error=None, empty=False):
    import urllib.error
    calls = {}

    def fake_urlopen(req, timeout):
        calls["body"] = json.loads(req.data.decode("utf-8"))
        calls["url"] = req.full_url
        class R:
            def __init__(self, content):
                self._c = content
            def read(self):
                return json.dumps({"choices": [{"message": {"content": self._c}}]}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        if error:
            raise urllib.error.HTTPError(req.full_url, 503, "svc down", {}, None)
        if empty:
            return R("")
        return R(json.dumps(payload) if isinstance(payload, dict) else payload)

    monkeypatch.setattr("mio_taskhub.api.value_discovery.urllib.request.urlopen", fake_urlopen)
    return calls


def test_run_success_filters_sources_and_archives(tmp_path, monkeypatch):
    home = _world(tmp_path, monkeypatch)
    calls = _patch_llm(monkeypatch, payload=OK_LLM)
    monkeypatch.setattr(mio, "llm_config",
                        lambda: {"apiUrl": "http://stub/v1/chat/completions",
                                 "apiKey": "k", "model": "m"})
    r = client.post("/api/v1/mio/value-discovery", json={
        "date": "2026-10-08", "internal_issues": ISSUES, "label": "首轮"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["opportunities"]) == 1
    assert body["opportunities"][0]["origin"] == "internal-led"
    # 素材：bilibili 噪声必须被排除，HTML 已清洗
    prompt = calls["body"]["messages"][1]["content"]
    assert "Reverse engineer anything" in prompt
    assert "娱乐噪声" not in prompt
    assert "login?return_to" not in prompt
    # 存档落盘
    runs = json.loads((home / "value-discovery" / "runs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert runs["id"].startswith("vd_")


def test_run_zero_opportunities_is_valid(tmp_path, monkeypatch):
    """反凑数：LLM 返回空机会列表是合法结果，不是错误。"""
    _world(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, payload={"opportunities": [], "verdict": "今日素材无值得行动的机会"})
    monkeypatch.setattr(mio, "llm_config",
                        lambda: {"apiUrl": "http://stub/x", "apiKey": "k", "model": "m"})
    r = client.post("/api/v1/mio/value-discovery", json={
        "date": "2026-10-08", "internal_issues": ISSUES})
    assert r.status_code == 200
    assert r.json()["opportunities"] == []
    assert "无值得行动" in r.json()["verdict"]


def test_run_llm_error_surfaces(tmp_path, monkeypatch):
    """LLM 503/空 content → 显式 502，不伪装成空结果。"""
    _world(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, error=True)
    monkeypatch.setattr(mio, "llm_config",
                        lambda: {"apiUrl": "http://stub/x", "apiKey": "k", "model": "m"})
    r = client.post("/api/v1/mio/value-discovery", json={
        "date": "2026-10-08", "internal_issues": ISSUES})
    assert r.status_code == 502 and "503" in str(r.json()["detail"])

    _patch_llm(monkeypatch, empty=True)
    r = client.post("/api/v1/mio/value-discovery", json={
        "date": "2026-10-08", "internal_issues": ISSUES})
    assert r.status_code == 502 and "空 content" in str(r.json()["detail"])


def test_run_missing_date_404(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, payload=OK_LLM)
    r = client.post("/api/v1/mio/value-discovery", json={
        "date": "2099-01-01", "internal_issues": ISSUES})
    assert r.status_code == 404


def test_stats_aggregation(tmp_path, monkeypatch):
    home = _world(tmp_path, monkeypatch)
    monkeypatch.setattr(mio, "observer_base_dir", lambda: str(tmp_path / "observer"))
    vd = home / "value-discovery"
    vd.mkdir(parents=True)
    rows = [
        {"id": "vd_1", "createdAt": "2026-10-08T01:00:00+00:00",
         "opportunities": [{"origin": "internal-led", "evidence": ["x"], "internal_link": "a",
                            "one_week_experiment": "b"}]},
        {"id": "vd_2", "createdAt": "2026-10-08T02:00:00+00:00",
         "opportunities": [{"origin": "external", "evidence": ["y"], "internal_link": "",
                            "one_week_experiment": ""}], "verdict": "v"},
    ]
    (vd / "runs.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    j = client.get("/api/v1/mio/value-discovery").json()
    s = j["stats"]
    assert s["batches"] == 2 and s["opportunities_total"] == 2
    assert s["opportunity_rate"] == 1.0
    assert s["evidence_coverage"] == 1.0
    assert s["internal_relevance"] == 0.5
    assert s["actionability"] == 0.5
    assert s["external_origin_rate"] == 0.5
    assert j["runs"][0]["id"] == "vd_2"          # 新的在前
