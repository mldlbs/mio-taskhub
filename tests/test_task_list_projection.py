# tests/test_task_list_projection.py
# 任务 613bc0ca · FR-1/FR-2/FR-3/FR-4/FR-8
# 目的：给「列表端点列投影 + board 汇总 SQL 聚合」这次性能改造**上锁**——
#   契约零差异（字段集与取值不变）、过滤语义不变、计数口径不变、
#   告警候选集收窄后行为不变。
#
# 背景：本文件与既有 test_board_summary.py 的分工——
#   · test_board_summary.py：验证 board 汇总的**业务行为**（本文件不重复）；
#   · 本文件：验证**契约与等价性**——尤其「投影漏字段会静默破坏响应」
#     这类回归，用例能直接抓到。
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Task, TaskStage

client = TestClient(app)

# 列表端点响应字段集（2026-11-18 实测锁定）。
# **这张清单就是契约**：改响应字段必须同步改清单与 api/tasks.py 的
# _TASK_LIST_COLUMNS 投影；少一个 → 投影漏字段 → 响应缺键（本用例会红）。
EXPECTED_TASK_KEYS = {
    "id", "title", "state", "stage", "priority", "target_agent_type",
    "fallback_after", "grab_mode", "doc_gate_exempt", "depends_on",
    "idea_id", "parent_id", "role", "complexity", "gate_tier",
    "completion_run_id", "est_duration_min", "task_kind", "created_at",
    "project", "workspace", "labels",
}


def _mk(title, stage="ready", **kw):
    """建任务。done/cancelled 阶段走 DB 直建（API 不接受这两个阶段）。"""
    if stage in ("done", "cancelled"):
        with Session(engine) as s:
            t = Task(title=title, stage=TaskStage(stage), **kw)
            s.add(t)
            s.commit()
            s.refresh(t)
            return {"id": t.id}
    body = {"title": title, "stage": stage}
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


def _row(task_id):
    for x in client.get("/api/v1/tasks", params={"cancelled": "true"}).json():
        if x["id"] == task_id:
            return x
    return None


# ── FR-1 / FR-6：契约零差异 ─────────────────────────────────────────────

def test_list_response_field_set_is_stable():
    """响应字段集必须与锁定的契约完全一致。

    这条同时兜住两个回归：
      · 投影漏字段 → 响应缺键 → 本用例红；
      · 有人偷偷给响应加了字段 → 字段集对不上 → 本用例红。
    """
    _mk("字段集校验", "ready")          # 先确保库里有数据（本用例可能是文件首个）
    r = client.get("/api/v1/tasks")
    assert r.status_code == 200
    data = r.json()
    assert data, "测试库应有任务，否则本用例失去意义"
    assert set(data[0].keys()) == EXPECTED_TASK_KEYS


def test_projected_values_roundtrip():
    """投影后取到的值必须与写入的一致（不只是键存在）。"""
    t = client.post("/api/v1/tasks", json={"title": "投影取值校验", "priority": 2}).json()
    r = client.patch(f"/api/v1/tasks/{t['id']}",
                     json={"labels": ["alpha", "beta"], "project": "proj-x"})
    assert r.status_code == 200
    row = _row(t["id"])
    assert row is not None
    assert row["title"] == "投影取值校验"
    assert row["priority"] == 2
    assert row["labels"] == ["alpha", "beta"]
    assert row["project"] == "proj-x"
    assert row["state"] == "queued"
    assert row["est_duration_min"] > 0
    assert row["task_kind"] == "normal"
    assert row["created_at"]


# ── FR-2 / FR-8：过滤语义不变 ───────────────────────────────────────────

def test_state_filter():
    _mk("S1", "ready")
    rows = client.get("/api/v1/tasks", params={"state": "queued"}).json()
    assert rows
    assert all(x["state"] == "queued" for x in rows)


def test_stage_filter():
    _mk("S2", "brainstorming")
    rows = client.get("/api/v1/tasks", params={"stage": "brainstorming"}).json()
    assert rows
    assert all(x["stage"] == "brainstorming" for x in rows)


def test_agent_type_filter():
    _mk("A1", "ready", target_agent_type="special-agent")
    rows = client.get("/api/v1/tasks", params={"agent_type": "special-agent"}).json()
    assert any(x["target_agent_type"] == "special-agent" for x in rows)
    assert all(x["target_agent_type"] in (None, "special-agent") for x in rows)


def test_cancelled_true_is_superset_of_default():
    """带 cancelled=true ⊇ 默认；默认结果里不含 cancelled 状态的任务。"""
    allc = client.get("/api/v1/tasks", params={"cancelled": "true"}).json()
    default = client.get("/api/v1/tasks").json()
    ids_all = {x["id"] for x in allc}
    ids_def = {x["id"] for x in default}
    assert ids_def <= ids_all
    assert all(x["state"] != "cancelled" for x in default)


def test_invalid_state_returns_400_with_code():
    r = client.get("/api/v1/tasks", params={"state": "not-a-state"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task.invalid_state"


def test_invalid_stage_returns_400_with_code():
    r = client.get("/api/v1/tasks", params={"stage": "not-a-stage"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task.invalid_stage"


# ── FR-3：SQL 聚合与原 Python 实现逐项一致 ─────────────────────────────

def test_board_counts_sql_matches_reference_implementation():
    """`_counts_by_stage_db`（SQL 聚合）必须与 `_count_by_stage`（原实现）逐项相等。

    这是 FR-7「计数一致性」的核心断言——**不允许"差不多"，必须相等**。
    """
    from mio_taskhub.api.board import _count_by_stage, _counts_by_stage_db
    with Session(engine) as s:
        all_tasks = s.exec(select(Task)).all()
        assert _counts_by_stage_db(s) == _count_by_stage(all_tasks)


def test_board_summary_counts_match_api():
    """board/summary 的 counts 与全量统计一致（含取消态任务）。"""
    data = client.get("/api/v1/board/summary").json()
    with Session(engine) as s:
        all_tasks = s.exec(select(Task)).all()
    assert data["counts"]["implementing"] == sum(1 for t in all_tasks
                                                if t.stage == TaskStage.IMPLEMENTING)


# ── FR-4：候选集收窄后告警行为不变 ─────────────────────────────────────

def test_dependency_alert_ignores_non_early_stage():
    """依赖阻塞告警只覆盖早阶段（brainstorming/design/planning）。

    候选集收窄后，这条行为**必须不变**：implementing 阶段的前置取消不报阻塞。
    """
    parent = _mk("dep-parent", stage="cancelled")
    child = _mk("dep-child", stage="implementing", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    assert not any(child["id"] in a["message"] and "依赖阻塞" in a["message"]
                   for a in data["alerts"])


def test_dependency_alert_still_fires_for_early_stage():
    """早阶段的依赖阻塞**仍然要报**——候选集收窄不能漏报。"""
    parent = _mk("dep-parent2", stage="cancelled")
    child = _mk("dep-child2", stage="planning", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    assert any(child["id"] in a["message"] and "依赖阻塞" in a["message"]
               for a in data["alerts"])


def test_overdue_alert_still_fires():
    """逾期告警仍然要报（due_at 在过去 + 非终态）。

    注意：逾期告警的消息只含**标题**（不含 id）——依赖阻塞告警才带 id。
    """
    t = _mk("overdue-task", stage="brainstorming",
            due_at="2020-01-01T00:00:00+00:00")
    data = client.get("/api/v1/board/summary").json()
    assert any(t["id"] in a["message"] or "overdue-task" in a["message"]
               for a in data["alerts"] if "超截止时间" in a["message"]), \
        "逾期任务应产生「超截止时间」告警"


def test_recent_done_only_lists_completed_tasks():
    """recent_done 只含已完成任务——join+limit 改造后不得放宽。"""
    _mk("not-done", "ready")
    data = client.get("/api/v1/board/summary").json()
    titles = [x["title"] for x in data["recent_done"]]
    assert "not-done" not in titles
