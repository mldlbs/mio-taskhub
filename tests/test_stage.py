from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage
from sqlmodel import Session

client = TestClient(app)

def _mk(title="t", stage="brainstorming"):
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage))
        s.add(t); s.commit(); s.refresh(t)
        return t.id

def test_advance_to_design_requires_spec_path():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design"})
    assert r.status_code == 422

def test_advance_to_design_requires_discussion():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design", "spec_path": "s.md"})
    assert r.status_code == 422  # no discussion record

def test_advance_to_design_with_spec_and_discussion():
    tid = _mk()
    client.post(f"/api/v1/tasks/{tid}/discussions", json={"topic": "理解", "agent": "a", "summary": "s", "conclusions": "c"})
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 200
    d = r.json()
    assert d["stage"] == "design" and d["spec_path"] == "docs/s.md"

def test_advance_to_planning_requires_plan_path():
    tid = _mk(stage="design")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "planning"})
    assert r.status_code == 422

def test_advance_to_planning_with_plan():
    tid = _mk(stage="design")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "planning", "plan_path": "docs/p.md"})
    assert r.status_code == 200
    assert r.json()["stage"] == "planning"

def test_advance_to_done_without_review_result_allowed():
    tid = _mk(stage="review")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "done"})
    assert r.status_code == 200
    assert r.json()["stage"] == "done"

def test_advance_to_done_with_review():
    tid = _mk(stage="review")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "done", "review_result": "approved"})
    assert r.status_code == 200
    assert r.json()["stage"] == "done"


def test_advance_to_implementing_requires_changelog():
    # brainstorming -> design -> planning -> ready，进入 implementing 需 changelog
    tid = _mk()
    client.post(f"/api/v1/tasks/{tid}/discussions",
                json={"topic": "理解", "agent": "a", "summary": "s", "conclusions": "c"})
    assert client.post(f"/api/v1/tasks/{tid}/stage",
                       json={"target_stage": "design", "spec_path": "docs/s.md"}).status_code == 200
    assert client.post(f"/api/v1/tasks/{tid}/stage",
                       json={"target_stage": "planning", "plan_path": "docs/p.md"}).status_code == 200
    assert client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "ready"}).status_code == 200
    # 缺 changelog → 422（2026-09-17 新增 implementing 门槛）
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "implementing"})
    assert r.status_code == 422
    assert "changelog" in r.json()["detail"]
    # 提供 changelog → 200
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "implementing", "doc_paths": {"changelog": "docs/changelog.md"}})
    assert r.status_code == 200
    assert r.json()["stage"] == "implementing"
    assert r.json()["doc_paths"].get("changelog") == "docs/changelog.md"

def test_standard_tier_requires_approved_doc(tmp_path):
    """standard 档：已登记文档只存在还不够，必须 approved。"""
    from mio_taskhub.api.task_stages import _check_lifecycle_gate

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "s.md").write_text("# spec", encoding="utf-8")
    t = Task(title="std", stage=TaskStage.DESIGN, workspace=str(tmp_path),
             gate_tier="standard", gate_tier_reason="spec_registered",
             doc_paths={"spec": "docs/s.md"})

    with pytest.raises(HTTPException) as ei:
        _check_lifecycle_gate(t, TaskStage.PLANNING)
    assert ei.value.status_code == 422
    assert any(g.get("reason") == "registered_but_not_approved"
               for g in ei.value.detail["gate"])

    t.doc_statuses = {"spec": {"state": "approved"}}
    assert _check_lifecycle_gate(t, TaskStage.PLANNING) == []


def test_light_tier_registered_doc_must_be_approved(tmp_path):
    """light 档：登记了文档就不能拿 draft 直通（方案 B）。"""
    from mio_taskhub.api.task_stages import _check_lifecycle_gate

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "s.md").write_text("# spec", encoding="utf-8")
    t = Task(title="light", stage=TaskStage.DESIGN, workspace=str(tmp_path),
             gate_tier="light", gate_tier_reason="default",
             doc_paths={"spec": "docs/s.md"})

    with pytest.raises(HTTPException) as ei:
        _check_lifecycle_gate(t, TaskStage.PLANNING)
    assert ei.value.status_code == 422
    assert any(g.get("reason") == "registered_but_not_approved"
               for g in ei.value.detail["gate"])

    t.doc_statuses = {"spec": {"state": "approved"}}
    assert _check_lifecycle_gate(t, TaskStage.PLANNING) == []


def test_light_tier_without_docs_still_passes(tmp_path):
    """未登记文档仍 fail-open；是否强制 requirement 属方案 C。"""
    from mio_taskhub.api.task_stages import _check_lifecycle_gate

    t = Task(title="light-nodoc", stage=TaskStage.DESIGN, workspace=str(tmp_path),
             gate_tier="light", gate_tier_reason="default")
    assert _check_lifecycle_gate(t, TaskStage.PLANNING) == []


def test_light_tier_registered_missing_file_blocks(tmp_path):
    """登记了但文件不在，不能静默放行。"""
    from mio_taskhub.api.task_stages import _check_lifecycle_gate

    t = Task(title="light-missing", stage=TaskStage.DESIGN, workspace=str(tmp_path),
             gate_tier="light", gate_tier_reason="default",
             doc_paths={"spec": "docs/not-there.md"})

    with pytest.raises(HTTPException) as ei:
        _check_lifecycle_gate(t, TaskStage.PLANNING)
    assert any(g.get("reason") == "registered_but_file_missing"
               for g in ei.value.detail["gate"])


def test_doc_registered_in_same_move_does_not_gate_that_move(tmp_path):
    """进 design 时才登记 spec 不能被本次校验拦住（否则永远无法进入 design）。"""
    from mio_taskhub.api.task_stages import _check_lifecycle_gate

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "s.md").write_text("# spec", encoding="utf-8")
    t = Task(title="chicken-egg", stage=TaskStage.DESIGN, workspace=str(tmp_path),
             gate_tier="light", gate_tier_reason="default",
             doc_paths={"spec": "docs/s.md"})

    with pytest.raises(HTTPException):
        _check_lifecycle_gate(t, TaskStage.PLANNING)
    assert _check_lifecycle_gate(t, TaskStage.PLANNING, skip_kinds={"spec"}) == []


def test_unresolvable_doc_path_fails_open():
    """workspace 缺失时无法校验文档，沿用系统惯例放行。"""
    from mio_taskhub.api.task_stages import _check_lifecycle_gate

    t = Task(title="no-ws", stage=TaskStage.DESIGN, workspace="",
             gate_tier="light", gate_tier_reason="default",
             doc_paths={"spec": "docs/s.md"})
    assert _check_lifecycle_gate(t, TaskStage.PLANNING) == []


def test_advance_illegal_transition():
    tid = _mk()  # brainstorming
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "planning"})
    assert r.status_code == 400

def test_advance_cancelled():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "cancelled"})
    assert r.status_code == 200
    assert r.json()["stage"] == "cancelled"

def test_get_task_returns_stage_fields():
    tid = _mk(stage="planning")
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "planning"
    assert "spec_path" in d and "plan_path" in d and "review_result" in d

def test_list_tasks_filter_by_stage():
    _mk(stage="design")
    _mk(stage="ready")
    r = client.get("/api/v1/tasks", params={"stage": "design"})
    data = r.json()
    assert all(t["stage"] == "design" for t in data)
    assert len(data) == 1

def test_create_task_defaults_brainstorming():
    r = client.post("/api/v1/tasks", json={"title": "new-task"})
    tid = r.json()["id"]
    detail = client.get(f"/api/v1/tasks/{tid}").json()
    assert detail["stage"] == "brainstorming"

def test_create_task_can_set_stage_ready():
    r = client.post("/api/v1/tasks", json={"title": "ready-task", "stage": "ready"})
    tid = r.json()["id"]
    detail = client.get(f"/api/v1/tasks/{tid}").json()
    assert detail["stage"] == "ready"

def test_claim_only_picks_ready():
    ready_id = _mk(title="ready", stage="ready")
    _mk(title="brain", stage="brainstorming")
    r = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent"})
    assert r.status_code == 200
    assert r.json()["task_id"] == ready_id
    d = client.get(f"/api/v1/tasks/{ready_id}").json()
    assert d["stage"] == "implementing"

def test_claim_returns_204_when_no_ready():
    _mk(title="only-brain", stage="brainstorming")
    r = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent2"})
    assert r.status_code == 204

def test_claim_moves_ready_to_implementing():
    tid = _mk(title="to-impl", stage="ready")
    r = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent3"})
    assert r.json()["task_id"] == tid
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "implementing"
    assert d["state"] == "claimed"

def test_submit_result_moves_to_review():
    _mk(title="finish", stage="ready")
    claim = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent4"}).json()
    rid, tid = claim["id"], claim["task_id"]
    r = client.post(f"/api/v1/runs/{rid}/result", json={"success": True, "result": "ok"})
    assert r.status_code == 200
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "review"
    assert d["state"] == "completed"


# --- T25：failed 任务的人工验收（2026-11-04 报错「未定义的转换: (failed,review) -> (completed,done)」）---
def _mk_failed(title="failed-task", stage="review"):
    from mio_taskhub.models import TaskState
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage), state=TaskState.FAILED)
        s.add(t); s.commit(); s.refresh(t)
        return t.id

def test_advance_failed_review_to_done_is_allowed():
    """人审推翻失败判定：FAILED(review) → COMPLETED(done) 单跳（T25）。"""
    tid = _mk_failed()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "done"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["stage"] == "done" and d["state"] == "completed"

def test_advance_failed_to_intermediate_stage_is_rejected_with_readable_reason():
    """失败任务不能中途往前推：要给 retry / cancel / 人工验收三条出路，而不是状态机天书。"""
    tid = _mk_failed(stage="implementing")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "review"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "失败状态" in detail and "retry" in detail and "cancelled" in detail

def test_advance_failed_to_cancelled_still_works():
    tid = _mk_failed(stage="implementing")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "cancelled"})
    assert r.status_code == 200
    assert r.json()["state"] == "cancelled"


# --- T25：failed 任务的人工验收（2026-11-04 报错「未定义的转换: (failed,review) -> (completed,done)」）---
def _mk_failed(title="failed-task", stage="review"):
    from mio_taskhub.models import TaskState
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage), state=TaskState.FAILED)
        s.add(t); s.commit(); s.refresh(t)
        return t.id

def test_advance_failed_review_to_done_is_allowed():
    """人审推翻失败判定：FAILED(review) → COMPLETED(done) 单跳（T25）。"""
    tid = _mk_failed()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "done"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["stage"] == "done" and d["state"] == "completed"

def test_advance_failed_to_intermediate_stage_is_rejected_with_readable_reason():
    """失败任务不能中途往前推：要给 retry / cancel / 人工验收三条出路，而不是状态机天书。"""
    tid = _mk_failed(stage="implementing")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "review"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "失败状态" in detail and "retry" in detail and "cancelled" in detail

def test_advance_failed_to_cancelled_still_works():
    tid = _mk_failed(stage="implementing")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "cancelled"})
    assert r.status_code == 200
    assert r.json()["state"] == "cancelled"


def test_advance_to_design_discussion_exempt_when_requirement_approved():
    """需求文档已批准时，进设计的讨论门自动豁免：方向确认由需求文档承担。

    背景（用户 2026-11-05 拍板）：讨论门和需求文档门在「定方向」上重复收费，
    requirement=approved 即视为方向已确认，不再单独要求讨论记录。
    """
    tid = _mk()
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.doc_statuses = {"requirement": {"state": "approved"}}
        s.add(t)
        s.commit()
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "design"


def test_advance_to_design_discussion_still_required_when_requirement_draft():
    """豁免只在需求文档真到 approved 时生效；草稿状态讨论门照常。"""
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 422
    assert "discussion record" in r.json()["detail"]
