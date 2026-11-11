# tests/test_mcp_server.py
import asyncio
import json
import httpx
import pytest
from mio_taskhub.main import app
from mio_taskhub import mcp_server
from mio_taskhub.db import init_db


@pytest.fixture()
def mcp_ctx(monkeypatch):
    """Point the MCP server's HTTP client at the FastAPI app via ASGI transport.

    放行 destructive（MIO_MCP_ALLOW_DESTRUCTIVE=1）：本文件测的是工具行为本身
    （含 cancel_task），不是风险门控——门控有独立测试 tests/test_mcp_risk_gate.py
    与 tests/test_destructive_guard.py。
    """
    monkeypatch.setenv("MIO_MCP_ALLOW_DESTRUCTIVE", "1")
    transport = httpx.ASGITransport(app=app)
    original_client = mcp_server._client
    # F-004：与 conftest 的 TestClient 口径一致——本文件的 MCP HTTP 客户端视为
    # 本机可信管理面，带 X-TaskHub-Admin: 1，工具行为用例才不会撞上管理面门。
    # F-004 第 3 步（destructive 下沉 + 签名收敛）已落地：生产 MCP 客户端只带 API
    # 凭据，taskhub_update_task 的执行边界参数已从签名移除（不再依赖 403 兜底）；
    # destructive 端点由服务端 destructive_guard 强制管理面凭据，无权限路径见
    # mcp_ctx_anon fixture 与 tests/test_destructive_guard.py。
    mcp_server._client = httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=5.0,
        headers={"X-TaskHub-Admin": "1"},
    )
    yield mcp_server
    mcp_server._client = original_client


@pytest.fixture()
def mcp_ctx_anon(monkeypatch):
    """生产口径的 MCP HTTP 客户端：只有 API 访问层，**不带任何管理面头**。

    用于验证：MCP 客户端门控放行（MIO_MCP_ALLOW_DESTRUCTIVE=1）后，destructive
    调用打到服务端会被 destructive_guard 以明确 HTTP 403 拒绝——env 开关不改变
    服务端判定。"""
    transport = httpx.ASGITransport(app=app)
    original_client = mcp_server._client
    mcp_server._client = httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=5.0)
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
    hb = _call("taskhub_heartbeat", {"run_id": rid, "agent": "mcp-agent", "progress": 50, "checkpoint": "step1"})
    assert hb["state"] == "running"
    assert hb["progress"] == 50


def test_submit_result_success(mcp_ctx):
    _call("taskhub_create_task", {"title": "Result", "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent"})
    rid = claim["id"]
    res = _call("taskhub_submit_result", {"run_id": rid, "agent": "mcp-agent", "success": True, "result": "done"})
    assert res["state"] == "finished"
    tasks = _call("taskhub_list_tasks", {})
    titles = {t["title"]: t["state"] for t in tasks["tasks"]}
    assert titles["Result"] == "completed"


def test_submit_result_failure_retries(mcp_ctx):
    _call("taskhub_create_task", {"title": "Retry", "max_retries": 3, "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent"})
    rid = claim["id"]
    res = _call("taskhub_submit_result", {"run_id": rid, "agent": "mcp-agent", "success": False, "result": "boom"})
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
    res = _call("taskhub_cancel_task", {
        "task_id": created["id"], "reason": "obsolete", "detail": "T0.6 透传验证",
    })
    assert res["state"] == "cancelled"
    assert res["reason"] == "obsolete"
    # F17/T0.6：MCP 取消的事件 payload 必须含 reason（非 null）
    from sqlmodel import Session, select
    from mio_taskhub.db import engine
    from mio_taskhub.models import Event
    with Session(engine) as s:
        ev = s.exec(select(Event).where(
            Event.type == "task_cancelled", Event.entity_id == created["id"]
        ).order_by(Event.id.desc())).first()
    assert ev is not None and ev.payload
    payload = json.loads(ev.payload)
    assert payload["reason"] == "obsolete"
    assert payload["detail"] == "T0.6 透传验证"


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


def _update_tool_props() -> set:
    """taskhub_update_task 的 FastMCP 入参 schema 属性集（签名收敛断言用）。"""
    tool = mcp_server.mcp._tool_manager._tools["taskhub_update_task"]
    return set(tool.parameters.get("properties", {}))


def test_update_task_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "Upd"})
    tid = created["id"]
    r = _call("taskhub_update_task", {"task_id": tid, "acceptance_criteria": "AC",
                                      "labels": ["blocked"], "project": "p"})
    assert r["acceptance_criteria"] == "AC"
    assert r["labels"] == ["blocked"]
    # F-004 第 3 步：执行边界字段已从工具签名移除（FastMCP 参数校验层面不接受）
    assert not {"workspace", "files", "deliverables", "depends_on",
                "target_agent_type"} & _update_tool_props()


def test_create_and_update_task_depends_on_array(mcp_ctx):
    """依赖创建走 taskhub_create_task 的 POST 路径（POST 不受字段级管理面校验）。

    taskhub_update_task 签名已不含 depends_on（F-004 第 3 步）：FastMCP 会忽略
    未登记的入参，body 不带该键 → PATCH 普通字段放行，依赖保持不变——
    「改执行边界须管理面凭据」在工具签名层就不可达，不再依赖服务端 403 兜底。
    """
    parent = _call("taskhub_create_task", {"title": "DepParent"})
    created = _call("taskhub_create_task", {"title": "DepChild", "depends_on": [parent["id"]]})
    assert created["depends_on"] == [parent["id"]]
    r = _call("taskhub_update_task", {"task_id": created["id"], "depends_on": []})
    assert r["depends_on"] == [parent["id"]]  # 未被改：工具签名里没有这个参数


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

    blocked = _call("taskhub_submit_result", {"run_id": rid, "agent": "mcp-rg", "success": True, "result": "x"})
    assert "error" in blocked and "422" in blocked["error"]

    status = _call("taskhub_read_status", {"run_id": rid})
    assert status["passed"] is False and status["missing"] == ["spec"]

    _call("taskhub_read_document",
          {"task_id": tid, "kind": "spec", "run_id": rid, "agent": "mcp-rg"})
    assert _call("taskhub_read_status", {"run_id": rid})["passed"] is True

    ok = _call("taskhub_submit_result", {"run_id": rid, "agent": "mcp-rg", "success": True, "result": "done"})
    assert ok.get("task_state") == "completed"


def test_claim_named_task_beats_relevance(mcp_ctx):
    """点名 claim（task_id）：队列存在更高优先级 ready 任务时，仍必须领到指定任务，
    不得被相关性挑选顶掉（trace claim-taskid-mcp-drop-20260927 回归）。"""
    _call("taskhub_create_task", {"title": "Decoy p3", "priority": 3, "stage": "ready"})
    target = _call("taskhub_create_task", {"title": "Target p0", "priority": 0, "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent", "task_id": target["id"]})
    assert claim.get("task_id") == target["id"], claim
    assert claim["task"]["title"] == "Target p0"


def test_claim_named_task_not_claimable_returns_error(mcp_ctx):
    """点名 claim 不可领取任务（已取消）→ 返回 error，绝不静默改领其他任务。"""
    created = _call("taskhub_create_task", {"title": "Cancelled target", "stage": "ready"})
    _call("taskhub_cancel_task", {"task_id": created["id"]})
    _call("taskhub_create_task", {"title": "Alternative p2", "priority": 2, "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent", "task_id": created["id"]})
    assert "error" in claim and "409" in claim["error"], claim


def test_claim_carries_lessons(mcp_ctx):
    """FR-2 / AC-2.1：MCP claim 响应里必须带 lessons（任务 28f8fceb）。

    agent 主要通过 MCP 接 taskhub，若lessons 只在 REST 存在，MCP 侧等于没有。
    这里用 ASGI transport 让 MCP 打到进程内 app（真实链路）。
    """
    ws = "/lessons-ws"
    # 造一条同 workspace 的已完成历史任务，含测试证据
    old = _call("taskhub_create_task", {"title": "历史任务", "workspace": ws,
                                        "project": "lp", "stage": "ready"})
    from mio_taskhub.db import Session as _S, engine
    from mio_taskhub.models import Task, TaskState, Run, RunState
    import uuid as _uuid
    with _S(engine) as s:
        t = s.get(Task, old["id"])
        t.state = TaskState.COMPLETED
        s.add(t)
        s.add(Run(id=str(_uuid.uuid4())[:8], task_id=t.id, agent_name="x",
                  state=RunState.FINISHED,
                  result="完成。测试：pytest tests/ -q 77 passed。"))
        s.commit()

    _call("taskhub_create_task", {"title": "新任务", "workspace": ws,
                                  "project": "lp", "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "lessons-agent", "workspace": ws,
                                    "project": "lp"})
    assert "lessons" in claim, sorted(claim.keys())
    ls = claim["lessons"]
    assert set(ls) == {"test_hints", "file_layout", "pitfalls", "source_count"}
    # 没有历史时不得报错
    assert isinstance(ls["test_hints"], list)


def test_claim_lessons_never_blocks(mcp_ctx):
    """AC-2.3：lessons 取值异常时 claim 仍应成功（软信息不得阻断主流程）。"""
    from mio_taskhub.api import task_lessons as tl

    def boom(*a, **kw):
        raise RuntimeError("lessons 炸了")

    orig = tl.recent_lessons
    tl.recent_lessons = boom
    try:
        _call("taskhub_create_task", {"title": "阻断验证", "workspace": "/boom-ws",
                                      "project": "bp", "stage": "ready"})
        claim = _call("taskhub_claim", {"agent": "boom-agent", "workspace": "/boom-ws",
                                        "project": "bp"})
    finally:
        tl.recent_lessons = orig
    assert claim.get("id"), claim
    # lessons 降级为空结构
    assert claim["lessons"]["source_count"] == 0


def test_admin_plane_guidance_in_mcp_texts():
    """F-004 收尾：MCP prompt 与工具描述必须如实告知管理面边界。

    生产 MCP 客户端只带 API 凭据、不带管理面头：agent 自助推文档状态会
    被服务端 403；执行边界字段已从 taskhub_update_task 签名移除（第 3 步）。
    若 prompt 还在指示 agent 去调 taskhub_set_doc_status（2026-11-11 修复前的
    写法），agent 会反复撞 403。
    """
    import pathlib
    src = pathlib.Path(mcp_server.__file__).read_text(encoding="utf-8")
    # prompt 不再指示 agent 自推文档状态
    assert "不要自行推进文档状态" in src
    assert "先用 taskhub_set_doc_status 推进其生命周期再编码" not in src
    # prompt 说明 destructive 门控下沉：env 开关只影响 MCP 客户端层
    assert "destructive 操作由人执行" in src
    assert "服务端不认" in src
    # 工具描述写明管理面边界、明确 403 语义与签名收敛
    assert "该动作属验收，需管理面凭据" in src
    assert "不在本工具签名里" in src


def test_destructive_env_only_relaxes_client_layer(mcp_ctx_anon, monkeypatch):
    """F-004 第 3 步核心断言：MIO_MCP_ALLOW_DESTRUCTIVE=1 放行的只是 MCP 客户端层，
    服务端 destructive 门控仍要求管理面凭据——生产口径（无管理面头）的 MCP 调用
    收到明确 HTTP 403，而不是客户端 block，也不是静默成功。"""
    monkeypatch.setenv("MIO_MCP_ALLOW_DESTRUCTIVE", "1")
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    created = _call("taskhub_create_task", {"title": "Gate me"})
    res = _call("taskhub_cancel_task", {"task_id": created["id"]})
    assert "error" in res, res
    assert "403" in res["error"], res
    assert "admin" in res["error"], res
    # 任务未被取消
    detail = _call("taskhub_get_task", {"task_id": created["id"]})
    assert detail["state"] != "cancelled"


def test_client_gate_still_blocks_destructive_by_default(mcp_ctx_anon, monkeypatch):
    """无 env 放行时，MCP 客户端门控依旧先拦（fail fast，不发网络请求）；
    拒绝文案如实指向管理面凭据，不再推荐无效的 env 逃生口。"""
    monkeypatch.delenv("MIO_MCP_ALLOW_DESTRUCTIVE", raising=False)
    res = _call("taskhub_cancel_task", {"task_id": "whatever"})
    assert res.get("blocked") is True, res
    assert "管理面凭据" in res.get("error", ""), res


def test_set_doc_status_surfaces_server_403(mcp_ctx_anon, monkeypatch):
    """set_doc_status：客户端放行后，agent 看到的是服务端明确 403（第 3 步
    desc 口径「admin.token_required / admin.local_confirmation_required」可对上）。"""
    monkeypatch.setenv("MIO_MCP_ALLOW_DESTRUCTIVE", "1")
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    res = _call("taskhub_set_doc_status",
                {"task_id": "deadbeef", "kind": "spec", "state": "approved"})
    assert "error" in res, res
    assert "403" in res["error"] and "admin.local_confirmation_required" in res["error"], res
