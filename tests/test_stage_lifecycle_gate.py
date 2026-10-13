# -*- coding: utf-8 -*-
"""阶段推进的文档生命周期门控测试。

缺口：advance_stage / move_to_stage 此前只查 doc_path_of（路径是否注册），
从不查 doc_statuses，导致 draft / 空契约仍能推进到 design，且 api 契约
从未被任何阶段门控。本文件验证门控：进入目标阶段前，要求相关文档 kind
的生命周期状态至少达到 required；force 可绕过但留痕。

2026-10-10 审计 P0-1 修正：
- 豁免从「隐式推断（doc_statuses 为空即视为历史任务）」改为**显式字段**
  `task.doc_gate_exempt`（迁移把存量历史任务全部置 True，行为不变）。
- 判定改为「凡已登记该 kind 的文档（doc_path_of 为真），必须有显式状态且达标」。
  原实现下**新建任务的 doc_statuses 本来就是空的** → 门控对全部新任务失效，
  实测可无任何文档 approved 直达 ready。
"""
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage
from sqlmodel import Session

client = TestClient(app)


def _mk(title="t", stage="brainstorming", exempt=False, doc_paths=None, workspace=""):
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage),
                 doc_gate_exempt=exempt, doc_paths=doc_paths or {},
                 workspace=workspace)
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
    """显式豁免（doc_gate_exempt=True）时按旧逻辑放行 —— 对应存量历史任务。

    2026-10-10 起豁免不再靠「doc_statuses 为空」推断，必须由字段显式声明。
    """
    tid = _mk(exempt=True)
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 200


def test_new_task_with_written_docs_blocked_without_approval(tmp_path):
    """P0-1 回归：spec/api/requirement **已落盘**却一份都没 approved 时，进 design 必须被拦。

    原实现下这里是 200 —— doc_statuses 为空即被当作「历史任务」放行，而新建任务的
    doc_statuses 本来就是空的，等于门控对全部新任务失效（实测可直达 ready）。
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "spec.md").write_text("# spec\n", encoding="utf-8")
    (ws / "api.md").write_text("# api\n", encoding="utf-8")
    (ws / "requirement.md").write_text("# requirement\n", encoding="utf-8")
    tid = _mk(workspace=str(ws), doc_paths={
        "spec": "spec.md", "api": "api.md", "requirement": "requirement.md"})
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design"})
    assert r.status_code == 422
    gate = r.json()["detail"]["gate"]
    assert {g["kind"] for g in gate} == {"requirement", "spec", "api"}
    assert all(g["current"] is None for g in gate)


def test_path_registered_but_not_written_is_allowed(tmp_path):
    """只登记路径、文件还没写 → 不阻断（保留「先声明产出物位置、边做边写」的工作流）。"""
    ws = tmp_path / "ws2"
    ws.mkdir()
    tid = _mk(workspace=str(ws), doc_paths={"spec": "spec.md", "api": "api.md"})
    _mk_discussion(tid)
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design"})
    assert r.status_code == 200
    assert r.json()["stage"] == "design"


def test_written_plan_blocks_planning(tmp_path):
    """P0-1 回归：plan 已落盘但未 approved 时不得进 planning。"""
    ws = tmp_path / "ws3"
    ws.mkdir()
    (ws / "plan.md").write_text("# plan\n", encoding="utf-8")
    tid = _mk(stage="design", workspace=str(ws), doc_paths={"plan": "plan.md"})
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "planning"})
    assert r.status_code == 422
    assert r.json()["detail"]["gate"][0]["kind"] == "plan"


def test_exempt_flag_reported_by_api():
    """豁免状态可经任务接口读出，便于审计区分「受门控」与「已豁免」。"""
    tid = _mk(exempt=True)
    r = client.get(f"/api/v1/tasks/{tid}")
    assert r.status_code == 200
    assert r.json()["doc_gate_exempt"] is True
    tid2 = _mk()
    assert client.get(f"/api/v1/tasks/{tid2}").json()["doc_gate_exempt"] is False


def _approve_upstream(tid):
    """把 planning 之前阶段的门槛（requirement/spec/api）全部批准。

    门控为**累计**式：进入 planning 需同时满足 design 与 planning 的门槛
    （2026-10-10 审计：否则「跳过 design 直进 planning」会绕过前序门槛）。
    """
    for kind in ("requirement", "spec", "api"):
        _set_status(tid, kind, "approved")


def test_draft_plan_blocks_advance_to_planning():
    tid = _mk(stage="design")
    _approve_upstream(tid)
    _set_status(tid, "plan", "draft")
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "planning", "plan_path": "docs/p.md"})
    assert r.status_code == 422
    assert r.json()["detail"]["gate"][0]["kind"] == "plan"


def test_approved_plan_allows_advance_to_planning():
    tid = _mk(stage="design")
    _approve_upstream(tid)
    _set_status(tid, "plan", "approved")
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "planning", "plan_path": "docs/p.md"})
    assert r.status_code == 200


def test_skipping_design_does_not_bypass_gate(tmp_path):
    """2026-10-10 审计 P0-1 回归：move 可任意跳转，跳过 design 不得免检。

    实测修复前：design 被 422 拦下，但紧接着的 planning / ready 仍 200 直达。
    注意前置：三份文档必须**已落盘**（只登记路径不拦，见 test_path_registered_but_not_written_is_allowed）。
    """
    ws = tmp_path / "ws_skip"
    ws.mkdir()
    for name in ("requirement.md", "spec.md", "api.md"):
        (ws / name).write_text(f"# {name}\n", encoding="utf-8")
    tid = _mk(stage="brainstorming", workspace=str(ws), doc_paths={
        "requirement": "requirement.md", "spec": "spec.md", "api": "api.md"})
    for dst in ("design", "planning", "ready"):
        r = client.post(f"/api/v1/tasks/{tid}/stage/move",
                        json={"target_stage": dst})
        assert r.status_code == 422, f"{dst} 未被拦截：{r.text}"
    assert client.get(f"/api/v1/tasks/{tid}").json()["stage"] == "brainstorming"


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
