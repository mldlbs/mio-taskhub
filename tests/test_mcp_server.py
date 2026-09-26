# tests/test_mcp_server.py
import asyncio
import json
import httpx
import pytest
from mio_taskhub.main import app
from mio_taskhub import mcp_server
from mio_taskhub.db import init_db


@pytest.fixture()
def mcp_ctx():
    """Point the MCP server's HTTP client at the FastAPI app via ASGI transport."""
    transport = httpx.ASGITransport(app=app)
    original_client = mcp_server._client
    mcp_server._client = httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=5.0
    )
    yield mcp_server
    mcp_server._client = original_client


def _call(name, arguments):
    result = asyncio.run(mcp_server.mcp.call_tool(name, arguments))
    # FastMCP returns tuple (Sequence[ContentBlock], structured_dict) or dict
    if isinstance(result, dict):
        return result
    blocks = result[0] if isinstance(result, tuple) else result
    text = ""
    for block in blocks:
        if hasattr(block, "text"):
            text += block.text
    return json.loads(text) if text else {}


def test_register_tool(mcp_ctx):
    data = _call("taskhub_register", {"name": "mcp-agent", "agent_type": "test"})
    assert data["name"] == "mcp-agent"
    assert data["status"] in ("online", "registered")

def test_create_and_claim_tool(mcp_ctx):
    created = _call("taskhub_create_task", {
        "title": "MCP Task", "description": "from mcp", "priority": 2, "stage": "ready",
    })
    assert created["state"] == "queued"
    claimed = _call("taskhub_claim", {"agent": "mcp-agent"})
    assert claimed.get("id"), claimed
    assert claimed["task"]["title"] == "MCP Task"
    assert claimed["state"] == "claimed"


def test_claim_idempotent(mcp_ctx):
    _call("taskhub_create_task", {"title": "Idem", "stage": "ready"})
    r1 = _call("taskhub_claim", {"agent": "mcp-agent"})
    r2 = _call("taskhub_claim", {"agent": "mcp-agent"})
    assert r1["id"] == r2["id"]


def test_no_tasks(mcp_ctx):
    data = _call("taskhub_claim", {"agent": "mcp-agent"})
    assert data.get("available") is False


def test_heartbeat_tool(mcp_ctx):
    _call("taskhub_create_task", {"title": "HB", "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent"})
    rid = claim["id"]
    hb = _call("taskhub_heartbeat", {"run_id": rid, "progress": 50, "checkpoint": "step1"})
    assert hb["state"] == "running"
    assert hb["progress"] == 50


def test_submit_result_success(mcp_ctx):
    _call("taskhub_create_task", {"title": "Result", "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent"})
    rid = claim["id"]
    res = _call("taskhub_submit_result", {"run_id": rid, "success": True, "result": "done"})
    assert res["state"] == "finished"
    tasks = _call("taskhub_list_tasks", {})
    titles = {t["title"]: t["state"] for t in tasks["tasks"]}
    assert titles["Result"] == "completed"


def test_submit_result_failure_retries(mcp_ctx):
    _call("taskhub_create_task", {"title": "Retry", "max_retries": 3, "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent"})
    rid = claim["id"]
    res = _call("taskhub_submit_result", {"run_id": rid, "success": False, "result": "boom"})
    assert res["state"] == "finished"
    tasks = _call("taskhub_list_tasks", {})
    state = {t["title"]: t["state"] for t in tasks["tasks"]}["Retry"]
    assert state == "retrying"


def test_list_and_get_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "Detail", "description": "desc"})
    tid = created["id"]
    detail = _call("taskhub_get_task", {"task_id": tid})
    assert detail["description"] == "desc"
    lst = _call("taskhub_list_tasks", {"state": "queued"})
    assert any(t["id"] == tid for t in lst["tasks"])


def test_cancel_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "Cancel me"})
    res = _call("taskhub_cancel_task", {"task_id": created["id"]})
    assert res["state"] == "cancelled"


def test_create_task_rich_fields(mcp_ctx):
    created = _call("taskhub_create_task", {
        "title": "Rich create", "project": "p", "workspace": "/w",
        "files": ["a.py"], "labels": ["new"], "deliverables": ["r.md"],
        "acceptance_criteria": "AC", "due_at": "2026-12-31T23:59:59+00:00",
    })
    tid = created["id"]
    detail = _call("taskhub_get_task", {"task_id": tid})
    assert detail["project"] == "p"
    assert detail["workspace"] == "/w"
    assert detail["files"] == ["a.py"]
    assert detail["labels"] == ["new"]
    assert detail["deliverables"] == ["r.md"]
    assert detail["acceptance_criteria"] == "AC"
    assert detail["due_at"] is not None


def test_claim_with_context(mcp_ctx):
    _call("taskhub_create_task", {"title": "MCP Ctx", "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent", "project": "p",
                                    "workspace": "/w", "files": "a.py,b.py"})
    assert claim["task"]["project"] == "p"
    assert claim["task"]["files"] == ["a.py", "b.py"]


def test_update_task_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "Upd"})
    tid = created["id"]
    r = _call("taskhub_update_task", {"task_id": tid, "acceptance_criteria": "AC",
                                      "labels": ["blocked"], "project": "p"})
    assert r["acceptance_criteria"] == "AC"
    assert r["labels"] == ["blocked"]


def test_create_and_update_task_depends_on_array(mcp_ctx):
    parent = _call("taskhub_create_task", {"title": "DepParent"})
    created = _call("taskhub_create_task", {"title": "DepChild", "depends_on": [parent["id"]]})
    assert created["depends_on"] == [parent["id"]]
    r = _call("taskhub_update_task", {"task_id": created["id"], "depends_on": []})
    assert r["depends_on"] == []


def test_subtask_tools(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "Sub"})
    tid = created["id"]
    st = _call("taskhub_add_subtask", {"task_id": tid, "title": "s1", "order": 1})
    assert st["status"] == "pending"
    up = _call("taskhub_update_subtask", {"task_id": tid, "subtask_id": st["id"], "status": "done"})
    assert up["status"] == "done"
    detail = _call("taskhub_get_task", {"task_id": tid})
    assert len(detail["subtasks"]) == 1


def test_gitref_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "GitT"})
    tid = created["id"]
    g = _call("taskhub_add_gitref", {"task_id": tid, "ref_type": "branch", "value": "feat/x"})
    assert g["ref_type"] == "branch"
    detail = _call("taskhub_get_task", {"task_id": tid})
    assert detail["gitrefs"][0]["value"] == "feat/x"


def test_history_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "HistT"})
    tid = created["id"]
    h = _call("taskhub_add_history", {"task_id": tid, "type": "discussion"})
    assert h["type"] == "discussion"
    detail = _call("taskhub_get_task", {"task_id": tid})
    assert len(detail["history"]) == 1


def test_discussion_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "DiscT"})
    tid = created["id"]
    d = _call("taskhub_add_discussion", {
        "task_id": tid, "topic": "方案", "agent": "mcp-agent",
        "summary": "讨论", "conclusions": "用B",
        "messages": [{"author": "mcp-agent", "role": "assistant", "content": "建议B"}],
    })
    assert d["status"] == "closed"
    disc = _call("taskhub_get_task", {"task_id": tid})
    assert len(disc["discussions"]) == 1


def test_advance_stage_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "Stage", "stage": "brainstorming"})
    tid = created["id"]
    d = _call("taskhub_add_discussion", {"task_id": tid, "topic": "理解", "agent": "mcp-agent",
                                         "summary": "s", "conclusions": "c"})
    assert d["status"] == "closed"
    r = _call("taskhub_advance_stage", {"task_id": tid, "target_stage": "design",
                                        "spec_path": "docs/s.md"})
    assert r["stage"] == "design"
    r2 = _call("taskhub_advance_stage", {"task_id": tid, "target_stage": "planning",
                                         "plan_path": "docs/p.md"})
    assert r2["stage"] == "planning"
    r3 = _call("taskhub_advance_stage", {"task_id": tid, "target_stage": "ready"})
    assert r3["stage"] == "ready"
    # then claim works（领取即 ready→implementing）
    claim = _call("taskhub_claim", {"agent": "mcp-agent"})
    assert claim["task"]["stage"] == "implementing"

def test_advance_stage_missing_artifact(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "NoArt", "stage": "brainstorming"})
    tid = created["id"]
    _call("taskhub_add_discussion", {"task_id": tid, "topic": "t", "agent": "a"})
    r = _call("taskhub_advance_stage", {"task_id": tid, "target_stage": "design"})
    assert "error" in r or r == {}  # 422 surfaced as error dict or empty

def test_discussion_with_stage_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "DiscStage", "stage": "ready"})
    tid = created["id"]
    d = _call("taskhub_add_discussion", {"task_id": tid, "topic": "评审", "agent": "mcp-agent",
                                         "stage": "review", "conclusions": "通过"})
    assert d["stage"] == "review"


def test_status_tool_empty(mcp_ctx):
    data = _call("taskhub_status", {})
    assert data["counts"]["ready"] == 0
    assert data["ready_queue"] == []
    assert data["running"] == []
    assert data["next_steps"]


def test_status_tool_reflects_board(mcp_ctx):
    _call("taskhub_create_task", {"title": "ST", "stage": "ready", "priority": 2})
    data = _call("taskhub_status", {})
    assert data["counts"]["ready"] == 1
    assert data["ready_queue"][0]["title"] == "ST"
    assert data["ready_queue"][0]["priority"] == 2


def test_status_tool_agent_filter(mcp_ctx):
    _call("taskhub_create_task", {"title": "ST2", "stage": "ready"})
    _call("taskhub_claim", {"agent": "mcp-agent"})
    all_r = _call("taskhub_status", {})["running"]
    mine = _call("taskhub_status", {"agent": "mcp-agent"})["running"]
    assert len(all_r) == 1
    assert len(mine) == 1
    other = _call("taskhub_status", {"agent": "nobody"})["running"]
    assert other == []


def test_poll_events_tool(mcp_ctx):
    _call("taskhub_create_task", {"title": "PE"})
    r = _call("taskhub_poll_events", {"seq": 0})
    assert "next_seq" in r
    assert any(e["type"] == "task_created" for e in r["events"])


def test_poll_events_incremental(mcp_ctx):
    r1 = _call("taskhub_poll_events", {"seq": 0})
    _call("taskhub_create_task", {"title": "PE2"})
    r2 = _call("taskhub_poll_events", {"seq": r1["next_seq"]})
    assert r2["events"]
    assert all(e["seq"] > r1["next_seq"] for e in r2["events"])


def test_move_to_stage_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "MV", "stage": "brainstorming"})
    r = _call("taskhub_move_to_stage", {"task_id": created["id"], "target_stage": "review"})
    assert r["stage"] == "review"
    d = _call("taskhub_get_task", {"task_id": created["id"]})
    assert d["stage"] == "review"


def test_breakdown_idea_tool(mcp_ctx):
    created = _call("taskhub_add_idea", {"title": "BI"})
    iid = created["id"]
    r = _call("taskhub_breakdown_idea", {
        "idea_id": iid,
        "tasks": [{"title": "a", "ref": "a", "depends_on": []},
                  {"title": "b", "ref": "b", "depends_on": ["a"]}],
    })
    assert r["idea"]["status"] == "broken_down"
    assert len(r["tasks"]) == 2
    b = next(t for t in r["tasks"] if t["ref"] == "b")
    a = next(t for t in r["tasks"] if t["ref"] == "a")
    assert b["depends_on"] == [a["id"]]


def test_agent_heartbeat_tool(mcp_ctx):
    r = _call("taskhub_agent_heartbeat", {"name": "hbmcp"})
    assert r["status"] == "online"
    assert "last_heartbeat" in r


def test_update_idea_versioning_tool(mcp_ctx):
    iid = _call("taskhub_add_idea", {"title": "MCP需求"})["id"]
    d = _call("taskhub_update_idea", {
        "idea_id": iid,
        "description": "v2",
        "change_reason": "补充说明",
        "versioning": "full",
        "track_change": False,
    })
    assert d["version"] == 2
    d2 = _call("taskhub_update_idea", {
        "idea_id": iid,
        "description": "v3",
        "versioning": "history_only",
    })
    assert d2["version"] == 2
    d3 = _call("taskhub_update_idea", {"idea_id": iid, "description": "v4"})
    assert d3["version"] == 3   # 省略 versioning → 后端默认 full，版本递增


def test_review_idea_tools_flow(mcp_ctx):
    iid = _call("taskhub_add_idea", {"title": "MCP 评审"})["id"]
    ctx = _call("taskhub_review_idea", {"idea_id": iid})
    assert ctx["idea"]["title"] == "MCP 评审"
    assert "checklist" in ctx and "recommend_options" in ctx
    r = _call("taskhub_submit_review",
              {"idea_id": iid, "recommend": "ferment", "reasoning": "ok"})
    assert r["status"] == "fermenting"
    assert r["review_count"] == 1
    hist = _call("taskhub_idea_history", {"idea_id": iid})
    assert hist["count"] == 2


def test_review_idea_history_paged(mcp_ctx):
    iid = _call("taskhub_add_idea", {"title": "paged"})["id"]
    for _ in range(3):
        _call("taskhub_submit_review", {"idea_id": iid, "recommend": "nothing"})
    hist = _call("taskhub_idea_history", {"idea_id": iid, "page": 2, "page_size": 2})
    assert hist["count"] == 3
    assert len(hist["items"]) == 1


def test_list_and_read_documents_tools(mcp_ctx, tmp_path):
    """文档工具：列清单 + 按 kind 读正文（走 REST，不要求本机可访问文件）。"""
    ws = tmp_path / "mcp-ws"
    ws.mkdir()
    (ws / "review-report.md").write_text("# review body", encoding="utf-8")

    created = _call("taskhub_create_task", {
        "title": "MCP docs", "stage": "ready", "workspace": str(ws),
        "doc_paths": {"review": "review-report.md"},
    })
    tid = created["id"]

    listing = _call("taskhub_list_documents", {"task_id": tid})
    kinds = {d["kind"] for d in listing["documents"]}
    assert "review" in kinds

    doc = _call("taskhub_read_document", {"task_id": tid, "kind": "review"})
    assert doc["kind"] == "review"
    assert doc["content"].strip() == "# review body"


def test_update_task_doc_paths_tool(mcp_ctx, tmp_path):
    """update_task 可写 doc_paths，并与旧列同步。"""
    ws = tmp_path / "mcp-ws2"
    ws.mkdir()
    (ws / "s.md").write_text("# s", encoding="utf-8")

    tid = _call("taskhub_create_task", {"title": "MCP doc paths"})["id"]
    updated = _call("taskhub_update_task", {
        "task_id": tid, "doc_paths": {"spec": "s.md"},
    })
    assert updated["doc_paths"].get("spec") == "s.md"
    assert updated["spec_path"] == "s.md"


def test_write_document_tool_roundtrip(mcp_ctx, tmp_path):
    """agent 可直接产出文档：写入 → 落盘 → 读回。"""
    ws = tmp_path / "mcp-ws3"
    ws.mkdir()

    tid = _call("taskhub_create_task",
                {"title": "MCP write doc", "workspace": str(ws)})["id"]
    w = _call("taskhub_write_document", {
        "task_id": tid, "kind": "review", "content": "# 审查\n通过",
    })
    assert w["path"] == f"docs/taskhub/{tid}/review.md"
    assert w["created"] is True
    assert (ws / "docs" / "taskhub" / tid / "review.md").read_text(encoding="utf-8") == "# 审查\n通过"

    doc = _call("taskhub_read_document", {"task_id": tid, "kind": "review"})
    assert doc["content"] == "# 审查\n通过"


def test_write_document_tool_append_mode(mcp_ctx, tmp_path):
    """mode=append 可逐条累积（changelog / 审查记录场景）。"""
    ws = tmp_path / "mcp-ws4"
    ws.mkdir()
    tid = _call("taskhub_create_task",
                {"title": "MCP append", "workspace": str(ws)})["id"]

    _call("taskhub_write_document",
          {"task_id": tid, "kind": "changelog", "content": "- a", "mode": "append"})
    r = _call("taskhub_write_document",
              {"task_id": tid, "kind": "changelog", "content": "- b", "mode": "append"})
    assert r["mode"] == "append"
    assert r["created"] is False
    assert (ws / "docs" / "taskhub" / tid / "changelog.md").read_text(encoding="utf-8") == "- a\n- b"


def test_read_evidence_gate_via_mcp(mcp_ctx, tmp_path, monkeypatch):
    """MCP 链路：claim 内联 → 未读 submit 422 → read_document(run_id) → submit 通过。"""
    monkeypatch.delenv("MIO_READ_GATE", raising=False)
    ws = tmp_path / "mcp-readgate"
    ws.mkdir()
    (ws / "spec.md").write_text("# spec\nFR-1", encoding="utf-8")
    tid = _call("taskhub_create_task", {
        "title": "MCP read gate", "stage": "ready", "workspace": str(ws),
        "doc_paths": {"spec": "spec.md"},
    })["id"]

    claim = _call("taskhub_claim", {"agent": "mcp-rg"})
    rid = claim["id"]
    assert claim["task_id"] == tid
    assert claim["required_reads"] == ["spec"] and claim["branch"] == f"task-{tid}"

    blocked = _call("taskhub_submit_result", {"run_id": rid, "success": True, "result": "x"})
    assert "error" in blocked and "422" in blocked["error"]

    status = _call("taskhub_read_status", {"run_id": rid})
    assert status["passed"] is False and status["missing"] == ["spec"]

    _call("taskhub_read_document",
          {"task_id": tid, "kind": "spec", "run_id": rid, "agent": "mcp-rg"})
    assert _call("taskhub_read_status", {"run_id": rid})["passed"] is True

    ok = _call("taskhub_submit_result", {"run_id": rid, "success": True, "result": "done"})
    assert ok.get("task_state") == "completed"
