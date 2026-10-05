# tests/test_mio_contract.py
"""契约冒烟自检：CLI 输出 schema 字段断言 + 告警接线（防 runtime 升级静默漂移）。"""
import pytest
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub import mio_runtime as mio
from mio_taskhub.observability.alerts import init_alert_manager

client = TestClient(app)

GOOD = {
    "status": {"version": 1, "home": "h", "agents": {},
               "serverScriptExists": True},
    "insight_status": {"total": 1, "unreported": 1, "reported": 0},
    "creativity_status": {"hypotheses": 0, "combos": 0, "active": 0},
    "policy_check": {"riskLevel": "low", "total": 0,
                     "guidance": {"hardGate": False}},
}


@pytest.fixture(autouse=True)
def _reset_contract_cache():
    mio._contract_last = None
    yield
    mio._contract_last = None


def _fake_json_stdout(payloads):
    """按参数关键词路由的假 CLI：payloads 缺省回退 GOOD['status']。"""
    def fake(args, timeout=300.0):
        if "insight" in args:
            return True, payloads.get("insight_status", GOOD["insight_status"])
        if "creativity" in args:
            return True, payloads.get("creativity_status", GOOD["creativity_status"])
        if "policy" in args:
            return True, payloads.get("policy_check", GOOD["policy_check"])
        return True, payloads.get("status", GOOD["status"])
    return fake


def _patch_cli(monkeypatch, payloads=None):
    monkeypatch.setattr(mio, "available", lambda: True)
    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(mio, "_json_stdout", _fake_json_stdout(payloads or {}))
    # mcp_script 本地探针确定性化：指向真实存在的文件（mio_runtime 自身）
    monkeypatch.setattr(mio, "resolve_mcp_script", lambda: mio.__file__)
    monkeypatch.setattr(mio, "resolve_node", lambda: mio.__file__)


# ── 探针断言 ─────────────────────────────────────────────────────────────

def test_contract_check_all_probes_ok(monkeypatch):
    _patch_cli(monkeypatch)
    res = mio.contract_check()
    assert res["ok"] is True
    assert res["available"] is True
    assert [p["name"] for p in res["probes"]] == [
        "status", "insight_status", "creativity_status", "policy_check",
        "mcp_script"]
    assert all(p["ok"] for p in res["probes"])
    # 结果已缓存
    assert mio.contract_last()["epoch"] == res["epoch"]


def test_contract_check_detects_schema_drift(monkeypatch):
    """policy 输出丢了 guidance.hardGate → 该探针失败并列出缺失字段。"""
    broken_policy = {"riskLevel": "low", "total": 0}  # 无 guidance
    _patch_cli(monkeypatch, {"policy_check": broken_policy})
    res = mio.contract_check()
    assert res["ok"] is False
    probe = next(p for p in res["probes"] if p["name"] == "policy_check")
    assert probe["ok"] is False
    assert probe["missing"] == ["guidance.hardGate"]
    # 其余探针不受影响
    assert next(p for p in res["probes"] if p["name"] == "status")["ok"] is True


def test_contract_cli_failed_probe_reports_error(monkeypatch):
    """CLI 非零/无输出 → 4 条 CLI 探针失败带 error；MCP 解析不到 → 本地探针失败。"""
    def broken(args, timeout=300.0):
        return False, None
    monkeypatch.setattr(mio, "available", lambda: True)
    monkeypatch.setattr(mio, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(mio, "_json_stdout", broken)
    monkeypatch.setattr(mio, "resolve_mcp_script", lambda: None)
    monkeypatch.setattr(mio, "resolve_node", lambda: None)
    res = mio.contract_check()
    assert res["ok"] is False
    cli_probes = [p for p in res["probes"] if p["name"] != "mcp_script"]
    assert all(p["ok"] is False and "error" in p for p in cli_probes)
    mcp = next(p for p in res["probes"] if p["name"] == "mcp_script")
    assert mcp["ok"] is False
    assert mcp["missing"] == ["script", "node"]


def test_contract_runtime_absent_is_quiet(monkeypatch):
    """runtime 缺失 = 设计上的正常态：available:false，不进告警。"""
    monkeypatch.setattr(mio, "available", lambda: False)
    res = mio.contract_check()
    assert res["ok"] is False
    assert res["available"] is False
    assert res["probes"] == []


def test_contract_mcp_script_missing_drift(monkeypatch, tmp_path):
    """MCP 脚本解析不到（env 指向不存在的路径，不静默回退）→ 探针失败 + 告警。"""
    _patch_cli(monkeypatch)
    monkeypatch.setattr(mio, "resolve_mcp_script",
                        lambda: str(tmp_path / "gone.js"))
    res = mio.contract_check()
    probe = next(p for p in res["probes"] if p["name"] == "mcp_script")
    assert probe["ok"] is False
    assert probe["missing"] == ["script"]
    assert res["ok"] is False

    mgr = init_alert_manager()
    mgr._last_eval = 0
    assert "MioContractDrift" in {a["name"] for a in mgr.get_active()}


# ── 告警接线（AlertManager） ─────────────────────────────────────────────

def test_contract_alert_fires_then_resolves(monkeypatch):
    mgr = init_alert_manager()

    # 1) schema 漂移 → MioContractDrift fire
    broken_policy = {"riskLevel": "low", "total": 0}
    _patch_cli(monkeypatch, {"policy_check": broken_policy})
    mio.contract_check()
    mgr._last_eval = 0
    active = {a["name"] for a in mgr.get_active()}
    assert "MioContractDrift" in active
    drift = next(a for a in mgr.get_all() if a["name"] == "MioContractDrift")
    assert drift["severity"] == "warning"
    assert "policy_check" in drift["message"] and "guidance.hardGate" in drift["message"]

    # 2) 恢复 → 自动 resolve（evaluate 差分逻辑）
    _patch_cli(monkeypatch)
    mio.contract_check()
    mgr._last_eval = 0
    active = {a["name"] for a in mgr.get_active()}
    assert "MioContractDrift" not in active


def test_contract_alert_silent_when_never_checked(monkeypatch):
    """未跑过/结果过期 → 不告警（fail-open）。"""
    mgr = init_alert_manager()
    mio._contract_last = None
    mgr._last_eval = 0
    assert "MioContractDrift" not in {a["name"] for a in mgr.get_active()}

    # 过期（epoch 远古）→ 同样静默
    mio._contract_last = {"ok": False, "available": True, "epoch": 1.0,
                          "interval_s": 3600, "probes": []}
    mgr._last_eval = 0
    assert "MioContractDrift" not in {a["name"] for a in mgr.get_active()}


# ── REST ────────────────────────────────────────────────────────────────

def test_contract_route_cached_and_run(monkeypatch):
    # 初次：未检查过
    r = client.get("/api/v1/mio/contract")
    assert r.status_code == 200
    assert r.json()["ok"] is None

    # ?run=1 立即跑（假 CLI）
    _patch_cli(monkeypatch)
    r = client.get("/api/v1/mio/contract?run=1")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["probes"]) == 5

    # 默认读缓存（不重跑）
    r = client.get("/api/v1/mio/contract")
    assert r.json()["epoch"] == body["epoch"]
