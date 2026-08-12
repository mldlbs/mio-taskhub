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
        "title": "MCP Task", "description": "from mcp", "priority": 2,
    })
    assert created["state"] == "queued"
    claimed = _call("taskhub_claim", {"agent": "mcp-agent"})
    assert claimed.get("id"), claimed
    assert claimed["task"]["title"] == "MCP Task"
    assert claimed["state"] == "claimed"


def test_claim_idempotent(mcp_ctx):
    _call("taskhub_create_task", {"title": "Idem"})
    r1 = _call("taskhub_claim", {"agent": "mcp-agent"})
    r2 = _call("taskhub_claim", {"agent": "mcp-agent"})
    assert r1["id"] == r2["id"]


def test_no_tasks(mcp_ctx):
    data = _call("taskhub_claim", {"agent": "mcp-agent"})
    assert data.get("available") is False


def test_heartbeat_tool(mcp_ctx):
    _call("taskhub_create_task", {"title": "HB"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent"})
    rid = claim["id"]
    hb = _call("taskhub_heartbeat", {"run_id": rid, "progress": 50, "checkpoint": "step1"})
    assert hb["state"] == "running"
    assert hb["progress"] == 50


def test_submit_result_success(mcp_ctx):
    _call("taskhub_create_task", {"title": "Result"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent"})
    rid = claim["id"]
    res = _call("taskhub_submit_result", {"run_id": rid, "success": True, "result": "done"})
    assert res["state"] == "finished"
    tasks = _call("taskhub_list_tasks", {})
    titles = {t["title"]: t["state"] for t in tasks["tasks"]}
    assert titles["Result"] == "completed"


def test_submit_result_failure_retries(mcp_ctx):
    _call("taskhub_create_task", {"title": "Retry", "max_retries": 3})
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
    _call("taskhub_create_task", {"title": "MCP Ctx"})
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
