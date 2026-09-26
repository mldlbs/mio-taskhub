# -*- coding: utf-8 -*-
"""#2 发酵状态 → taskhub idea 生命周期映射（只读 + 显式推进 + 幂等同步）。"""
from fastapi.testclient import TestClient

from mio_taskhub import mio_runtime as mio_rt
from mio_taskhub.api.mio_runtime import FERMENT_MAP, _action_for, _ferment_items
from mio_taskhub.main import app

client = TestClient(app)


def _hyp(hid, title, status, score=200):
    return {"id": hid, "title": title, "status": status,
            "novelty": 90, "feasibility": 60, "impact": 70, "score": score,
            "idea": "the idea body", "sourceLabels": ["mio"]}


def _patch_cr(monkeypatch, items, available=True):
    monkeypatch.setattr(mio_rt, "creativity",
                        lambda limit=20: {"available": available,
                                          "status": {"hypotheses": len(items)},
                                          "items": items})


def _mk_idea(title="T", labels=None):
    r = client.post("/api/v1/ideas",
                    json={"title": title, "labels": labels or []})
    assert r.status_code == 200
    return r.json()


# ── 映射表 ──────────────────────────────────────────────────────────────────

def test_ferment_map_covers_mio_statuses():
    assert FERMENT_MAP == {"draft": "new", "active": "fermenting",
                           "validated": "formed", "rejected": "cancelled"}


# ── _action_for 单元 ────────────────────────────────────────────────────────

class _I:
    def __init__(self, id="i1"):
        self.id = id


def test_action_adjacent_direct():
    a = _action_for("fermenting", "formed", _I())
    assert a["to"] == "formed" and a["toward"] == "formed"


def test_action_multistep_gives_next_step():
    a = _action_for("new", "formed", _I())          # new → formed 非相邻
    assert a["to"] == "fermenting" and a["toward"] == "formed"


def test_action_rejected_to_cancelled():
    assert _action_for("new", "cancelled", _I())["to"] == "cancelled"


def test_action_none_when_same_or_backward():
    assert _action_for("new", "new", _I()) is None          # draft → new，已是 new
    assert _action_for("formed", "new", _I()) is None       # 回退不可
    assert _action_for("cancelled", "cancelled", _I()) is None


# ── _ferment_items 关联 ────────────────────────────────────────────────────

def test_link_by_label_beats_title():
    class Idea:
        id = "idea-9"
        title = "Same Title"
        status = type("S", (), {"value": "new"})()
        labels = ["mio-hyp:h1"]

    items = _ferment_items([_hyp("h1", "Same Title", "active")], [Idea()])
    assert items[0]["linked"] is True
    assert items[0]["idea"]["id"] == "idea-9"
    assert items[0]["action"]["to"] == "fermenting"


def test_link_by_title_when_no_label():
    class Idea:
        id = "idea-8"
        title = "  Exact Title  "
        status = type("S", (), {"value": "fermenting"})()
        labels = []

    items = _ferment_items([_hyp("h2", "Exact Title", "validated")], [Idea()])
    assert items[0]["linked"] is True
    assert items[0]["action"]["to"] == "formed"


def test_unlinked_has_no_action():
    items = _ferment_items([_hyp("h3", "Nobody", "validated")], [])
    it = items[0]
    assert it["linked"] is False and it["idea"] is None and it["action"] is None
    assert it["suggested_status"] == "formed"


# ── API：GET /mio/ferment ───────────────────────────────────────────────────

def test_ferment_endpoint_maps_and_counts(monkeypatch):
    idea = _mk_idea(title="Act Hyp", labels=["mio-hyp:hX"])
    _patch_cr(monkeypatch, [_hyp("hX", "Act Hyp", "active"),
                            _hyp("hY", "Loose", "draft")])
    r = client.get("/api/v1/mio/ferment")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True and d["mapping"]["active"] == "fermenting"
    assert d["counts"] == {"total": 2, "linked": 1, "pending_actions": 1}
    linked = next(x for x in d["items"] if x["linked"])
    assert linked["idea"]["id"] == idea["id"]
    assert linked["action"]["to"] == "fermenting"


def test_ferment_endpoint_unavailable(monkeypatch):
    _patch_cr(monkeypatch, [], available=False)
    d = client.get("/api/v1/mio/ferment").json()
    assert d["available"] is False and d["items"] == []


# ── API：POST /mio/ferment/{id}/sync（幂等） ────────────────────────────────

def test_sync_creates_linked_idea(monkeypatch):
    _patch_cr(monkeypatch, [_hyp("h10", "Sync Me", "active", score=244)])
    r = client.post("/api/v1/mio/ferment/h10/sync")
    assert r.status_code == 200 and r.json()["created"] is True
    iid = r.json()["idea"]["id"]

    got = client.get(f"/api/v1/ideas/{iid}").json()
    assert got["title"] == "Sync Me"
    assert "mio-hyp:h10" in got["labels"]
    assert "mio-intelligence" in got["labels"]
    assert "h10" in got["description"]        # 来源可追溯

    # 幂等：再同步不重复建
    r2 = client.post("/api/v1/mio/ferment/h10/sync")
    assert r2.json()["created"] is False and r2.json()["already"] is True
    assert r2.json()["idea"]["id"] == iid

    # 同步后 GET /ferment 立即关联，且 active → 可推进 fermenting
    d = client.get("/api/v1/mio/ferment").json()
    assert d["counts"]["linked"] == 1 and d["counts"]["pending_actions"] == 1


def test_sync_unknown_hyp_404(monkeypatch):
    _patch_cr(monkeypatch, [_hyp("h11", "Only", "draft")])
    assert client.post("/api/v1/mio/ferment/nope/sync").status_code == 404


def test_sync_creativity_unavailable_404(monkeypatch):
    _patch_cr(monkeypatch, [], available=False)
    assert client.post("/api/v1/mio/ferment/h11/sync").status_code == 404
