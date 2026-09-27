# -*- coding: utf-8 -*-
"""P5（FR-34/FR-36）：驾驶舱草稿生成 + 本地假设 hid 补全。

LLM 一律 monkeypatch（不打外网）。
"""
import asyncio
import json

import httpx
from sqlmodel import Session, select

from mio_taskhub.api import draft as draft_api
from mio_taskhub.api import ideas as ideas_api
from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Idea, IdeaChange


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


def _fake_cfg(configured=True):
    def f():
        if not configured:
            return {"apiUrl": "", "apiKey": "", "model": ""}
        return {"apiUrl": "https://llm.example/chat/completions",
                "apiKey": "sk-test-secret", "model": "test-model"}
    return f


def _fake_llm(status="ok", content=None):
    """替换 draft._call_llm：记录调用入参，返回预设 (status, content)。"""
    calls = []

    def f(cfg, messages, timeout=draft_api.LLM_TIMEOUT):
        calls.append({"cfg": cfg, "messages": messages, "timeout": timeout})
        if status == "ok":
            payload = content if content is not None else json.dumps({
                "goal": "给【值班员】解决【告警靠电话汇总】的问题，因为【窗口只有 2 小时】",
                "success_metric": "【告警→接单时长】从【20 分钟】到【≤5 分钟】，在【3 个月内】",
                "constraints": "时间：6 周；预算：不新增服务器；合规：数据不出内网",
                "out_of_scope": "不做预测模型；不做移动端",
                "mvp_scope": "仅 12 个点位：告警聚合 → 一键分派",
                "tags": ["高风险", "合规", "高风险"],
                "assumptions": [{"text": "值班员愿意点接单"}, {"text": ""}, {"title": "告警延迟 < 30s"}],
                "risks": [{"text": "误报误分派", "level": "HIGH", "mitigation": "首月人工确认"},
                          {"text": "缺缓解措施"}, "字符串风险"],
                "unknown_key": "应被丢弃",
            }, ensure_ascii=False)
            return "ok", payload
        return status, content
    return f, calls


async def _make_idea(c, **kw):
    r = await c.post("/api/v1/ideas", json={"title": kw.pop("title", "草稿想法"), **kw})
    assert r.status_code == 200
    return r.json()


# ---------- FR-34：生成草稿（成功路径 + 零写库） ----------

def test_draft_fields_ok_and_zero_write(monkeypatch):
    """FR-34：200 且 8 字段齐全、规范化正确；调用前后 version/IdeaChange 不变。"""
    monkeypatch.setattr(draft_api.mio_runtime, "llm_config", _fake_cfg())
    fake, calls = _fake_llm()
    monkeypatch.setattr(draft_api, "_call_llm", fake)
    state = {}

    async def k(c):
        base = await _make_idea(c, description="汛期告警靠电话汇总，平均 20 分钟才分派")
        iid = base["id"]
        rid = base["version"]
        with Session(engine) as s:
            n0 = len(s.exec(select(IdeaChange).where(IdeaChange.idea_id == iid)).all())
        r = await c.post(f"/api/v1/ideas/{iid}/draft-fields", json={})
        assert r.status_code == 200
        b = r.json()
        d = b["draft"]
        assert set(d) == set(draft_api.DRAFT_FIELDS)
        assert d["goal"].startswith("给【值班员】")
        assert d["tags"] == ["高风险", "合规"]              # 去重
        assert len(d["assumptions"]) == 2                    # 空 text 被剔除；title 兼容
        assert all(a["hid"].startswith("as-") for a in d["assumptions"])
        assert all(a["status"] == "open" for a in d["assumptions"])
        assert len(d["risks"]) == 3                          # 含裸字符串条目的容错
        assert d["risks"][0]["level"] == "high"              # 大写归一小写
        assert d["risks"][1]["level"] == "medium"            # 缺 level 补 medium
        assert d["risks"][2]["text"] == "字符串风险" and d["risks"][2]["level"] == "medium"
        assert "unknown_key" not in d
        assert b["source"] == "llm" and b["model"] == "test-model"
        assert isinstance(b["elapsed_ms"], int) and b["elapsed_ms"] >= 0
        # 零写库：version 与 IdeaChange 计数不变
        with Session(engine) as s:
            idea = s.get(Idea, iid)
            n1 = len(s.exec(select(IdeaChange).where(IdeaChange.idea_id == iid)).all())
        assert idea.version == rid and n1 == n0
        assert idea.goal == "" and (idea.tags or []) == []   # 草稿未落库
    _with_client(k)
    # prompt 含标题/描述；模型名进请求体；apiKey 只出现在请求头（不进 messages）
    assert "汛期告警靠电话汇总" in calls[0]["messages"][1]["content"]
    assert calls[0]["cfg"]["model"] == "test-model"
    assert all("sk-test-secret" not in m["content"] for m in calls[0]["messages"])


def test_draft_fields_fields_subset(monkeypatch):
    """FR-34：fields 限定子集时只生成/返回这些键。"""
    monkeypatch.setattr(draft_api.mio_runtime, "llm_config", _fake_cfg())
    fake, _ = _fake_llm()
    monkeypatch.setattr(draft_api, "_call_llm", fake)

    async def k(c):
        base = await _make_idea(c)
        r = await c.post(f"/api/v1/ideas/{base['id']}/draft-fields",
                         json={"fields": ["goal", "risks"]})
        assert r.status_code == 200
        assert set(r.json()["draft"]) == {"goal", "risks"}
    _with_client(k)


def test_draft_fields_codeblock_extraction(monkeypatch):
    """FR-34：上游用 ```json 包裹时也能解析。"""
    monkeypatch.setattr(draft_api.mio_runtime, "llm_config", _fake_cfg())
    fake, _ = _fake_llm(content="好的，草稿如下：\n```json\n{\"goal\": \"G\", \"tags\": [\"t\"]}\n```")
    monkeypatch.setattr(draft_api, "_call_llm", fake)

    async def k(c):
        base = await _make_idea(c)
        r = await c.post(f"/api/v1/ideas/{base['id']}/draft-fields",
                         json={"fields": ["goal", "tags"]})
        assert r.status_code == 200
        assert r.json()["draft"] == {"goal": "G", "tags": ["t"]}
    _with_client(k)


# ---------- FR-34：降级路径 ----------

def test_draft_fields_not_configured_503(monkeypatch):
    monkeypatch.setattr(draft_api.mio_runtime, "llm_config", _fake_cfg(configured=False))

    async def k(c):
        base = await _make_idea(c)
        r = await c.post(f"/api/v1/ideas/{base['id']}/draft-fields", json={})
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]
    _with_client(k)


def test_draft_fields_unavailable_503(monkeypatch):
    monkeypatch.setattr(draft_api.mio_runtime, "llm_config", _fake_cfg())
    fake, _ = _fake_llm(status="unavailable")
    monkeypatch.setattr(draft_api, "_call_llm", fake)

    async def k(c):
        base = await _make_idea(c)
        r = await c.post(f"/api/v1/ideas/{base['id']}/draft-fields", json={})
        assert r.status_code == 503
        assert r.json()["detail"] == "llm unavailable"
    _with_client(k)


def test_draft_fields_upstream_http_error_503(monkeypatch):
    """上游 401/500 → 503 llm http <code>（不回显上游 body/key）。"""
    monkeypatch.setattr(draft_api.mio_runtime, "llm_config", _fake_cfg())
    fake, _ = _fake_llm(status="http_401")
    monkeypatch.setattr(draft_api, "_call_llm", fake)

    async def k(c):
        base = await _make_idea(c)
        r = await c.post(f"/api/v1/ideas/{base['id']}/draft-fields", json={})
        assert r.status_code == 503
        assert r.json()["detail"] == "llm http 401"
        assert "sk-test-secret" not in r.text
    _with_client(k)


def test_draft_fields_timeout_504(monkeypatch):
    monkeypatch.setattr(draft_api.mio_runtime, "llm_config", _fake_cfg())
    fake, _ = _fake_llm(status="timeout")
    monkeypatch.setattr(draft_api, "_call_llm", fake)

    async def k(c):
        base = await _make_idea(c)
        r = await c.post(f"/api/v1/ideas/{base['id']}/draft-fields", json={})
        assert r.status_code == 504
        assert r.json()["detail"] == "llm timeout"
    _with_client(k)


def test_draft_fields_invalid_json_502(monkeypatch):
    monkeypatch.setattr(draft_api.mio_runtime, "llm_config", _fake_cfg())
    fake, _ = _fake_llm(content="抱歉，我无法输出 JSON")
    monkeypatch.setattr(draft_api, "_call_llm", fake)

    async def k(c):
        base = await _make_idea(c)
        r = await c.post(f"/api/v1/ideas/{base['id']}/draft-fields", json={})
        assert r.status_code == 502
        assert "invalid json" in r.json()["detail"]
    _with_client(k)


def test_draft_fields_404_and_422(monkeypatch):
    monkeypatch.setattr(draft_api.mio_runtime, "llm_config", _fake_cfg())
    fake, _ = _fake_llm()
    monkeypatch.setattr(draft_api, "_call_llm", fake)

    async def k(c):
        r = await c.post("/api/v1/ideas/nope404/draft-fields", json={})
        assert r.status_code == 404
        base = await _make_idea(c)
        r2 = await c.post(f"/api/v1/ideas/{base['id']}/draft-fields",
                          json={"fields": ["goal", "bogus"]})
        assert r2.status_code == 422
        assert "unknown fields" in r2.json()["detail"]
    _with_client(k)


# ---------- FR-36：本地假设 hid 补全 ----------

def test_assumptions_hid_backfill_and_writeback():
    """FR-36：整表 PATCH 补 hid（既有 hid 保留、请求内不重复），补后可单条回写。"""
    state = {}

    async def k(c):
        base = await _make_idea(c)
        iid = base["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"assumptions": [
            {"text": "值班员愿意点接单"},
            {"text": "告警延迟 < 30s", "hid": "keep-me"},
            {"text": "第三条"},
        ]})
        assert r.status_code == 200
        rows = r.json()["assumptions"]
        hids = [x.get("hid") for x in rows]
        assert all(h for h in hids), hids
        assert rows[1]["hid"] == "keep-me"          # 既有 hid 不被改写
        assert len(set(hids)) == 3                  # 请求内不重复
        assert rows[0]["hid"].startswith("as-") and rows[2]["hid"].startswith("as-")
        state["iid"], state["hid"] = iid, rows[0]["hid"]
        # 用补出的 hid 单条回写（FR-15）：窗口期曾因缺 hid 静默失败
        w = await c.patch(f"/api/v1/ideas/{iid}/assumptions/{rows[0]['hid']}",
                          json={"status": "validated", "note": "回写可用"})
        assert w.status_code == 200
        assert w.json()["version"] == r.json()["version"] + 1
    _with_client(k)
    with Session(engine) as s:
        idea = s.get(Idea, state["iid"])
        row = [x for x in idea.assumptions if x.get("hid") == state["hid"]][0]
        assert row["status"] == "validated" and row["note"] == "回写可用"


def test_assumptions_hid_stable_across_patch():
    """FR-36：同一条目再次整表 PATCH（带回 hid）不再生成新 hid。"""
    async def k(c):
        base = await _make_idea(c)
        iid = base["id"]
        r1 = await c.patch(f"/api/v1/ideas/{iid}",
                           json={"assumptions": [{"text": "A"}]})
        hid = r1.json()["assumptions"][0]["hid"]
        r2 = await c.patch(f"/api/v1/ideas/{iid}",
                           json={"assumptions": [{"hid": hid, "text": "A（改）"}]})
        assert r2.json()["assumptions"][0]["hid"] == hid
    _with_client(k)
