# tests/test_material_panel.py
# -*- coding: utf-8 -*-
"""素材面板（只读聚合端点）：分类口径、指标、可追溯字段。"""
import json

from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub import mio_runtime as mio

client = TestClient(app)

MS_DAY = 86_400_000
NOW = 1_790_000_000_000  # 固定基准，避免与真实时间耦合


def _make_world(tmp_path, monkeypatch):
    """造 observer 素材库 + MIO_HOME creativity store，再 monkeypatch 路径。"""
    base = tmp_path / "observer"
    for sub in ("observations", "insights", "topics"):
        (base / sub).mkdir(parents=True)

    (base / "observations" / "2026-10-08.json").write_text(json.dumps({
        "date": "2026-10-08",
        "observations": [
            {"source": "github-trending", "title": "【GitHub】repoA ⭐"},
            {"source": "github-trending", "title": "【GitHub】repoB ⭐"},
            {"source": "bilibili", "title": "【B站热门】视频甲"},
        ]}, ensure_ascii=False), encoding="utf-8")

    (base / "insights" / "obs_a.json").write_text(json.dumps({
        "id": "obs_a", "topic": "【GitHub】", "generatedAt": "2026-10-08T08:00:00.000Z",
        "sections": [{"title": "发生了什么", "content": "给定信息没有出现 GitHub 字样"}],
        "metadata": {"confidence": 1, "llmCalls": 5, "durationMs": 9000},
    }, ensure_ascii=False), encoding="utf-8")
    (base / "insights" / "obs_b.json").write_text(json.dumps({
        "id": "obs_b", "topic": "Mistral", "generatedAt": "2026-10-08T07:19:48.000Z",
        "sections": [],
        "metadata": {"confidence": 0, "llmCalls": 5, "durationMs": 1648},
    }, ensure_ascii=False), encoding="utf-8")

    (base / "topics" / "2026-10-08.json").write_text(json.dumps({
        "selectedAt": "2026-10-07T16:01:30.000Z",
        "topic": {"topic": "【GitHub】", "popularity": 1}, "fallback": False,
    }, ensure_ascii=False), encoding="utf-8")

    home = tmp_path / "miohome"
    cre = home / "creativity"
    cre.mkdir(parents=True)
    combos = [
        # 生产·带洞察（正常链路的期望形态）
        {"createdAt": NOW, "sources": [
            "template-goal: 每日灵感：挑一个改进",
            "observer-insight:【GitHub】: 主题：【GitHub】"], "description": "假设甲"},
        # 生产·模板自配（P0 修复前的实际形态）
        {"createdAt": NOW - MS_DAY, "sources": [
            "template-goal: 每日灵感：挑一个改进",
            "template-context: 探索主题：每日灵感"], "description": "假设乙"},
        # 测试残留（alpha/beta 假素材）
        {"createdAt": NOW - 2 * MS_DAY, "sources": ["alpha X", "beta Y"], "description": "测试"},
        # 测试残留（S1-S4 假素材）
        {"createdAt": NOW - 3 * MS_DAY, "sources": ["S1", "S2"], "description": "测试2"},
    ]
    (cre / "creativity-combos.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in combos) + "\n", encoding="utf-8")
    hyps = [
        {"createdAt": NOW, "title": "假设甲", "status": "active", "strategy": "explore",
         "sourceLabels": ["template-goal: 每日灵感", "observer-insight:【GitHub】"]},
        {"createdAt": NOW - 2 * MS_DAY, "title": "Alpha 假设", "status": "active",
         "strategy": "explore", "sourceLabels": ["alpha X", "beta Y"]},
    ]
    (cre / "creativity-hypotheses.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in hyps) + "\n", encoding="utf-8")

    monkeypatch.setenv("MIO_HOME", str(home))
    monkeypatch.setattr(mio, "observer_base_dir", lambda: str(base))
    return base, home


def test_material_panel_classification_and_metrics(tmp_path, monkeypatch):
    _make_world(tmp_path, monkeypatch)
    r = client.get("/api/v1/mio/material-panel")
    assert r.status_code == 200
    body = r.json()

    # 洞察指标：1 有效 + 1 LLM 失败（空 section）
    assert body["insight_metrics"]["total"] == 2
    assert body["insight_metrics"]["llm_failed"] == 1
    assert body["insight_metrics"]["valid_ratio"] == 0.5
    failed = next(i for i in body["insights"] if i["id"] == "obs_b")
    assert failed["llm_failed"] is True

    # 配对分类：with-insight / template-only / test 分开统计，测试不计入生产指标
    m = body["creativity"]["metrics"]
    assert m["combos_by_class"] == {"with-insight": 1, "template-only": 1, "test": 2}
    assert m["real_material_pair_ratio"] == 0.5
    assert m["hypotheses_by_class"] == {"with-insight": 1, "test": 1}

    # 原始素材可追溯：来源分布 + 样本标题
    obs = body["observations"][0]
    srcs = {s["source"]: s for s in obs["sources"]}
    assert srcs["github-trending"]["count"] == 2
    assert "【GitHub】repoA ⭐" in srcs["github-trending"]["samples"][0]

    # 洞察带当天来源分布（主题匹配度核对入口）
    ok_insight = next(i for i in body["insights"] if i["id"] == "obs_a")
    assert ok_insight["same_day_sources"].get("bilibili") == 1

    # 选题记录
    assert body["topics"][0]["topic"] == "【GitHub】"

    # 分类口径说明（边界 3：手动/例行不可从 store 区分，须显式告知）
    assert "手动" in body["classification_note"]


def test_material_panel_empty_world(tmp_path, monkeypatch):
    """observer 素材库/MIO_HOME 缺失 → 全空但不报错（fail-open）。"""
    monkeypatch.setenv("MIO_HOME", str(tmp_path / "none"))
    monkeypatch.setattr(mio, "observer_base_dir", lambda: str(tmp_path / "none"))
    r = client.get("/api/v1/mio/material-panel")
    assert r.status_code == 200
    body = r.json()
    assert body["observations"] == [] and body["insights"] == []
    assert body["creativity"]["metrics"]["combos_total"] == 0
    assert body["creativity"]["metrics"]["real_material_pair_ratio"] is None
