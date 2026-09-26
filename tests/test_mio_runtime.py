# tests/test_mio_runtime.py
"""Mio Agent Runtime 低耦合适配：只读 MIO_HOME + CLI 白名单 + digest 定时。"""
import json

import pytest
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub import mio_runtime as mio

client = TestClient(app)


def _home(tmp_path):
    h = tmp_path / "miohome"
    h.mkdir()
    (h / "config.json").write_text(json.dumps({
        "version": 1,
        "home": str(h),
        "createdAt": "2026-08-14T15:14:57.023Z",
        "agents": {"opencode": {"installedAt": "2026-08-22T05:58:39.817Z",
                                "configPath": "x.json"}},
        "llm": {"apiUrl": "https://api.deepseek.com", "apiKey": "sk-SECRET", "model": "m"},
    }, ensure_ascii=False), encoding="utf-8")
    (h / "memory.jsonl").write_text(
        '{"kind":"note","content":"a"}\n'          # 1
        '\n'                                        # 空行
        'not-json\n'                                # 坏行
        '{"kind":"decision","content":"b"}\n',      # 3
        encoding="utf-8")
    (h / "traces.jsonl").write_text(
        "\n".join(json.dumps({"i": i, "event_type": "task_outcome"}) for i in range(5)) + "\n",
        encoding="utf-8")
    return h


# ── 只读文件层 ────────────────────────────────────────────────────────────

def test_status_reads_home_and_sanitizes_config(tmp_path, monkeypatch):
    h = _home(tmp_path)
    monkeypatch.setenv("MIO_HOME", str(h))
    st = mio.status()
    assert st["available"] is True
    # 敏感字段必须剔除
    assert "llm" not in st["config"]
    assert "sk-SECRET" not in json.dumps(st, ensure_ascii=False)
    # agents 保留安全字段
    assert "opencode" in st["config"]["agents"]
    # 有效记录数（容错：空行/坏行不计）
    by = {f["name"]: f for f in st["files"]}
    assert by["memory.jsonl"]["records"] == 2
    assert by["traces.jsonl"]["records"] == 5


def test_unavailable_when_no_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_HOME", str(tmp_path / "nope"))
    st = mio.status()
    assert st["available"] is False
    assert mio.traces()["items"] == []
    assert mio.memory()["items"] == []


def test_tail_jsonl_newest_first_and_tolerant(tmp_path, monkeypatch):
    h = _home(tmp_path)
    monkeypatch.setenv("MIO_HOME", str(h))
    items = mio.traces(limit=3)["items"]
    assert [i["i"] for i in items] == [4, 3, 2]          # 倒序
    mem = mio.memory(limit=10)["items"]
    assert [m["content"] for m in mem] == ["b", "a"]     # 坏行/空行跳过


# ── CLI 白名单 ────────────────────────────────────────────────────────────

def test_run_mio_rejects_non_whitelisted(monkeypatch):
    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])
    res = mio.run_mio(["rm", "-rf", "/"])
    assert res["ok"] is False and "not allowed" in res["stderr"]


def test_run_mio_degrades_when_cli_missing(monkeypatch):
    monkeypatch.setattr(mio, "mio_cli", lambda: None)
    res = mio.run_mio(["digest", "--days", "7"])
    assert res["ok"] is False and "not found" in res["stderr"]


def test_run_mio_invokes_cli(monkeypatch):
    calls = {}

    class R:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return R()

    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(mio.subprocess, "run", fake_run)
    res = mio.run_mio(["digest", "--days", "7", "--write-back"])
    assert res["ok"] is True
    assert calls["cmd"] == ["mio", "digest", "--days", "7", "--write-back"]


# ── digest 定时 ───────────────────────────────────────────────────────────

def test_digest_job_ticks_when_enabled(monkeypatch):
    called = []
    monkeypatch.delenv("MIO_DIGEST_DISABLED", raising=False)
    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(mio, "run_mio", lambda args, **kw: called.append(args) or
                        {"ok": True, "code": 0, "stdout": "", "stderr": ""})
    job = mio.MioDigestJob()
    job.tick()
    assert called and called[0][0] == "digest" and "--write-back" in called[0]


def test_digest_job_disabled_and_missing_cli(monkeypatch):
    called = []
    monkeypatch.setattr(mio, "run_mio", lambda args, **kw: called.append(args))
    monkeypatch.setenv("MIO_DIGEST_DISABLED", "1")
    mio.MioDigestJob().tick()
    assert called == []                       # 关闭 → 不调
    monkeypatch.delenv("MIO_DIGEST_DISABLED")
    monkeypatch.setattr(mio, "mio_cli", lambda: None)
    mio.MioDigestJob().tick()
    assert called == []                       # 无 CLI → 不调


# ── API ──────────────────────────────────────────────────────────────────

def test_mio_api_endpoints(tmp_path, monkeypatch):
    h = _home(tmp_path)
    monkeypatch.setenv("MIO_HOME", str(h))
    s = client.get("/api/v1/mio/status").json()
    assert s["available"] is True and "llm" not in s["config"]
    assert client.get("/api/v1/mio/traces", params={"limit": 2}).json()["items"][0]["i"] == 4
    assert client.get("/api/v1/mio/memory", params={"limit": 1}).json()["items"][0]["content"] == "b"


# ── creativity（只读并入）────────────────────────────────────────────────

def test_whitelist_allows_readonly_creativity_only():
    assert mio._check_allowed(["--json", "creativity", "list", "--limit", "5"]) is None
    assert mio._check_allowed(["--json", "creativity", "status"]) is None
    assert mio._check_allowed(["creativity", "generate"]) is not None      # 烧 LLM → 拒
    assert mio._check_allowed(["creativity", "ferment"]) is not None
    assert mio._check_allowed(["--json", "insight", "list"]) is None
    assert mio._check_allowed(["--json", "insight", "generate"]) is not None
    assert mio._check_allowed(["rm", "-rf", "/"]) is not None
    assert mio._check_allowed([]) is not None


def test_run_mio_allows_json_prefixed_creativity(monkeypatch):
    calls = {}

    class R:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return R()

    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(mio.subprocess, "run", fake_run)
    res = mio.run_mio(["--json", "creativity", "list"])
    assert res["ok"] is True
    assert calls["cmd"] == ["mio", "--json", "creativity", "list"]


def test_creativity_reads_status_and_list(monkeypatch):
    def fake(args, timeout=300.0):
        return {"ok": True, "code": 0,
                "stdout": json.dumps({"hypotheses": 1, "active": 1}) if "status" in args
                else json.dumps([{"id": "h1", "title": "T", "novelty": 93,
                                  "feasibility": 67, "impact": 84, "score": 244}]),
                "stderr": ""}
    monkeypatch.setattr(mio, "run_mio", fake)
    monkeypatch.setattr(mio, "available", lambda: True)
    out = mio.creativity(5)
    assert out["available"] is True
    assert out["status"]["hypotheses"] == 1
    assert out["items"][0]["score"] == 244


def test_creativity_unavailable_without_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_HOME", str(tmp_path / "nope"))
    out = mio.creativity()
    assert out["available"] is False and out["items"] == []


def test_mio_creativity_api(tmp_path, monkeypatch):
    h = _home(tmp_path)
    monkeypatch.setenv("MIO_HOME", str(h))

    def fake(args, timeout=300.0):
        return {"ok": True, "code": 0,
                "stdout": json.dumps([{"id": "h1", "title": "T", "novelty": 90,
                                       "feasibility": 60, "impact": 80, "score": 230}])
                if "list" in args else json.dumps({"hypotheses": 1}),
                "stderr": ""}
    monkeypatch.setattr(mio, "run_mio", fake)
    j = client.get("/api/v1/mio/creativity").json()
    assert j["available"] is True and j["items"][0]["novelty"] == 90


# ── insight（只读并入观测台）─────────────────────────────────────────────

def test_insight_reads_status_and_list(monkeypatch):
    def fake(args, timeout=300.0):
        return {"ok": True, "code": 0,
                "stdout": json.dumps({"total": 3, "unreported": 1, "highValue": 2})
                if "status" in args
                else json.dumps([{"id": "i1", "kind": "problem", "title": "T",
                                  "detector": "recurring-problem", "score": 88}]),
                "stderr": ""}
    monkeypatch.setattr(mio, "run_mio", fake)
    monkeypatch.setattr(mio, "available", lambda: True)
    out = mio.insight(5)
    assert out["available"] is True
    assert out["status"]["total"] == 3
    assert out["items"][0]["detector"] == "recurring-problem"


def test_insight_unavailable_without_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_HOME", str(tmp_path / "nope"))
    out = mio.insight()
    assert out["available"] is False and out["items"] == []


def test_mio_insight_api(tmp_path, monkeypatch):
    h = _home(tmp_path)
    monkeypatch.setenv("MIO_HOME", str(h))

    def fake(args, timeout=300.0):
        return {"ok": True, "code": 0,
                "stdout": json.dumps([{"id": "i1", "kind": "problem", "score": 77}])
                if "list" in args else json.dumps({"total": 1}),
                "stderr": ""}
    monkeypatch.setattr(mio, "run_mio", fake)
    j = client.get("/api/v1/mio/insight").json()
    assert j["available"] is True and j["items"][0]["score"] == 77


# ── MCP 脚本 / Node 可移植解析（替代写死绝对路径）────────────────────────

def test_resolve_mcp_script_env_override(monkeypatch, tmp_path):
    """env 覆盖优先；指向不存在的路径也不静默回退（缺失交给存在性检查暴露）。"""
    real = tmp_path / "custom.js"
    real.write_text("")
    monkeypatch.setenv("MIO_MCP_SCRIPT", str(real))
    assert mio.resolve_mcp_script() == str(real)

    gone = tmp_path / "gone.js"
    monkeypatch.setenv("MIO_MCP_SCRIPT", str(gone))
    assert mio.resolve_mcp_script() == str(gone)


def test_resolve_mcp_script_from_cli_prefix(monkeypatch, tmp_path):
    """从 mio CLI shim 位置推导 npm prefix → node_modules 定位脚本。"""
    monkeypatch.delenv("MIO_MCP_SCRIPT", raising=False)
    prefix = tmp_path / "npmprefix"
    script = prefix.joinpath(*mio._MCP_SCRIPT_SUFFIX)
    script.parent.mkdir(parents=True)
    script.write_text("")
    shim = prefix / "mio.cmd"
    shim.write_text("")
    monkeypatch.setattr(mio, "mio_cli", lambda: [str(shim)])
    assert mio.resolve_mcp_script() == str(script)


def test_resolve_mcp_script_not_found(monkeypatch, tmp_path):
    """三路全空（env/推导/兜底）→ None，不猜路径。"""
    monkeypatch.delenv("MIO_MCP_SCRIPT", raising=False)
    monkeypatch.setattr(mio, "mio_cli", lambda: None)
    monkeypatch.setattr(mio.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(mio, "_MCP_SCRIPT_LEGACY", str(tmp_path / "absent.js"))
    assert mio.resolve_mcp_script() is None


def test_resolve_node_order(monkeypatch, tmp_path):
    """env MIO_NODE → PATH → 便携硬编码兜底 → 全空 None。"""
    monkeypatch.setenv("MIO_NODE", str(tmp_path / "n.exe"))
    assert mio.resolve_node() == str(tmp_path / "n.exe")

    monkeypatch.delenv("MIO_NODE")
    monkeypatch.setattr(mio.shutil, "which",
                        lambda name: "/fake/node" if name == "node" else None)
    assert mio.resolve_node() == "/fake/node"

    monkeypatch.setattr(mio.shutil, "which", lambda *_a, **_k: None)
    legacy = tmp_path / "portable_node.exe"
    legacy.write_text("")
    monkeypatch.setattr(mio, "_NODE_LEGACY", str(legacy))
    assert mio.resolve_node() == str(legacy)

    monkeypatch.setattr(mio, "_NODE_LEGACY", str(tmp_path / "nope.exe"))
    assert mio.resolve_node() is None
