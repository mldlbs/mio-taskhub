# -*- coding: utf-8 -*-
"""阶段推进的文档生命周期门控测试。

缺口：advance_stage / move_to_stage 此前只查 doc_path_of（路径是否注册），
从不查 doc_statuses，导致 draft / 空契约仍能推进到 design，且 api 契约
从未被任何阶段门控。本文件验证新门控：进入目标阶段前，要求相关文档 kind
的生命周期状态至少达到 required；仅当该 kind 已显式设过状态时才校验
（旧任务 / 未跟踪任务向后兼容放行）；force 可绕过但留痕。
"""
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage
from sqlmodel import Session

client = TestClient(app)


def _mk(title="t", stage="brainstorming"):
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage))
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _set_status(tid, kind, state):
    """直接写 doc_statuses（绕过 set_doc_status 的「需先有文档」校验，纯测门控）。"""
    with Session(engine) as s:
        t = s.get(Task, tid)
        st = dict(t.doc_statuses or {})
        st[kind] = {"state": state, "at": "2026-09-18T00:00:00+00:00", "note": ""}
        t.doc_statuses = st
        s.add(t)
        s.commit()


def _mk_discussion(tid):
    client.post(f"/api/v1/tasks/{tid}/discussions",
                json={"topic": "理解", "agent": "a", "summary": "s", "conclusions": "c"})


def test_draft_spec_blocks_advance_to_design():
    tid = _mk()
    _set_status(tid, "spec", "draft")
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 422
    assert any(g["kind"] == "spec" for g in r.json()["detail"]["gate"])


def test_review_spec_blocks_advance_to_design():
    # review 还不够，必须 approved
    tid = _mk()
    _set_status(tid, "spec", "review")
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 422


def test_approved_spec_allows_advance_to_design():
    tid = _mk()
    _set_status(tid, "requirement", "approved")   # design 门控同时要求 requirement
    _set_status(tid, "spec", "approved")
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 200
    assert r.json()["stage"] == "design"


def test_tracked_task_without_requirement_blocks_design():
    """已进入文档生命周期（有 spec/api 状态）却无 requirement → 阻断进 design。"""
    tid = _mk()
    _set_status(tid, "spec", "approved")
    _set_status(tid, "api", "approved")
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 422
    assert any(g["kind"] == "requirement" and g["current"] is None
               for g in r.json()["detail"]["gate"])


def test_draft_api_blocks_advance_to_design():
    # api 此前不在任何阶段 document_kinds 里；现在进 design 必须 approved
    tid = _mk()
    _set_status(tid, "spec", "approved")
    _set_status(tid, "api", "draft")
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 422
    assert any(g["kind"] == "api" for g in r.json()["detail"]["gate"])


def test_legacy_task_without_status_not_blocked():
    # 不设任何 doc_statuses，仍按旧逻辑放行（向后兼容，不阻断历史数据）
    tid = _mk()
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 200


def test_draft_plan_blocks_advance_to_planning():
    tid = _mk(stage="design")
    _set_status(tid, "plan", "draft")
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "planning", "plan_path": "docs/p.md"})
    assert r.status_code == 422
    assert r.json()["detail"]["gate"][0]["kind"] == "plan"


def test_approved_plan_allows_advance_to_planning():
    tid = _mk(stage="design")
    _set_status(tid, "plan", "approved")
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "planning", "plan_path": "docs/p.md"})
    assert r.status_code == 200


def test_force_bypasses_lifecycle_gate_and_leaves_trail():
    tid = _mk()
    _set_status(tid, "spec", "draft")
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md", "force": True})
    assert r.status_code == 200
    assert r.json()["stage"] == "design"
    ev = client.get("/api/v1/events").json()["events"]
    assert any(e["type"] == "task_stage_gate_forced" and e["entity_id"] == tid
               for e in ev)


def test_move_to_design_also_gated():
    tid = _mk()  # brainstorming
    _set_status(tid, "spec", "draft")
    r = client.post(f"/api/v1/tasks/{tid}/stage/move",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 422
    assert any(g["kind"] == "spec" for g in r.json()["detail"]["gate"])


def test_requirements_endpoint_exposes_lifecycle_gate():
    r = client.get("/api/v1/tasks/stages/requirements")
    assert r.status_code == 200
    gates = r.json()["stages"]
    assert gates["design"]["lifecycle_gate"] == {
        "requirement": "approved", "spec": "approved", "api": "approved"}
    assert gates["planning"]["lifecycle_gate"] == {"plan": "approved"}
