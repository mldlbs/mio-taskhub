# -*- coding: utf-8 -*-
"""P2b 拆分校验 / apply（FR-9 / FR-11）测试。"""
import copy
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import mio_taskhub.decomposition as dec
from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Task

client = TestClient(app)


def _enable(monkeypatch, stage="validate"):
    monkeypatch.setenv("MIO_DECOMPOSE_STAGE", stage)


def _mk_parent(complexity=None, description="实现拆分校验", **over):
    body = {
        "title": "父任务",
        "description": description,
        "stage": "ready",
        "complexity": complexity,
    }
    body.update(over)
    r = client.post("/api/v1/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _child(ref="C1", plan="P1", **over):
    body = {
        "ref": ref,
        "title": f"子任务 {ref}",
        "description": f"完成 {ref}",
        "plan_item_refs": [plan],
        "acceptance_criteria": ["验收通过"],
        "deliverables": [f"{ref.lower()}.py"],
        "files": [f"{ref.lower()}.py"],
        "verification": f"pytest -k {ref}",
        "depends_on_refs": [],
        "complexity": "S",
        "contracts": {
            "inputs": [],
            "outputs": [],
            "boundaries": {"allowed_files": [f"{ref.lower()}.py"]},
        },
    }
    body.update(over)
    return body


def _payload(children=None, **over):
    body = {
        "version": 1,
        "parent_summary": "把父任务拆成两个可独立交付的子任务",
        "plan_items": [
            {"ref": "P1", "title": "第一步", "acceptance": "第一步完成"},
            {"ref": "P2", "title": "第二步", "acceptance": "第二步完成"},
        ],
        "contracts": [],
        "children": children if children is not None else [
            _child("C1", "P1"),
            _child("C2", "P2", depends_on_refs=["C1"]),
        ],
    }
    body.update(over)
    return body


def _codes(response):
    detail = response.json().get("detail")
    if isinstance(detail, dict):
        return {e["code"] for e in detail.get("errors", [])} or {detail.get("code")}
    return set()


def _write_spec(tmp_path):
    doc = tmp_path / "docs" / "spec.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        "# Spec\n\nFULL_ONLY\n\n## 2.3 Target\n\nSECTION_ONLY\n\n"
        "## 9 Other\n\nOTHER_ONLY\n",
        encoding="utf-8",
    )
    return doc


def test_preview_valid_and_apply_creates_children(monkeypatch):
    """T29：合法 apply 原子创建子任务、回写父任务、依赖映射正确。"""
    _enable(monkeypatch)
    pid = _mk_parent(complexity="M")
    payload = _payload()
    pv = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    assert pv.status_code == 200, pv.text
    assert pv.json()["ok"] is True
    token = pv.json()["apply_token"]

    r = client.post(f"/api/v1/tasks/{pid}/decompose/apply",
                    json={**payload, "apply_token": token})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["children"]) == 2

    with Session(engine) as db:
        parent = db.get(Task, pid)
        assert parent.role == "main"
        decomp = json.loads(parent.decomposition)
        assert decomp["child_ids"] == list(data["ref_map"].values())
        children = {c.id: c for c in db.exec(select(Task).where(Task.parent_id == pid)).all()}
        c1 = children[data["ref_map"]["C1"]]
        c2 = children[data["ref_map"]["C2"]]
        assert c1.role == "child" and c1.parent_id == pid
        assert c1.stage == "ready"
        assert c2.depends_on == [c1.id]
        package = json.loads(c1.context_package)
        assert package["parent"]["id"] == pid
        assert package["contracts"]["boundaries"]["allowed_files"] == ["c1.py"]


def test_plan_item_uncovered(monkeypatch):
    """T25：plan item 未被任何子任务覆盖。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    payload = _payload(children=[_child("C1", "P1")])
    r = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "decompose.plan_item_uncovered" in {e["code"] for e in body["errors"]}
    assert "P2" in body["plan_coverage"]["missing"]


def test_cycle(monkeypatch):
    """T26：子任务依赖成环。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    payload = _payload(children=[
        _child("C1", "P1", depends_on_refs=["C2"]),
        _child("C2", "P2", depends_on_refs=["C1"]),
    ])
    r = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    assert r.status_code == 200
    assert "decompose.cycle" in {e["code"] for e in r.json()["errors"]}


def test_missing_acceptance_deliverable_verification(monkeypatch):
    """T27：缺验收/交付物/验证一次列全。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    bad = _child("C1", "P1")
    bad["acceptance_criteria"] = []
    bad["deliverables"] = []
    bad["verification"] = ""
    payload = _payload(children=[bad, _child("C2", "P2")])
    r = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    codes = {e["code"] for e in r.json()["errors"]}
    assert {"decompose.missing_acceptance",
            "decompose.missing_deliverable",
            "decompose.missing_verification"} <= codes


def test_contract_unknown_input(monkeypatch):
    """T35：输入引用不存在的输出。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    c2 = _child("C2", "P2", depends_on_refs=["C1"])
    c2["contracts"]["inputs"] = [
        {"from_ref": "C1", "artifact": "schema", "contract": "x", "required": True}
    ]
    payload = _payload(children=[_child("C1", "P1"), c2])
    r = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    assert "decompose.contract_unknown_input" in {e["code"] for e in r.json()["errors"]}


def test_contract_duplicate_output(monkeypatch):
    """T41：同一 (from_ref, artifact) 重复定义输出。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    c1 = _child("C1", "P1")
    c2 = _child("C2", "P2", depends_on_refs=["C1"])
    output = {"from_ref": "C1", "artifact": "schema", "path": "c1.py",
              "contract": "schema v1", "consumers": ["C2"]}
    c1["contracts"]["outputs"] = [output]
    c2["contracts"]["outputs"] = [copy.deepcopy(output)]
    payload = _payload(children=[c1, c2])
    r = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    assert "decompose.contract_duplicate" in {e["code"] for e in r.json()["errors"]}


def test_high_risk_requires_override(monkeypatch):
    """T40：高风险路径声明 M 且无 override_reason。"""
    _enable(monkeypatch)
    monkeypatch.setenv("MIO_DECOMPOSE_REQUIRED", "L")
    pid = _mk_parent(complexity="M", description="数据库迁移与权限改造")
    payload = _payload()
    r = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    assert "decompose.complexity_override_required" in {
        e["code"] for e in r.json()["errors"]}
    assert r.json()["complexity"]["declared"] == "M"

    payload["complexity_override_reason"] = "只改一个迁移文件"
    r2 = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    assert r2.json()["ok"] is True


def test_l_requires_docs(monkeypatch):
    """T23：L 缺 spec 返回 doc_required；补 spec 后通过。"""
    _enable(monkeypatch)
    pid = _mk_parent(complexity="L", doc_paths={
        "requirement": "docs/requirement.md",
        "plan": "docs/plan.md",
    })
    with Session(engine) as db:
        parent = db.get(Task, pid)
        parent.doc_statuses = {"plan": {"state": "approved"}}
        db.add(parent)
        db.commit()
    payload = _payload()
    r = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    assert "decompose.doc_required" in {e["code"] for e in r.json()["errors"]}

    with Session(engine) as db:
        parent = db.get(Task, pid)
        parent.doc_paths = {**parent.doc_paths, "spec": "docs/spec.md"}
        db.add(parent)
        db.commit()
    r2 = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload)
    assert r2.json()["ok"] is True


def test_direct_child_create_forbidden(monkeypatch):
    """T30：P2b 起直接 POST /tasks 带 parent_id 返回 400。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    r = client.post("/api/v1/tasks", json={"title": "child", "parent_id": pid})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "decompose.child_create_forbidden"


def test_child_cannot_be_decomposed_again(monkeypatch):
    """T34：子任务再拆返回 depth_exceeded。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    payload = _payload()
    applied = client.post(f"/api/v1/tasks/{pid}/decompose/apply", json=payload).json()
    child_id = applied["ref_map"]["C1"]
    r = client.post(f"/api/v1/tasks/{child_id}/decompose/apply", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "decompose.depth_exceeded"


def test_parent_claim_children_incomplete(monkeypatch):
    """T32：子任务未完成时显式 claim 父任务 409。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    client.post(f"/api/v1/tasks/{pid}/decompose/apply", json=_payload())
    r = client.post("/api/v1/tasks/claim", params={"agent": "p2b-worker", "task_id": pid})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "decompose.children_incomplete"


def test_apply_token_stale(monkeypatch):
    """T38：preview 后请求体变化，旧 token 返回 preview_stale。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    payload = _payload()
    token = client.post(f"/api/v1/tasks/{pid}/decompose/preview",
                        json=payload).json()["apply_token"]
    payload["children"][0]["title"] = "改过的标题"
    r = client.post(f"/api/v1/tasks/{pid}/decompose/apply",
                    json={**payload, "apply_token": token})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "decompose.preview_stale"


def test_m_declared_without_plan_is_warning(monkeypatch):
    """T22：M 无 plan 只 warning，不阻断。"""
    _enable(monkeypatch)
    pid = _mk_parent(complexity="M")
    r = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=_payload())
    body = r.json()
    assert body["ok"] is True
    assert "decompose.plan_recommended" in {w["code"] for w in body["warnings"]}


def test_s_declared_with_many_files_is_warning(monkeypatch):
    """T24：声明 S 但 files=10 只提示 M，声明不变。"""
    _enable(monkeypatch)
    pid = _mk_parent(complexity="S")
    c1 = _child("C1", "P1")
    c1["files"] = [f"f{i}.py" for i in range(10)]
    c1["contracts"]["boundaries"]["allowed_files"] = c1["files"]
    payload = _payload(children=[c1, _child("C2", "P2")])
    body = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload).json()
    assert body["ok"] is True
    assert body["complexity"]["declared"] == "S"
    assert body["complexity"]["suggested"] in ("M", "L")
    assert "decompose.complexity_signal" in {w["code"] for w in body["warnings"]}


def test_apply_rolls_back_on_failure(monkeypatch):
    """apply 原子性：第 2 个子任务构造失败时不留下任何子任务。"""
    _enable(monkeypatch)
    pid = _mk_parent()
    payload = _payload()
    real = dec._child_context
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("inject failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(dec, "_child_context", boom)
    with Session(engine) as db:
        parent = db.get(Task, pid)
        result = dec.validate_decomposition(payload, parent)
        assert result.has_errors is False
        token = dec.make_apply_token(pid, payload)
        with pytest.raises(RuntimeError):
            dec.apply_decomposition(db, parent, payload, result, token)
    with Session(engine) as db:
        assert db.exec(select(Task).where(Task.parent_id == pid)).first() is None
        parent = db.get(Task, pid)
        assert parent.role == "leaf"
        assert not parent.decomposition


def test_stage_off_returns_404(monkeypatch):
    """T39：开关 off 时 preview/apply 404，普通建单不受影响。"""
    monkeypatch.delenv("MIO_DECOMPOSE_STAGE", raising=False)
    pid = _mk_parent()
    assert client.post(f"/api/v1/tasks/{pid}/decompose/preview",
                       json=_payload()).status_code == 404
    assert client.post(f"/api/v1/tasks/{pid}/decompose/apply",
                       json=_payload()).status_code == 404


def test_context_soft_budget_warns(monkeypatch, tmp_path):
    """T28: soft budget warns, apply still succeeds."""
    _enable(monkeypatch, "context")
    monkeypatch.setenv("MIO_SUBTASK_CONTEXT_SOFT", "10")
    monkeypatch.setenv("MIO_SUBTASK_CONTEXT_HARD", "100000")
    _write_spec(tmp_path)
    pid = _mk_parent(workspace=str(tmp_path))
    child = _child("C1", "P1")
    child["documents"] = [{"kind": "spec", "path": "docs/spec.md",
                           "sections": ["2.3"]}]
    payload = _payload(children=[child, _child("C2", "P2")])
    body = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload).json()
    assert body["ok"] is True
    assert body["context_estimates"]["C1"]["over_soft"] is True
    assert "decompose.context_soft_budget" in {w["code"] for w in body["warnings"]}
    applied = client.post(f"/api/v1/tasks/{pid}/decompose/apply", json=payload)
    assert applied.status_code == 200, applied.text


def test_context_hard_cap_blocks_apply(monkeypatch, tmp_path):
    """T28: hard cap blocks apply with 422."""
    _enable(monkeypatch, "context")
    monkeypatch.setenv("MIO_SUBTASK_CONTEXT_SOFT", "1")
    monkeypatch.setenv("MIO_SUBTASK_CONTEXT_HARD", "10")
    _write_spec(tmp_path)
    pid = _mk_parent(workspace=str(tmp_path))
    child = _child("C1", "P1")
    child["documents"] = [{"kind": "spec", "path": "docs/spec.md",
                           "sections": ["2.3"]}]
    payload = _payload(children=[child, _child("C2", "P2")])
    body = client.post(f"/api/v1/tasks/{pid}/decompose/preview", json=payload).json()
    assert body["ok"] is False
    assert "decompose.context_hard_cap" in {e["code"] for e in body["errors"]}
    applied = client.post(f"/api/v1/tasks/{pid}/decompose/apply", json=payload)
    assert applied.status_code == 422
    assert "decompose.context_hard_cap" in _codes(applied)


def test_claim_returns_package_sections_only(monkeypatch, tmp_path):
    """T31: child claim inlines only context_package sections."""
    _enable(monkeypatch, "context")
    _write_spec(tmp_path)
    pid = _mk_parent(workspace=str(tmp_path))
    child = _child("C1", "P1")
    child["documents"] = [{"kind": "spec", "path": "docs/spec.md",
                           "sections": ["2.3"]}]
    payload = _payload(children=[child, _child("C2", "P2")])
    applied = client.post(f"/api/v1/tasks/{pid}/decompose/apply", json=payload)
    assert applied.status_code == 200, applied.text
    child_id = applied.json()["ref_map"]["C1"]

    claimed = client.post("/api/v1/tasks/claim",
                          params={"agent": "p2c-worker", "task_id": child_id})
    assert claimed.status_code == 200, claimed.text
    body = claimed.json()
    assert body["package_only"] is True
    assert body["context_package"]["parent"]["id"] == pid
    assert body["context_package"]["estimate"]["tokens"] > 0
    doc = body["documents"]["spec"]
    assert doc["sections"] == ["2.3"]
    assert "SECTION_ONLY" in doc["content"]
    assert "FULL_ONLY" not in doc["content"]
    assert "OTHER_ONLY" not in doc["content"]
