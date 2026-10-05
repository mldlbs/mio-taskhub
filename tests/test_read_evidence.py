# tests/test_read_evidence.py
"""Read Evidence：读取留痕 + submit 前置门控。

覆盖：
- 指纹 / FR 抽取 / required kinds 纯逻辑
- record_read upsert + check_read_gate（missing → stale → passed）
- claim 内联上下文（不等于已读）
- API 端到端：未读 submit → 422；读取后 submit → 200；文档改动后 → 422 stale
"""
import pytest
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task
from mio_taskhub import read_evidence as re_mod
from sqlmodel import Session, select


client = TestClient(app)


# ── 纯逻辑 ────────────────────────────────────────────────────────────────

def test_file_fingerprint_stable_and_changes(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("hello", encoding="utf-8")
    fp1 = re_mod.file_fingerprint(f)
    assert fp1.startswith("sha256:")
    assert re_mod.file_fingerprint(f) == fp1
    f.write_text("hello!", encoding="utf-8")
    assert re_mod.file_fingerprint(f) != fp1
    assert re_mod.file_fingerprint(tmp_path / "nope.md") == ""


def test_extract_fr_ids():
    assert re_mod.extract_fr_ids("见 FR-2、FR-1 与 fr-10") == ["FR-1", "FR-2", "FR-10"]
    assert re_mod.extract_fr_ids("no refs") == []


def test_required_read_kinds():
    t = Task(title="x", doc_paths={"spec": "s.md", "api": "a.md", "plan": "p.md"})
    # 默认要求 spec/api/requirement；plan 不在读门控内
    assert re_mod.required_read_kinds(t) == ["spec", "api"]
    t2 = Task(title="y", doc_paths={})
    assert re_mod.required_read_kinds(t2) == []


def test_gate_kinds_env_override(monkeypatch):
    monkeypatch.setenv("MIO_READ_GATE_KINDS", "spec,plan")
    assert re_mod.gate_kinds() == ["spec", "plan"]


# ── record + gate（DB） ───────────────────────────────────────────────────

def _mk_task(tmp_path, kinds=("spec", "api", "requirement")):
    tmp_path.mkdir(parents=True, exist_ok=True)
    doc_paths = {}
    for k in kinds:
        (tmp_path / f"{k}.md").write_text(f"# {k}\nFR-1 FR-2", encoding="utf-8")
        doc_paths[k] = f"{k}.md"
    t = Task(id="t1", title="doc task", workspace=str(tmp_path), doc_paths=doc_paths)
    with Session(engine) as db:
        db.add(t)
        db.commit()
    return t


def test_gate_missing_then_pass(tmp_path, monkeypatch):
    monkeypatch.delenv("MIO_READ_GATE", raising=False)
    t = _mk_task(tmp_path)
    with Session(engine) as db:
        task = db.get(Task, "t1")
        g = re_mod.check_read_gate(db, task, "run1")
        assert g["enforced"] is True and g["passed"] is False
        assert set(g["missing"]) == {"spec", "api", "requirement"}

        re_mod.record_read(db, task, "spec", "run1")
        re_mod.record_read(db, task, "api", "run1")
        re_mod.record_read(db, task, "requirement", "run1")
        g2 = re_mod.check_read_gate(db, task, "run1")
        assert g2["passed"] is True and g2["missing"] == [] and g2["stale"] == []


def test_gate_stale_after_doc_change(tmp_path, monkeypatch):
    monkeypatch.delenv("MIO_READ_GATE", raising=False)
    t = _mk_task(tmp_path, kinds=("spec",))
    with Session(engine) as db:
        task = db.get(Task, "t1")
        re_mod.record_read(db, task, "spec", "run2")
        assert re_mod.check_read_gate(db, task, "run2")["passed"] is True
        (tmp_path / "spec.md").write_text("# spec v2", encoding="utf-8")
        g = re_mod.check_read_gate(db, task, "run2")
        assert g["passed"] is False and g["stale"] == ["spec"]


def test_gate_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_READ_GATE", "0")
    _mk_task(tmp_path, kinds=("spec",))
    with Session(engine) as db:
        task = db.get(Task, "t1")
        g = re_mod.check_read_gate(db, task, "run3")
        assert g["enforced"] is False


def test_record_read_is_idempotent(tmp_path):
    _mk_task(tmp_path, kinds=("spec",))
    with Session(engine) as db:
        task = db.get(Task, "t1")
        re_mod.record_read(db, task, "spec", "runX")
        re_mod.record_read(db, task, "spec", "runX")
        rows = db.exec(select(re_mod.ReadEvidence)
                       .where(re_mod.ReadEvidence.run_id == "runX")).all()
        assert len(rows) == 1


def test_build_claim_context(tmp_path):
    _mk_task(tmp_path)
    with Session(engine) as db:
        task = db.get(Task, "t1")
        ctx = re_mod.build_claim_context(task)
        assert ctx["branch"] == "task-t1"
        assert set(ctx["required_reads"]) == {"spec", "api", "requirement"}
        assert ctx["required_fr"] == ["FR-1", "FR-2"]
        assert "content" in ctx["documents"]["spec"]
        assert "不等于已读" in ctx["note"] or "不构成已读" in ctx["note"]


# ── API 端到端 ─────────────────────────────────────────────────────────────

def _make_ready_task(tmp_path):
    (tmp_path / "spec.md").write_text("# spec\nFR-1", encoding="utf-8")
    (tmp_path / "api.md").write_text("# api\nFR-1", encoding="utf-8")
    (tmp_path / "req.md").write_text("# requirement\nFR-1 FR-2", encoding="utf-8")
    r = client.post("/api/v1/tasks", json={
        "title": "ReadGate E2E", "stage": "ready", "workspace": str(tmp_path),
        "doc_paths": {"spec": "spec.md", "api": "api.md", "requirement": "req.md"},
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_claim_inlines_context_then_submit_gated(tmp_path, monkeypatch):
    monkeypatch.delenv("MIO_READ_GATE", raising=False)
    tid = _make_ready_task(tmp_path)
    client.post("/api/v1/agents/register", json={"name": "rg-agent"})

    claim = client.post("/api/v1/tasks/claim", params={"agent": "rg-agent"}).json()
    assert claim["task_id"] == tid
    assert claim["branch"] == f"task-{tid}"
    assert set(claim["required_reads"]) == {"spec", "api", "requirement"}
    assert claim["required_fr"] == ["FR-1", "FR-2"]
    assert "content" in claim["documents"]["requirement"]
    rid = claim["id"]

    # 未读 → submit 成功被门控拦截
    bad = client.post(f"/api/v1/runs/{rid}/result",
                      json={"success": True, "result": "done"})
    assert bad.status_code == 422

    # read_status 反映 missing
    st = client.get(f"/api/v1/runs/{rid}/read-evidence").json()
    assert st["passed"] is False and set(st["missing"]) == {"spec", "api", "requirement"}

    # 带 run_id 读取 → 留痕
    for kind in ("spec", "api", "requirement"):
        d = client.get(f"/api/v1/tasks/{tid}/doc",
                       params={"kind": kind, "run_id": rid, "agent": "rg-agent"}).json()
        assert d["read_evidence"]["kind"] == kind
        assert d["read_evidence"]["run_id"] == rid

    st2 = client.get(f"/api/v1/runs/{rid}/read-evidence").json()
    assert st2["passed"] is True

    ok = client.post(f"/api/v1/runs/{rid}/result",
                     json={"success": True, "result": "done"})
    assert ok.status_code == 200
    assert ok.json()["task_state"] == "completed"


def test_submit_gate_stale_after_change(tmp_path, monkeypatch):
    monkeypatch.delenv("MIO_READ_GATE", raising=False)
    tid = _make_ready_task(tmp_path)
    client.post("/api/v1/agents/register", json={"name": "rg-agent2"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "rg-agent2"}).json()
    rid = claim["id"]
    for kind in claim["required_reads"]:
        client.get(f"/api/v1/tasks/{tid}/doc", params={"kind": kind, "run_id": rid})

    (tmp_path / "api.md").write_text("# api v2", encoding="utf-8")
    r = client.post(f"/api/v1/runs/{rid}/result", json={"success": True, "result": "x"})
    assert r.status_code == 422
    assert "api" in str(r.json())


def test_submit_failure_not_gated(tmp_path, monkeypatch):
    monkeypatch.delenv("MIO_READ_GATE", raising=False)
    _make_ready_task(tmp_path)
    client.post("/api/v1/agents/register", json={"name": "rg-agent3"})
    claim = client.post("/api/v1/tasks/claim", params={"agent": "rg-agent3"}).json()
    rid = claim["id"]
    r = client.post(f"/api/v1/runs/{rid}/result", json={"success": False, "result": "boom"})
    # 失败提交不受读门控限制（可能重试/失败，但不是 422）
    assert r.status_code != 422
