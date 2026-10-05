# tests/test_review_mode.py
"""想法落地闭环 P2 包 C：讨论双模式 / 门控 / 幂等转任务 / prompt 快照 / 词表迁 DB。

FR-18 双模式 · FR-19 prompt DB+快照 · FR-20 词表优先级 · FR-21 五段门控 · FR-22 convert 幂等
"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.db import init_db


def _full_review(**over):
    review = {
        "risks": ["权限模型未定"],
        "divergences": "无分歧，已就 MVP 范围达成一致",
        "suggestions": "先做只读视图",
        "decisions": ["方案A：先只读", "方案B：直接上写"],
        "action_items": [
            {"id": "ai_1", "owner": "张三", "action": "出权限矩阵", "due": "2026-10-01",
             "status": "pending", "task_id": None},
            {"id": "ai_2", "owner": "李四", "action": "补迁移脚本", "due": "2026-10-03",
             "status": "pending", "task_id": None},
        ],
    }
    review.update(over)
    return review


def _mk_idea(c, tags=None):
    body = {"title": "评审模式测试想法"}
    if tags is not None:
        body["tags"] = tags
    return c.post("/api/v1/ideas", json=body).json()["id"]


def _mk_review(c, iid, roles=None):
    r = c.post("/api/v1/discussions", json={
        "topic": "结构化评审", "idea_id": iid, "mode": "review",
        "roles": roles or ["产品", "技术", "红队"],
    })
    assert r.status_code == 200, r.text
    return r.json()


# ---------- FR-18 双模式 ----------

def test_free_mode_defaults_and_keys():
    """不传 mode 等价 free；新增字段有默认值且既有键不变（回归）。"""
    init_db()
    with TestClient(app) as c:
        iid = _mk_idea(c)
        r = c.post("/api/v1/discussions", json={"topic": "闲聊", "idea_id": iid})
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] == "free"
        assert d["roles"] == []
        assert d["review"] is None
        assert d["prompt_snapshot"] is None
        # 既有键不回退
        for k in ("id", "topic", "status", "stage", "messages", "conclusions"):
            assert k in d


def test_review_requires_roles():
    """FR-18：mode=review 缺 roles 422；非法 mode 422；roles 非字符串列表 422。"""
    init_db()
    with TestClient(app) as c:
        iid = _mk_idea(c)
        assert c.post("/api/v1/discussions",
                      json={"topic": "x", "idea_id": iid, "mode": "review"}).status_code == 422
        assert c.post("/api/v1/discussions",
                      json={"topic": "x", "idea_id": iid, "mode": "review", "roles": []}).status_code == 422
        assert c.post("/api/v1/discussions",
                      json={"topic": "x", "idea_id": iid, "mode": "chat"}).status_code == 422
        assert c.post("/api/v1/discussions",
                      json={"topic": "x", "idea_id": iid, "mode": "review",
                            "roles": ["ok", 1]}).status_code == 422
        # review + roles 成功
        d = _mk_review(c, iid)
        assert d["mode"] == "review"
        assert d["roles"] == ["产品", "技术", "红队"]


# ---------- FR-19 prompt 快照与热更新 ----------

def test_review_snapshot_and_hot_update_isolation():
    """创建时快照 roles+version；配置热更新影响新评审、不影响进行中会话。"""
    init_db()
    with TestClient(app) as c:
        iid = _mk_idea(c)
        d1 = _mk_review(c, iid, roles=["红队"])
        snap1 = d1["prompt_snapshot"]
        assert snap1 and snap1["roles"] == ["红队"]
        v1 = snap1["prompts"]["红队"]["version"]
        p1 = snap1["prompts"]["红队"]["prompt"]
        assert v1 == 1 and p1

        # 热更新红队 prompt
        r = c.put("/api/v1/config/role-prompts", json={"prompts": {"红队": "新红队提示词 v2"}})
        assert r.status_code == 200
        assert r.json()["prompts"]["红队"]["version"] == 2

        # 进行中评审仍用创建时快照
        d1_again = c.get(f"/api/v1/discussions/{d1['id']}").json()
        assert d1_again["prompt_snapshot"]["prompts"]["红队"]["version"] == v1
        assert d1_again["prompt_snapshot"]["prompts"]["红队"]["prompt"] == p1

        # 新评审用新版
        d2 = _mk_review(c, iid, roles=["红队"])
        assert d2["prompt_snapshot"]["prompts"]["红队"]["version"] == 2
        assert d2["prompt_snapshot"]["prompts"]["红队"]["prompt"] == "新红队提示词 v2"


def test_role_prompts_config_seeds_and_validation():
    """FR-19：种子 5 角色；空 prompt 422。"""
    init_db()
    with TestClient(app) as c:
        g = c.get("/api/v1/config/role-prompts").json()
        assert set(g["roles"]) == {"产品", "技术", "商业", "合规", "红队"}
        assert c.put("/api/v1/config/role-prompts",
                     json={"prompts": {}}).status_code == 422
        assert c.put("/api/v1/config/role-prompts",
                     json={"prompts": {"产品": "  "}}).status_code == 422


# ---------- FR-20 词表迁 DB ----------

def test_risk_vocab_default_db_env_priority():
    """优先级 env > DB > 默认；cockpit high_risk 契约不回退。"""
    init_db()
    with TestClient(app) as c:
        # 默认：未配置 DB → 来源 default，含 4 默认词
        g = c.get("/api/v1/config/role-prompts").json()
        assert g["risk_vocab_source"] == "default"
        assert set(g["risk_vocab"]) >= {"高风险", "合规", "用户数据", "花钱"}

        # 写 DB 词表 → 来源 db，判定随之变化
        r = c.put("/api/v1/config/risk-vocab", json={"words": ["A类风险", "B类风险"]})
        assert r.status_code == 200
        g = c.get("/api/v1/config/role-prompts").json()
        assert g["risk_vocab_source"] == "db"
        assert g["risk_vocab"] == ["A类风险", "B类风险"]

        iid = _mk_idea(c, tags=["A类风险"])
        ck = c.get(f"/api/v1/ideas/{iid}/cockpit").json()
        assert ck["high_risk"] is True
        iid2 = _mk_idea(c, tags=["高风险"])  # 默认词已被 DB 词表替换
        ck2 = c.get(f"/api/v1/ideas/{iid2}/cockpit").json()
        assert ck2["high_risk"] is False

        # env 覆盖优先于 DB
        import os
        os.environ["MIO_IDEA_RISK_TAGS"] = "X风险"
        try:
            g = c.get("/api/v1/config/role-prompts").json()
            assert g["risk_vocab_source"] == "env"
            assert g["risk_vocab"] == ["X风险"]
            ck3 = c.get(f"/api/v1/ideas/{iid2}/cockpit").json()
            assert ck3["high_risk"] is False   # 高风险/DB 词 A类风险 均不在 env 词表
            iid3 = _mk_idea(c, tags=["X风险"])
            ck4 = c.get(f"/api/v1/ideas/{iid3}/cockpit").json()
            assert ck4["high_risk"] is True    # env 词命中
        finally:
            os.environ.pop("MIO_IDEA_RISK_TAGS", None)

        # 非法写入 422
        assert c.put("/api/v1/config/risk-vocab",
                     json={"words": ["", "ok"]}).status_code == 422
        assert c.put("/api/v1/config/risk-vocab", json={"words": "not-list"}).status_code == 422


# ---------- FR-21 五段门控 ----------

def test_close_review_gate_each_missing_segment():
    """五种缺段分别 422 且 detail 指明段名；free close 不受门控（回归在 test_ideas_api）。"""
    init_db()
    with TestClient(app) as c:
        iid = _mk_idea(c)
        cases = [
            ({**_full_review(), "risks": []}, "risks"),
            ({**_full_review(), "divergences": ""}, "divergences"),
            ({**_full_review(), "suggestions": "   "}, "suggestions"),
            ({**_full_review(), "decisions": ["只有一个"]}, "decisions"),
            ({**_full_review(), "action_items": []}, "action_items"),
        ]
        for review, seg in cases:
            d = _mk_review(c, iid)
            r = c.post(f"/api/v1/discussions/{d['id']}/close",
                       json={"conclusions": "完", "review": review})
            assert r.status_code == 422, (seg, r.text)
            assert seg in r.json()["detail"]
            # 门控拒绝时讨论不被关闭
            assert c.get(f"/api/v1/discussions/{d['id']}").json()["status"] == "open"

        # 结构化条目缺字段 → 422
        d = _mk_review(c, iid)
        bad = _full_review()
        bad["action_items"] = [{"id": "ai_1", "owner": "张三", "action": "出矩阵"}]  # 缺 due/status/task_id
        r = c.post(f"/api/v1/discussions/{d['id']}/close",
                   json={"conclusions": "完", "review": bad})
        assert r.status_code == 422
        assert "action_items[0]" in r.json()["detail"]

        # mode=review 关闭不带 review payload → 422
        d = _mk_review(c, iid)
        r = c.post(f"/api/v1/discussions/{d['id']}/close", json={"conclusions": "完"})
        assert r.status_code == 422


def test_close_review_success_and_free_close_unchanged():
    """达标落库可回读；mode=free close 行为不变。"""
    init_db()
    with TestClient(app) as c:
        iid = _mk_idea(c)
        d = _mk_review(c, iid)
        r = c.post(f"/api/v1/discussions/{d['id']}/close",
                   json={"conclusions": "评审完成", "summary": "一轮", "review": _full_review()})
        assert r.status_code == 200, r.text
        closed = r.json()
        assert closed["status"] == "closed"
        assert closed["review"]["risks"] == ["权限模型未定"]
        assert len(closed["review"]["action_items"]) == 2
        assert all(it["task_id"] is None for it in closed["review"]["action_items"])

        # free 回归：无 review 也可关
        fd = c.post("/api/v1/discussions", json={"topic": "自由", "idea_id": iid}).json()
        fr = c.post(f"/api/v1/discussions/{fd['id']}/close", json={"conclusions": "聊完"})
        assert fr.status_code == 200
        assert fr.json()["status"] == "closed"
        assert fr.json()["review"] is None


# ---------- FR-22 convert 幂等 ----------

def test_convert_action_items_idempotent():
    """首次转换创建任务（含验收标准、stage=ready）；重复转换不重复创建。"""
    init_db()
    with TestClient(app) as c:
        iid = _mk_idea(c)
        d = _mk_review(c, iid)
        c.post(f"/api/v1/discussions/{d['id']}/close",
               json={"conclusions": "完", "review": _full_review()})

        r = c.post(f"/api/v1/discussions/{d['id']}/convert", json={})
        assert r.status_code == 200, r.text
        results = r.json()["results"]
        assert len(results) == 2
        assert all(x["created"] for x in results)
        tid = results[0]["task_id"]
        t = c.get(f"/api/v1/tasks/{tid}").json()
        assert t["acceptance_criteria"] == "出权限矩阵"
        assert t["stage"] == "ready"
        assert t["idea_id"] == iid

        # 第二次（缺省全部）：全部幂等跳过，不新建
        r2 = c.post(f"/api/v1/discussions/{d['id']}/convert", json={})
        assert r2.status_code == 200
        assert all(x["created"] is False for x in r2.json()["results"])
        tasks = c.get(f"/api/v1/ideas/{iid}").json()["tasks"]
        assert len(tasks) == 2

        # task_id 已回写进 review
        got = c.get(f"/api/v1/discussions/{d['id']}").json()
        assert [it["task_id"] for it in got["review"]["action_items"]] == \
               [x["task_id"] for x in results]


def test_convert_selection_and_errors():
    """item_ids 精选、未知 id 422、无行动项 422（free 讨论）。"""
    init_db()
    with TestClient(app) as c:
        iid = _mk_idea(c)
        d = _mk_review(c, iid)
        c.post(f"/api/v1/discussions/{d['id']}/close",
               json={"conclusions": "完", "review": _full_review()})

        # 精选 1 条
        r = c.post(f"/api/v1/discussions/{d['id']}/convert", json={"item_ids": ["ai_2"]})
        assert r.status_code == 200
        assert len(r.json()["results"]) == 1
        assert r.json()["results"][0]["item_id"] == "ai_2"

        # 未知 id 422
        assert c.post(f"/api/v1/discussions/{d['id']}/convert",
                      json={"item_ids": ["nope"]}).status_code == 422

        # 缺省转换：只建剩余 1 条
        r = c.post(f"/api/v1/discussions/{d['id']}/convert", json={})
        assert len(r.json()["results"]) == 2
        created = [x for x in r.json()["results"] if x["created"]]
        assert len(created) == 1

        # free 讨论（无 action_items）→ 422
        fd = c.post("/api/v1/discussions", json={"topic": "自由", "idea_id": iid}).json()
        assert c.post(f"/api/v1/discussions/{fd['id']}/convert",
                      json={}).status_code == 422


def test_review_closed_at_creation_gated():
    """创建即关闭（带 conclusions）的 review 也走同一门控，不可绕过。"""
    init_db()
    with TestClient(app) as c:
        iid = _mk_idea(c)
        r = c.post("/api/v1/discussions", json={
            "topic": "即关", "idea_id": iid, "mode": "review", "roles": ["技术"],
            "conclusions": "直接结论",   # 缺 review 结构
        })
        assert r.status_code == 422
        assert "review" in r.json()["detail"]
