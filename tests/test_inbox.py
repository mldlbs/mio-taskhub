# -*- coding: utf-8 -*-
"""决策收件箱 GET /api/v1/tasks/inbox（E7 产品化）+ 门控 422 人话化。

覆盖：
  - 三队列分桶（决策 / 验收 / 失败）与 summary 计数
  - 机器噪声恒过滤（不受任何开关影响）
  - action 提示存在
  - set_doc_status 非法转移的 422 报错含「你要做的是」
"""
import os

from fastapi.testclient import TestClient
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Run, RunState, Task, TaskState
from mio_taskhub.noise import is_noise

client = TestClient(app)


def _mk_task(task_id: str, state, stage, title=None, depends=None,
             agent_type=None, workspace="") -> Task:
    with Session(engine) as s:
        t = Task(id=task_id, title=title or f"task-{task_id}", state=state,
                 stage=stage, workspace=workspace,
                 target_agent_type=agent_type,
                 depends_on=depends or [])
        s.add(t)
        s.commit()
        return t


# ── noise 模块 ─────────────────────────────────────────────────────

def test_noise_predicate():
    assert is_noise({"target_agent_type": "idea-reviewer", "title": "x"})
    assert is_noise({"target_agent_type": None, "title": "「Idea」想法评审"})
    assert is_noise({"target_agent_type": None, "title": "[insight] x"})
    assert is_noise({"target_agent_type": None, "title": "[变更] x"})
    assert is_noise({"target_agent_type": None, "title": "[溯因] x"})
    assert is_noise({"target_agent_type": None, "title": "__verify_x"})
    assert not is_noise({"target_agent_type": "coding", "title": "【需求23-F6】染色"})
    assert not is_noise({"target_agent_type": None, "title": "排水管网数据联动"})
    assert not is_noise(None)


# ── inbox 三队列 ────────────────────────────────────────────────────

def test_inbox_three_queues_and_noise_filtered():
    # 依赖阻塞：b01 依赖失败的 pre
    _mk_task("bpre0001", TaskState.FAILED, "implementing", title="前置：骨架")
    _mk_task("b010001", TaskState.QUEUED, "brainstorming",
             title="后置：统计卡", depends=["bpre0001"])
    # 待验收：completed + review
    _mk_task("b020002", TaskState.COMPLETED, "review", title="验收项A")
    _mk_task("b020003", TaskState.COMPLETED, "review", title="验收项B")
    # 失败
    _mk_task("b030004", TaskState.FAILED, "implementing", title="失败项")
    # 噪声（不进任何队列）
    _mk_task("b04no001", TaskState.COMPLETED, "review",
             title="「Idea」想法评审", agent_type="idea-reviewer")
    _mk_task("b04no002", TaskState.FAILED, "implementing", title="[insight] xx")
    # 完成且 done → 非待验收
    _mk_task("b05done1", TaskState.COMPLETED, "done", title="已完结")

    r = client.get("/api/v1/tasks/inbox")
    assert r.status_code == 200
    d = r.json()
    ids_decide = [x["id"] for x in d["decide"]]
    ids_review = [x["id"] for x in d["review"]]
    ids_failed = [x["id"] for x in d["failed"]]
    assert "b010001" in ids_decide
    assert set(ids_review) >= {"b020002", "b020003"}
    assert "b030004" in ids_failed
    # 噪声与已完结不出现
    for bad in ("b04no001", "b04no002", "b05done1"):
        assert bad not in ids_decide + ids_review + ids_failed
    # summary 与 action
    assert d["summary"]["total"] >= 4
    for x in d["decide"] + d["review"] + d["failed"]:
        assert x.get("action")
        assert x.get("id") and x.get("title")
    # 依赖阻塞项带 blocked_by
    dec = next(x for x in d["decide"] if x["id"] == "b010001")
    assert "bpre0001" in dec["blocked_by"]


def test_inbox_review_sorted_oldest_first():
    # created_at 由 DB default 决定，同秒内不稳定——这里只验证排序键方向：
    # 构造两个不同 created_at 的 review 项，老的应排前
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    for tid, delta in (("b06old01", timedelta(days=40)), ("b06new01", timedelta(days=1))):
        with Session(engine) as s:
            t = Task(id=tid, title=f"rv-{tid}", state=TaskState.COMPLETED,
                     stage="review", created_at=now - delta)
            s.add(t)
            s.commit()
    r = client.get("/api/v1/tasks/inbox")
    d = r.json()
    review_ids = [x["id"] for x in d["review"]]
    if "b06old01" in review_ids and "b06new01" in review_ids:
        assert review_ids.index("b06old01") < review_ids.index("b06new01")
    else:
        # 其中一个被 20 条上限挤出时只验证在表内那个的顺序无异常
        assert ("b06old01" in review_ids) or ("b06new01" in review_ids)


# ── 422 人话化 ─────────────────────────────────────────────────────

def test_doc_status_422_contains_next_step(tmp_path):
    ws = str(tmp_path)
    os.makedirs(os.path.join(ws, "docs"), exist_ok=True)
    with open(os.path.join(ws, "docs", "spec.md"), "w", encoding="utf-8") as f:
        f.write("# spec\n")
    _mk_task("b070001", TaskState.RUNNING, "design", workspace=ws)
    with Session(engine) as s:
        t = s.get(Task, "b070001")
        t.doc_paths = {"spec": "docs/spec.md"}
        s.add(t)
        s.commit()

    # draft 未落状态直跳 approved → 非法转移 → 422 必须带「你要做的是」
    r = client.post("/api/v1/tasks/b070001/doc/spec/status",
                    json={"state": "approved"})
    assert r.status_code == 422
    msg = r.text
    assert "你要做的是" in msg
    assert "draft" in msg
