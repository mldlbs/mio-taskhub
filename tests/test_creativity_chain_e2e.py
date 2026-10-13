# tests/test_creativity_chain_e2e.py
# -*- coding: utf-8 -*-
"""P0 端到端回归：真实生产链路（idea_templates → node 直调 mio.js → creativity-engine 配对器）。

设计（2026-10-08，用户验收标准「不只测命令退出码」）：
- LLM 用本地 stub（OpenAI 兼容 /chat/completions），env LLM_API_URL 优先级最高
  （llm-client.js 实证），MIO_HOME 指向临时目录——不烧真实 key、不污染生产判重池。
- 洞察样本走真实 CLI 读取路径：按生产 schema 造 <base>/insights/obs_*.json，
  `_fetch_observer_insights` 经真实 `mio observer insights` 解析。
- 验收点：
  1. source 数量完整到达引擎（3 source → 3 配对全跑，pairsAttempted=3）；
  2. 中文洞察内容原样进入配对 prompt（配对器用 pair 的 name+content 构造 prompt，
     stub 捕获请求体即可断言，绕开「哪个 pair 被选中」的随机性）；
  3. 隔离 store 的 combos.jsonl 出现 observer-insight source → 洞察真实进入配对记录；
  4. LLM 失败显式暴露（errors → HTTP 502 带 "HTTP 402"），不伪装成素材重复/空产出。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.api import idea_templates as it

client = TestClient(app)

INSIGHT_TOPIC = "观测配对测试主题"
INSIGHT_FRAGMENT = "洞见锚点：中文内容与空格必须原样抵达配对器 P0 回归样本"


def _insight_base(tmp_path: Path) -> Path:
    """按生产 insights schema 造一个可被真实 CLI 解析的观测台素材库。"""
    base = tmp_path / "observer"
    ins = base / "insights"
    ins.mkdir(parents=True)
    (ins / "obs_2026-10-08T00-00-00.json").write_text(json.dumps({
        "id": "obs_2026-10-08T00-00-00",
        "topic": INSIGHT_TOPIC,
        "mode": "analytical",
        "generatedAt": "2026-10-08T00:00:00.000Z",
        "sections": [
            {"title": "发生了什么", "content": INSIGHT_FRAGMENT},
        ],
        "metadata": {"wordCount": 20, "confidence": 1},
    }, ensure_ascii=False), encoding="utf-8")
    return base


def _idea_payload():
    return {
        "title": "配对链路回归假设",
        "idea": "前提->机制->结果的完整因果链：真实素材进入配对器后，洞察能被追溯。",
        "expectedBenefit": "配对可追溯", "risk": "无",
        "novelty": 80, "feasibility": 80, "impact": 80, "logic": 80,
    }


class _StubLLM:
    """OpenAI 兼容 stub：捕获请求体、按模式返回成功/402。"""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.prompts = []
        self.bodies = []
        server = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    payload = json.loads(body)
                except Exception:  # noqa: BLE001
                    payload = {}
                server.bodies.append(payload)
                msg = (payload.get("messages") or [{}])[-1].get("content", "")
                server.prompts.append(msg)
                if server.mode == "402":
                    self.send_response(402)
                    self.end_headers()
                    return
                content = json.dumps(_idea_payload(), ensure_ascii=False)
                resp = json.dumps(
                    {"choices": [{"message": {"content": content}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            def log_message(self, *_a):  # 静默
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1/chat/completions"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture()
def stub_llm(tmp_path, monkeypatch):
    """隔离环境：临时 MIO_HOME + stub LLM + 造好的洞察素材库。"""
    s = _StubLLM().start()
    monkeypatch.setenv("MIO_HOME", str(tmp_path / "miohome"))
    (tmp_path / "miohome").mkdir()
    monkeypatch.setenv("LLM_API_URL", s.url)
    monkeypatch.setenv("LLM_KEY", "stub-key")
    monkeypatch.setenv("MIO_OBSERVER_BASE_DIR", str(_insight_base(tmp_path)))
    yield s
    s.stop()


def _real_cli_available() -> bool:
    from mio_taskhub import mio_runtime as mio
    cmd = mio.mio_cli()
    return bool(cmd) and Path(cmd[0]).name.lower().startswith("node")


def test_real_chain_sources_enter_pairer(stub_llm):
    """真实 CLI 链路：3 source → 3 配对全跑；洞察内容进 prompt；combo 落隔离 store。"""
    if not _real_cli_available():
        pytest.skip("mio runtime not installed")
    r = client.post("/api/v1/ideas/templates/generate", json={
        "template_id": "feature-request", "values": {"title": "P0 回归目标"},
        "num_ideas": 3, "sync_to_hub": False, "cross_domain": False,
        "sources_limit": 1, "auto_recently_seen": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["generated"] >= 1

    # 1) source 数量：goal + context + 1 条洞察 = 3 source → 3 配对全部尝试
    #    （endpoint 返回 generated 受 num_ideas 截断，配对数从 prompt 捕获数验证）
    assert len(stub_llm.prompts) == 3

    # 2) 中文洞察内容原样进入配对 prompt（6 段 Concept 文本里必含洞察名与内容）
    joined = "\n".join(stub_llm.prompts)
    assert f"observer-insight:{INSIGHT_TOPIC}" in joined
    assert INSIGHT_FRAGMENT in joined          # 中文+空格无损
    assert "P0 回归目标" in joined             # goal 也原样到达

    # 3) 隔离 store 的 combos.jsonl：洞察 source 真实进入配对记录
    combos_path = (Path(stub_llm.url and __import__("os").environ["MIO_HOME"])
                   / "creativity" / "creativity-combos.jsonl")
    combos = [json.loads(x) for x in combos_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    names = [s for c in combos for s in c.get("sources", [])]
    assert any(n.startswith("observer-insight:") for n in names), combos


def test_real_chain_llm_402_surfaces_not_masked(stub_llm):
    """LLM 402：全部配对失败必须显式 502 并带 402，不得伪装成素材重复/空产出。"""
    if not _real_cli_available():
        pytest.skip("mio runtime not installed")
    stub_llm.mode = "402"
    r = client.post("/api/v1/ideas/templates/generate", json={
        "template_id": "feature-request", "values": {"title": "t"},
        "sync_to_hub": False, "cross_domain": False, "sources_limit": 1,
        "auto_recently_seen": False})
    assert r.status_code == 502
    detail = str(r.json()["detail"])
    assert "LLM 调用失败" in detail
    assert "HTTP 402" in detail
