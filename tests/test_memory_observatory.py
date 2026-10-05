# -*- coding: utf-8 -*-
"""Mio 记忆观测投影测试：对标 mneme 只读观测层（server 投影 + 其测试覆盖面）。

FR-1 只读源与响应结构 / FR-2 日志可见性与环境强制隐藏 /
FR-3 复用热度公式 / FR-4 实体类型与标题 / FR-5 证据统计与坏行容错 /
FR-6 mtime 缓存失效。
"""
import json

import pytest
from fastapi.testclient import TestClient

import mio_taskhub.memory_observatory as obs
from mio_taskhub.main import app

client = TestClient(app)
EP = "/api/v1/memory/observatory/data"


def _write(path, rows):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def _mem(**kw):
    row = {
        "id": "m1",
        "kind": "decision",
        "content": "[opencode] 决定采用方案X以收敛架构",
        "tags": ["架构"],
        "source": "mcp",
        "project": "agent-dev",
    }
    row.update(kw)
    return row


def _default_mem_rows():
    return [
        _mem(),
        _mem(id="m2", kind="note", content="task done line one",
             tags=["task-outcome"], source="cli"),
        _mem(id="m3", kind="context", content="ctx line",
             source="foo-observer", project=None),
        _mem(id="m4", kind="idea", content="idea content here", tags=[]),
    ]


def _default_reuse_rows():
    return [
        {"experienceId": "m1", "reuse": True, "outcomeImproved": True},
        {"experienceId": "m1", "reuse": True},
        {"experienceId": "other", "reuse": True},
        {"reuse": True},
    ]


def _seed(home):
    _write(home / "memory.jsonl", _default_mem_rows())
    _write(home / "experience_reuse.jsonl", _default_reuse_rows())


@pytest.fixture
def mio_home(tmp_path, monkeypatch):
    home = tmp_path / "mio"
    home.mkdir()
    monkeypatch.setenv("MIO_HOME", str(home))
    monkeypatch.delenv("MIO_OBSERVATORY_HIDE_LOGS", raising=False)
    obs._reset_cache()
    yield home
    obs._reset_cache()


def test_only_reads_mio_home(mio_home):
    """FR-1：只读 MIO_HOME/memory.jsonl + experience_reuse.jsonl，历史遗留文件不参与。"""
    r0 = client.get(EP)
    assert r0.status_code == 200
    body0 = r0.json()
    assert body0["entities"] == [] and body0["relations"] == []
    assert body0["meta"]["evidence"] == {
        "memTotal": 0, "memWithEvidence": 0, "reuseRecords": 0,
    }

    _seed(mio_home)
    (mio_home / "hermes.jsonl").write_text(
        json.dumps({"id": "legacy-1"}) + "\n", encoding="utf-8")
    before = (mio_home / "memory.jsonl").read_bytes()

    body = client.get(EP).json()
    names = [e["name"] for e in body["entities"]]
    assert names and all(n.startswith("mio-") for n in names)
    assert not any("legacy" in n for n in names)
    assert set(body) == {"entities", "relations", "meta"}
    assert (mio_home / "memory.jsonl").read_bytes() == before  # 只读，不回写


def test_default_view_hides_logs(mio_home):
    """FR-2：默认视图隐藏 note+task-outcome 日志实体，证据统计仍覆盖全部记忆。"""
    _seed(mio_home)
    body = client.get(EP).json()
    names = [e["name"] for e in body["entities"]]
    assert "mio-m2" not in names
    assert body["meta"]["showLogs"] is False
    assert body["meta"]["evidence"]["memTotal"] == 4


def test_logs_param_expands(mio_home):
    """FR-2：?logs=1 展开日志实体，类型为 log。"""
    _seed(mio_home)
    body = client.get(EP + "?logs=1").json()
    ent = {e["name"]: e for e in body["entities"]}
    assert "mio-m2" in ent and ent["mio-m2"]["type"] == "log"
    assert body["meta"]["showLogs"] is True


def test_env_force_hide_logs(mio_home, monkeypatch):
    """FR-2：MIO_OBSERVATORY_HIDE_LOGS 非空强制隐藏（logs 失效），空串不强制。"""
    _seed(mio_home)
    monkeypatch.setenv("MIO_OBSERVATORY_HIDE_LOGS", "1")
    body = client.get(EP + "?logs=1").json()
    assert "mio-m2" not in [e["name"] for e in body["entities"]]
    assert body["meta"]["showLogs"] is False

    monkeypatch.setenv("MIO_OBSERVATORY_HIDE_LOGS", "")
    body2 = client.get(EP + "?logs=1").json()
    assert "mio-m2" in [e["name"] for e in body2["entities"]]
    assert body2["meta"]["showLogs"] is True


def test_reuse_score_formula():
    """FR-3：公式逐项对齐 mneme computeReuseScore（含下限 0）。"""
    decision = {"kind": "decision", "content": "x", "source": "mcp"}
    assert obs.compute_reuse_score(decision, [], {"reuse": 0, "improved": 0}) == 6
    assert obs.compute_reuse_score(
        {"kind": "decision", "content": "x", "source": "mcp-observer"}, [],
        {"reuse": 0, "improved": 0}) == 3
    assert obs.compute_reuse_score(decision, [], {"reuse": 2, "improved": 1}) == 19
    assert obs.compute_reuse_score(
        {"kind": "note", "content": "x", "source": "mcp"}, ["task-outcome"],
        {"reuse": 0, "improved": 0}) == 1
    assert obs.compute_reuse_score(
        {"kind": "idea", "content": "x", "source": "mcp"}, [],
        {"reuse": 0, "improved": 0}) == 3  # 未知 kind 权重 0
    assert obs.compute_reuse_score(
        {"kind": "note", "content": "x", "source": "x-observer"}, ["task-outcome"],
        {"reuse": 0, "improved": 0}) == 0  # 下限 0
    assert obs.compute_reuse_score(
        {"kind": "note", "content": "必须执行", "source": "mcp"}, [],
        {"reuse": 0, "improved": 0}) == 4  # 决策词 +1


def test_kind_map_clean_title(mio_home):
    """FR-4：kind→类型映射、[agent] 前缀剥离、标题截断与兜底。"""
    rows = _default_mem_rows() + [
        _mem(id="m5", kind="decision", content="长" * 100, tags=[], project=None),
        _mem(id="m6", kind="context", content="", tags=[], project=None),
    ]
    _write(mio_home / "memory.jsonl", rows)
    _write(mio_home / "experience_reuse.jsonl", _default_reuse_rows())

    ent = {e["name"]: e for e in client.get(EP + "?logs=1").json()["entities"]}
    assert ent["mio-m1"]["type"] == "rule"
    assert ent["mio-m4"]["type"] == "note"       # 未知 kind → note
    assert ent["mio-m6"]["type"] == "context"

    m1 = ent["mio-m1"]
    assert m1["obs"][0].startswith("来源 mcp · 项目 agent-dev · decision #架构")
    assert m1["obs"][1] == "决定采用方案X以收敛架构"  # "[opencode] " 前缀已剥离
    assert m1["title"] == "决定采用方案X以收敛架构"
    assert ent["mio-m5"]["title"] == "长" * 60 + "…"
    assert ent["mio-m6"]["title"] == "Mio context"
    assert len(ent["mio-m6"]["obs"]) == 1  # 空内容仅 meta 行


def test_relations_and_ids(mio_home):
    """FR-1/FR-4：顺序编号、来源/项目关系、同名实体去重、无项目无「属于」。"""
    rows = _default_mem_rows() + [_mem()]  # 重复 id=m1
    _write(mio_home / "memory.jsonl", rows)
    _write(mio_home / "experience_reuse.jsonl", _default_reuse_rows())

    body = client.get(EP + "?logs=1").json()
    ents, rels = body["entities"], body["relations"]
    assert [e["id"] for e in ents] == list(range(len(ents)))
    assert sum(1 for e in ents if e["name"] == "mio-m1") == 1
    names = {e["name"]: e["id"] for e in ents}
    assert {"来自", "属于"} <= {r["rel"] for r in rels}
    assert all(0 <= r["source"] < len(ents) and 0 <= r["target"] < len(ents)
               for r in rels)
    m3_id = names["mio-m3"]
    assert not any(r["source"] == m3_id and r["rel"] == "属于" for r in rels)


def test_evidence_stats(mio_home):
    """FR-5：evidence 覆盖统计（memTotal 全量、memWithEvidence 有复用、reuseRecords 行数）。"""
    _seed(mio_home)
    ev = client.get(EP).json()["meta"]["evidence"]
    assert ev == {"memTotal": 4, "memWithEvidence": 1, "reuseRecords": 4}


def test_bad_lines_skipped(mio_home):
    """FR-5：坏行计入 meta.skipped 不中断整体返回。"""
    (mio_home / "memory.jsonl").write_text(
        json.dumps(_mem(id="ok1"), ensure_ascii=False) + "\n{bad json\n"
        + json.dumps(_mem(id="ok2", kind="note"), ensure_ascii=False) + "\n",
        encoding="utf-8")
    (mio_home / "experience_reuse.jsonl").write_text(
        json.dumps({"experienceId": "ok1", "reuse": True}) + "\n{oops\n",
        encoding="utf-8")

    body = client.get(EP).json()
    names = [e["name"] for e in body["entities"]]
    assert "mio-ok1" in names and "mio-ok2" in names
    assert body["meta"]["skipped"] == {"mio": 1, "reuse": 1}


def test_mtime_cache(mio_home, monkeypatch):
    """FR-6：同 key 命中缓存不重读；showLogs/文件变更各自触发重建。"""
    _write(mio_home / "memory.jsonl", [_mem()])
    _write(mio_home / "experience_reuse.jsonl", [])

    calls = {"n": 0}
    orig = obs.read_jsonl

    def counting(path, bucket=None):
        calls["n"] += 1
        return orig(path, bucket)

    monkeypatch.setattr(obs, "read_jsonl", counting)
    obs._reset_cache()

    r1 = obs.build_data_cached(False)
    n1 = calls["n"]
    assert n1 > 0

    r2 = obs.build_data_cached(False)          # 同 key → 缓存命中
    assert calls["n"] == n1
    assert r2 is r1

    obs.build_data_cached(True)                # showLogs 不同 → 重建
    n2 = calls["n"]
    assert n2 > n1

    _write(mio_home / "memory.jsonl",          # 文件变更 → 重建
           [_mem(), _mem(id="m2", kind="note")])
    obs.build_data_cached(True)
    assert calls["n"] > n2


def test_api_endpoint(mio_home, monkeypatch):
    """FR-1：端点响应结构、bool 解析、错误体 {error}。"""
    r = client.get(EP)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert set(body) == {"entities", "relations", "meta"}
    assert {"generatedAt", "showLogs", "skipped", "counts", "evidence"} <= set(body["meta"])
    assert body["meta"]["generatedAt"].endswith("Z")
    assert client.get(EP + "?logs=true").status_code == 200
    assert client.get(EP + "?logs=abc").status_code == 422

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(obs, "build_data_cached", boom)
    r500 = client.get(EP)
    assert r500.status_code == 500
    assert r500.json() == {"error": "boom"}
