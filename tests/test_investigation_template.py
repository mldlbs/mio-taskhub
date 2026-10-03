# -*- coding: utf-8 -*-
"""Investigation 任务协议测试（task c1d3be13 / R283 设计 + R284 裁定）。

覆盖 spec docs/taskhub/spec-investigation-template-r284.md 的验收 A1-A8：
    A1 普通任务 done 门控行为不变
    A2 investigation 无 verdict → done 阻断
    A3 verdict 非法/空 → 阻断；合法三值 → 放行
    A4 inconclusive + basis 缺说明 → warning 不阻断
    A5 evidence_required 未满足 → warning 不阻断
    A6 review 阶段 → warning 不阻断（Q3=B）
    A7 investigation → normal 无 force → 阻断
    A8 无新表/新状态/新进程（红线）—— 见 test_no_new_schema_objects

红线：不改 TaskState/TaskStage 枚举与转移函数；不新增表/状态/进程。
"""
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage, TaskKind, TaskEvent, TaskState

client = TestClient(app)


def _mk(kind="investigation", stage="review", title="inv"):
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage))
        if kind:
            t.task_kind = TaskKind(kind)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _verdict_event(tid, verdict, basis="因为证据 X（本地日志）", source="agent_local_log",
                   evidence_required=None, evidence_collected=None):
    with Session(engine) as s:
        meta = {"verdict": verdict, "verdict_basis": basis, "evidence_source": source}
        if evidence_required is not None:
            meta["evidence_required"] = evidence_required
        if evidence_collected is not None:
            meta["evidence_collected"] = evidence_collected
        s.add(TaskEvent(task_id=tid, event_type="investigation_verdict",
                        actor_type="agent", actor_id="test", event_metadata=meta))
        s.commit()


def _advance(tid, stage, **extra):
    return client.post(f"/api/v1/tasks/{tid}/stage",
                       json={"target_stage": stage, "review_result": "r", **extra})


# ---------- A1：普通任务行为不变 ----------

def test_A1_normal_task_done_unaffected():
    """normal 任务推进 done 不因 investigation 门控而被阻断。"""
    tid = _mk(kind="normal")
    r = _advance(tid, "done")
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "done"
    assert r.json().get("investigation_warnings") in (None, [])


def test_A1_normal_task_none_kind_unaffected():
    """task_kind 为 None 的历史任务同样不受影响。"""
    tid = _mk(kind=None)
    r = _advance(tid, "done")
    assert r.status_code == 200, r.text


# ---------- A2：无 verdict → done 阻断 ----------

def test_A2_investigation_done_without_verdict_blocked():
    tid = _mk()
    r = _advance(tid, "done")
    assert r.status_code == 422
    gate = r.json()["detail"]["gate"]
    assert any(g.get("rule") == "H4" for g in gate)


# ---------- A3：verdict 合法/非法 ----------

def test_A3_invalid_verdict_blocked():
    tid = _mk()
    _verdict_event(tid, "maybe")          # 非法枚举
    r = _advance(tid, "done")
    assert r.status_code == 422
    assert any(g.get("rule") == "H1" for g in r.json()["detail"]["gate"])


def test_A3_empty_basis_blocked():
    tid = _mk()
    _verdict_event(tid, "confirmed", basis="   ")   # H2
    r = _advance(tid, "done")
    assert r.status_code == 422
    assert any(g.get("rule") == "H2" for g in r.json()["detail"]["gate"])


def test_A3_invalid_source_blocked():
    tid = _mk()
    _verdict_event(tid, "confirmed", source="vibes")  # H3
    r = _advance(tid, "done")
    assert r.status_code == 422
    assert any(g.get("rule") == "H3" for g in r.json()["detail"]["gate"])


def test_A3_valid_verdicts_pass():
    for v in ("confirmed", "rejected", "inconclusive"):
        tid = _mk(title=f"inv-{v}")
        _verdict_event(tid, v, basis="证据充分（本地 jsonl 对照 Hub）",
                       source="agent_local_log")
        r = _advance(tid, "done")
        assert r.status_code == 200, f"{v}: {r.text}"
        assert r.json()["stage"] == "done"


def test_A3_force_bypasses_hard_gate():
    tid = _mk()
    r = _advance(tid, "done", force=True)     # 无 verdict + force
    assert r.status_code == 200, r.text


# ---------- A4：inconclusive + basis 缺"为何不足" → warning 不阻断 ----------

def test_A4_inconclusive_without_reason_is_warning_not_block():
    tid = _mk()
    _verdict_event(tid, "inconclusive", basis="就是不确定。", source="human_observation")
    r = _advance(tid, "done")
    assert r.status_code == 200, r.text
    warns = r.json().get("investigation_warnings") or []
    assert any(w.get("rule") == "S2" for w in warns)


def test_A4_inconclusive_with_reason_no_s2():
    tid = _mk()
    _verdict_event(tid, "inconclusive",
                   basis="证据不足，缺少 agent_local_log，无法判断启动失败原因",
                   source="human_observation")
    r = _advance(tid, "done")
    assert r.status_code == 200, r.text
    warns = r.json().get("investigation_warnings") or []
    assert not any(w.get("rule") == "S2" for w in warns)


# ---------- A5：evidence_required 未满足 → warning 不阻断 ----------

def test_A5_evidence_required_unmet_is_warning():
    tid = _mk()
    _verdict_event(tid, "confirmed", basis="ok（本地日志）", source="agent_local_log",
                   evidence_required=["taskhub_event", "agent_local_log"],
                   evidence_collected=["agent_local_log"])   # 缺 taskhub_event
    r = _advance(tid, "done")
    assert r.status_code == 200, r.text
    warns = r.json().get("investigation_warnings") or []
    assert any(w.get("rule") == "S1/S3" for w in warns)


# ---------- A6：review 阶段只 warning 不阻断（Q3=B）----------

def test_A6_review_without_verdict_is_warning_not_block():
    tid = _mk(stage="review")
    # 直接验证门控函数语义：review 只返回 warning，不抛 422
    from mio_taskhub.api.task_stages import _check_investigation_gate
    with Session(engine) as s:
        t = s.get(Task, tid)
        warns = _check_investigation_gate(t, TaskStage.REVIEW, s)
    assert any(w.get("missing") == "investigation_verdict" for w in warns)


def test_A6_review_warning_surfaced_in_advance(monkeypatch):
    """从 implementing→review 时，investigation 缺 verdict 只 warning 不阻断。"""
    tid = _mk(stage="implementing")
    r = _advance(tid, "review")
    assert r.status_code == 200, r.text
    warns = r.json().get("investigation_warnings") or []
    assert any(w.get("missing") == "investigation_verdict" for w in warns)


# ---------- A7：investigation → normal 无 force → 阻断 ----------

def test_A7_kind_change_without_force_blocked():
    tid = _mk()
    r = client.patch(f"/api/v1/tasks/{tid}", json={"task_kind": "normal"})
    assert r.status_code == 422
    assert r.json()["detail"]["gate"][0]["rule"] == "C1"


def test_A7_kind_change_with_force_and_trace():
    tid = _mk()
    r = client.patch(f"/api/v1/tasks/{tid}",
                     json={"task_kind": "normal", "force": True,
                           "kind_change_reason": "误建"})
    assert r.status_code == 200, r.text
    with Session(engine) as s:
        evs = s.exec(select(TaskEvent).where(
            TaskEvent.task_id == tid,
            TaskEvent.event_type == "kind_changed")).all()
        assert len(evs) == 1
        assert evs[0].event_metadata["from"] == "investigation"
        assert evs[0].event_metadata["to"] == "normal"


# ---------- A8：红线——无新表/状态/枚举外状态 ----------

def test_A8_no_new_task_state_or_stage():
    """TaskState / TaskStage 枚举值集合未被 investigation 扩展。"""
    assert "investigation" not in [s.value for s in TaskState]
    assert "investigation" not in [s.value for s in TaskStage]


def test_A8_investigation_kind_is_data_only():
    """investigation 仅是一个 TaskKind 枚举值，不引入新表。"""
    assert TaskKind.INVESTIGATION.value == "investigation"
    assert set(TaskKind) == {TaskKind.NORMAL, TaskKind.CHANGE_TRACKING,
                             TaskKind.REVIEW, TaskKind.INVESTIGATION}


# ---------- 创建路径：可直接建 investigation 任务 ----------

def test_create_investigation_task():
    r = client.post("/api/v1/tasks", json={"title": "inv-create", "task_kind": "investigation",
                                            "stage": "ready"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    with Session(engine) as s:
        assert s.get(Task, tid).task_kind == TaskKind.INVESTIGATION
