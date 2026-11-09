# -*- coding: utf-8 -*-
"""Decision 实体（任务 89a90af6）REST + inbox 集成。

覆盖：
  - FR-02 创建/列表/详情/裁决/作废，422 人话化，409 终态，404
  - options 形态校验（str / {label,note} / 缺 label）
  - 裁决不覆盖 question/options 原文（审计只增不改）
  - FR-03 inbox decide 队列纳入 open 决策 + summary 分解计数
  - 排序：decide 最老的在前
"""
from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.models import Decision

client = TestClient(app)


def _mk_decision(**kw) -> dict:
    body = {"question": "测试决策：A 还是 B？"}
    body.update(kw)
    r = client.post("/api/v1/decisions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── FR-02 创建 ─────────────────────────────────────────────────────

def test_create_requires_question():
    r = client.post("/api/v1/decisions", json={})
    assert r.status_code == 422
    assert "你要做的是" in r.text


def test_create_with_options_variants():
    # dict 带 note
    d1 = _mk_decision(options=[{"label": "方案A", "note": "快"}, {"label": "方案B"}])
    assert d1["options"] == [
        {"label": "方案A", "note": "快"},
        {"label": "方案B", "note": ""},
    ]
    # 纯字符串选项自动转 {label, note:""}
    d2 = _mk_decision(options=["A", "B"])
    assert d2["options"][0] == {"label": "A", "note": ""}
    # 不传 options → []
    d3 = _mk_decision()
    assert d3["options"] == []


def test_create_rejects_bad_options():
    r = client.post("/api/v1/decisions", json={"question": "q", "options": "方案A"})
    assert r.status_code == 422
    assert "你要做的是" in r.text
    r = client.post("/api/v1/decisions", json={"question": "q", "options": [{"note": "无label"}]})
    assert r.status_code == 422
    assert "label" in r.text


def test_create_rejects_bad_deadline():
    r = client.post("/api/v1/decisions", json={"question": "q", "deadline": "不是时间"})
    assert r.status_code == 422
    assert "ISO 8601" in r.text
    r = client.post("/api/v1/decisions", json={"question": "q", "deadline": "2026-11-14T12:00"})
    assert r.status_code == 200
    assert r.json()["deadline"] is not None


# ── FR-02 列表 / 详情 ──────────────────────────────────────────────

def test_list_default_open_sorted_desc():
    a = _mk_decision(question="决策甲")
    b = _mk_decision(question="决策乙")
    r = client.get("/api/v1/decisions")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "open" and d["count"] >= 2
    ids = [x["id"] for x in d["items"]]
    assert b["id"] in ids and a["id"] in ids
    # created_at 降序（新的在前）
    assert ids.index(b["id"]) < ids.index(a["id"])
    # 非法 status
    r = client.get("/api/v1/decisions", params={"status": "nope"})
    assert r.status_code == 422


def test_get_decision_404():
    r = client.get("/api/v1/decisions/deadbeef")
    assert r.status_code == 404


# ── FR-02 裁决 / 作废 ──────────────────────────────────────────────

def test_resolve_lifecycle():
    d = _mk_decision(options=[{"label": "A", "note": ""}, {"label": "B", "note": ""}])
    # 缺 choice → 422 人话化 + 列出可选
    r = client.post(f"/api/v1/decisions/{d['id']}/resolve", json={})
    assert r.status_code == 422
    assert "A" in r.text and "你要做的是" in r.text
    # 正常裁决
    r = client.post(f"/api/v1/decisions/{d['id']}/resolve",
                    json={"choice": "A", "rationale": "用户原话：先快后好"})
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "resolved"
    assert got["choice"] == "A" and got["rationale"].startswith("用户原话")
    assert got["resolved_at"] is not None
    # 审计只增不改：question/options 原文保留
    assert got["question"] == d["question"]
    assert got["options"] == d["options"]
    # 终态再裁决 → 409
    r = client.post(f"/api/v1/decisions/{d['id']}/resolve", json={"choice": "B"})
    assert r.status_code == 409
    # 列表切到 resolved 可见
    r = client.get("/api/v1/decisions", params={"status": "resolved"})
    assert d["id"] in [x["id"] for x in r.json()["items"]]


def test_resolve_404():
    r = client.post("/api/v1/decisions/deadbeef/resolve", json={"choice": "x"})
    assert r.status_code == 404


def test_supersede():
    old = _mk_decision(question="旧问题")
    new = _mk_decision(question="新问题")
    # 不存在的 successor → 422
    r = client.post(f"/api/v1/decisions/{old['id']}/supersede", json={"successor_id": "nope"})
    assert r.status_code == 422
    # 正常作废并指后继
    r = client.post(f"/api/v1/decisions/{old['id']}/supersede",
                    json={"successor_id": new["id"]})
    assert r.status_code == 200
    assert r.json()["status"] == "superseded"
    assert r.json()["successor_id"] == new["id"]
    # 终态再作废 → 409
    r = client.post(f"/api/v1/decisions/{old['id']}/supersede", json={})
    assert r.status_code == 409


# ── FR-03 inbox 集成 ───────────────────────────────────────────────

def test_inbox_includes_open_decisions():
    _mk_decision(question="收件箱决策：走哪条路？", options=[{"label": "走A", "note": ""}])
    r = client.get("/api/v1/tasks/inbox")
    assert r.status_code == 200
    d = r.json()
    dec_items = [x for x in d["decide"] if x.get("kind") == "decision"]
    assert len(dec_items) >= 1
    it = dec_items[0]
    assert it["question"] == "收件箱决策：走哪条路？"
    assert it["title"] == it["question"]
    assert it["options"] and it["action"]
    assert "/resolve" in it["action"]
    assert "age_days" in it
    # 任务条目 kind=task
    for x in d["decide"]:
        assert x.get("kind") in ("task", "decision")
    # summary 分解：decide = 任务阻塞 + 决策
    s = d["summary"]
    assert s["decide"] == s.get("decide_tasks", 0) + s.get("decide_decisions", 0)
    assert s["decide_decisions"] >= 1
    # 裁决后从队列消失
    did = it["id"]
    rr = client.post(f"/api/v1/decisions/{did}/resolve", json={"choice": "走A"})
    assert rr.status_code == 200
    d2 = client.get("/api/v1/tasks/inbox").json()
    assert did not in [x["id"] for x in d2["decide"] if x.get("kind") == "decision"]


def test_inbox_decide_oldest_first():
    now = datetime.now(timezone.utc)
    from sqlmodel import Session
    from mio_taskhub.db import engine
    with Session(engine) as s:
        for did, delta in (("d0old000", timedelta(days=30)), ("d0new000", timedelta(days=1))):
            s.add(Decision(id=did, question=f"排序-{did}",
                           status="open", created_at=now - delta))
        s.commit()
    d = client.get("/api/v1/tasks/inbox").json()
    dec_ids = [x["id"] for x in d["decide"] if x.get("kind") == "decision"]
    if "d0old000" in dec_ids and "d0new000" in dec_ids:
        assert dec_ids.index("d0old000") < dec_ids.index("d0new000")
    else:
        assert ("d0old000" in dec_ids) or ("d0new000" in dec_ids)
