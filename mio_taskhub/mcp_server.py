#!/usr/bin/env python3
"""MCP server for mio-taskhub.

Exposes the local task hub as MCP tools so any MCP-capable agent
(opencode, claude code, codex, hermes, workbuddy, ...) can register,
claim tasks, send heartbeats and submit results as native tool calls.

Run:    python -m mio_taskhub.mcp_server
Config: MIO_TASKHUB_URL (default http://127.0.0.1:8080/api/v1)
"""

import json
import os
from typing import Optional
import httpx
from pydantic import Field
from mcp.server.fastmcp import FastMCP

HUB_URL = os.environ.get("MIO_TASKHUB_URL", "http://127.0.0.1:8080/api/v1")
TIMEOUT = 15.0

_headers = {}
_token = os.environ.get("MIO_TASKHUB_TOKEN", "")
if _token:
    _headers["Authorization"] = f"Bearer {_token}"
_client = httpx.AsyncClient(timeout=TIMEOUT, headers=_headers)

mcp = FastMCP(
    "mio_taskhub_mcp",
    instructions=(
        "mio-taskhub 是一个本地跨 agent 任务中心。"
        "Agent 通过此服务注册、领取任务、发送心跳并提交结果。"
        "典型流程：taskhub_register 注册 → taskhub_claim 领取 → "
        "taskhub_heartbeat 心跳 → taskhub_submit_result 提交结果。"
    ),
)


async def _request(method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> dict:
    """调用 hub HTTP API，统一处理错误。"""
    try:
        resp = await _client.request(method, f"{HUB_URL}{path}", params=params, json=body)
        if resp.status_code == 204:
            return {}
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        return {"error": f"无法连接 mio-taskhub 服务（{HUB_URL}）。请先启动：python -m uvicorn mio_taskhub.main:app --port 8080"}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except httpx.HTTPError as e:
        return {"error": f"请求失败: {e}"}


def _fmt(data: dict, pretty: bool = True) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2 if pretty else None)


@mcp.tool(
    name="taskhub_register",
    title="注册 Agent 到任务中心",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def taskhub_register(
    name: str = Field(description="Agent 名称，如 opencode / claude-code / codex / hermes", min_length=1, max_length=64),
    agent_type: str = Field(default="cli", description="Agent 类型标签", max_length=32),
) -> str:
    """将当前 agent 注册到 mio-taskhub，使其在 Web 看板显示为在线。

    重复注册会刷新在线状态，幂等安全。所有 agent 执行任务前应先注册。

    Args:
        name: agent 名称（唯一）
        agent_type: 类型标签，默认 cli

    Returns:
        JSON: {"name": "opencode", "status": "online"} 或错误信息
    """
    data = await _request("POST", "/agents/register", body={"name": name, "agent_type": agent_type})
    return _fmt(data)


@mcp.tool(
    name="taskhub_claim",
    title="领取一个任务",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def taskhub_claim(
    agent: str = Field(description="当前 agent 名称，需先注册", min_length=1, max_length=64),
    agent_type: Optional[str] = Field(default=None, description="若设置，只领取匹配该类型的任务", max_length=32),
    project: Optional[str] = Field(default=None, description="关联项目名", max_length=200),
    workspace: Optional[str] = Field(default=None, description="工作区根路径", max_length=500),
    files: Optional[str] = Field(default=None, description="逗号分隔的文件路径列表", max_length=2000),
) -> str:
    """按优先级 + FIFO 领取一个排队任务，并返回 Run 上下文（含任务详情）。

    若该 agent 已有进行中的 run，会返回同一个 run（幂等）。无可用任务时
    返回空结果。领取后应执行任务，过程中发送 taskhub_heartbeat，
    完成后调用 taskhub_submit_result。

    Args:
        agent: 当前 agent 名称
        agent_type: 可选，只领取匹配该类型的任务

    Returns:
        JSON: {"id": run_id, "task_id": ..., "task": {...详情...}, "state": "claimed"}
    """
    query = {"agent": agent, "agent_type": agent_type}
    if project: query["project"] = project
    if workspace: query["workspace"] = workspace
    if files: query["files"] = files
    claim = await _request("POST", "/tasks/claim", params=query)
    if "error" in claim:
        return _fmt(claim)
    if not claim.get("id"):
        return _fmt({"message": "当前没有可领取的任务", "available": False})
    detail = await _request("GET", f"/tasks/{claim['task_id']}")
    return _fmt({**claim, "task": detail if "error" not in detail else {}})


@mcp.tool(
    name="taskhub_heartbeat",
    title="发送任务心跳",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def taskhub_heartbeat(
    run_id: str = Field(description="Run 唯一标识（claim 返回的 id）", min_length=1),
    progress: int = Field(default=50, description="进度百分比 0-100", ge=0, le=100),
    checkpoint: Optional[str] = Field(default=None, description="阶段检查点描述", max_length=500),
) -> str:
    """更新 run 状态为 running 并上报进度（0-100）。

    执行任务期间定期调用，避免被判定超时。超时任务会被重置回排队并重试。

    Args:
        run_id: claim 返回的 run id
        progress: 进度百分比
        checkpoint: 可选阶段描述

    Returns:
        JSON: {"id": ..., "state": "running", "progress": 50}
    """
    body = {"progress": progress}
    if checkpoint is not None:
        body["checkpoint"] = checkpoint
    data = await _request("POST", f"/runs/{run_id}/heartbeat", body=body)
    return _fmt(data)


@mcp.tool(
    name="taskhub_submit_result",
    title="提交任务执行结果",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def taskhub_submit_result(
    run_id: str = Field(description="Run 唯一标识（claim 返回的 id）", min_length=1),
    success: bool = Field(default=True, description="是否成功"),
    result: str = Field(default="", description="结果描述 / 产出摘要", max_length=4000),
    exit_code: Optional[int] = Field(default=None, description="退出码（默认 0 成功 / 1 失败）"),
) -> str:
    """提交 run 的最终结果（成功/失败）。

    成功后任务标记 completed；失败时若未超最大重试次数会进入 retrying
    并重新排队，否则标记 failed。

    Args:
        run_id: claim 返回的 run id
        success: 是否成功
        result: 结果描述 / 产出摘要
        exit_code: 可选退出码

    Returns:
        JSON: {"id": ..., "state": "finished", "result": "..."}
    """
    body = {"success": success, "result": result}
    if exit_code is not None:
        body["exit_code"] = exit_code
    data = await _request("POST", f"/runs/{run_id}/result", body=body)
    return _fmt(data)


@mcp.tool(
    name="taskhub_list_tasks",
    title="列出任务",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def taskhub_list_tasks(
    state: Optional[str] = Field(default=None, description="按状态过滤：queued/claimed/running/retrying/completed/failed"),
    agent_type: Optional[str] = Field(default=None, description="只显示匹配该 agent 类型的任务"),
) -> str:
    """列出任务看板，可按状态 / 目标 agent 类型过滤。用于了解待办、进行中和历史任务。

    Args:
        state: 可选状态过滤
        agent_type: 可选目标 agent 类型过滤

    Returns:
        JSON: {"count": N, "tasks": [...]}
    """
    query = {}
    if state:
        query["state"] = state
    if agent_type:
        query["agent_type"] = agent_type
    data = await _request("GET", "/tasks", params=query)
    if isinstance(data, list):
        return _fmt({"count": len(data), "tasks": data})
    return _fmt(data)


@mcp.tool(
    name="taskhub_get_task",
    title="查看任务详情",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def taskhub_get_task(
    task_id: str = Field(description="任务唯一标识", min_length=1),
) -> str:
    """查看单个任务的完整详情（标题、描述、优先级、依赖、重试次数等）。

    Args:
        task_id: 任务唯一标识

    Returns:
        JSON: 任务完整字段
    """
    data = await _request("GET", f"/tasks/{task_id}")
    return _fmt(data)


@mcp.tool(
    name="taskhub_create_task",
    title="创建任务",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def taskhub_create_task(
    title: str = Field(description="任务标题", min_length=1, max_length=200),
    description: str = Field(default="", description="任务详细描述", max_length=4000),
    target_agent_type: Optional[str] = Field(default=None, description="指定可执行的 agent 类型，为空表示任意"),
    priority: int = Field(default=0, description="优先级 0-3，越大越优先", ge=0, le=3),
    est_duration_min: int = Field(default=30, description="预估耗时（分钟）", ge=1, le=1440),
    depends_on: Optional[str] = Field(default=None, description="前置任务 id"),
    max_retries: int = Field(default=3, description="最大重试次数", ge=0, le=10),
    acceptance_criteria: str = Field(default="", description="验收标准 / 完成定义"),
    due_at: Optional[str] = Field(default=None, description="截止时间 ISO 格式"),
    labels: Optional[list] = Field(default=None, description="自定义状态标签列表"),
    project: str = Field(default="", description="关联项目名"),
    workspace: str = Field(default="", description="工作区根路径"),
    files: Optional[list] = Field(default=None, description="文件路径列表（相对工作区）"),
    deliverables: Optional[list] = Field(default=None, description="预期产出物路径列表"),
    stage: str = Field(default="brainstorming", description="研发阶段（brainstorming/design/planning/ready/implementing/review/done），ready 才可被领取"),
) -> str:
    """向任务中心提交一个新任务，供各 agent 领取执行。

    Args:
        title: 标题
        description: 详细描述
        target_agent_type: 指定执行者类型，为空表示任意
        priority: 0-3 优先级
        est_duration_min: 预估耗时分钟数
        depends_on: 前置任务 id
        max_retries: 最大重试次数
        acceptance_criteria: 验收标准 / 完成定义
        due_at: 截止时间 ISO 格式
        labels: 自定义状态标签列表
        project: 关联项目名
        workspace: 工作区根路径
        files: 文件路径列表（相对工作区）
        deliverables: 预期产出物路径列表

    Returns:
        JSON: {"id":..., "title":..., "state": "queued", ...}
    """
    body = {
        "title": title,
        "description": description,
        "target_agent_type": target_agent_type,
        "priority": priority,
        "est_duration_min": est_duration_min,
        "depends_on": depends_on,
        "max_retries": max_retries,
        "acceptance_criteria": acceptance_criteria,
        "due_at": due_at,
        "labels": labels,
        "project": project,
        "workspace": workspace,
        "files": files,
        "deliverables": deliverables,
        "stage": stage,
    }
    data = await _request("POST", "/tasks", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_update_task", title="更新任务细节", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_update_task(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    title: Optional[str] = Field(default=None, description="标题"),
    description: Optional[str] = Field(default=None, description="描述"),
    acceptance_criteria: Optional[str] = Field(default=None, description="验收标准"),
    due_at: Optional[str] = Field(default=None, description="截止时间 ISO 格式"),
    labels: Optional[list] = Field(default=None, description="自定义状态标签列表"),
    project: Optional[str] = Field(default=None, description="项目名"),
    workspace: Optional[str] = Field(default=None, description="工作区路径"),
    files: Optional[list] = Field(default=None, description="文件路径列表"),
    deliverables: Optional[list] = Field(default=None, description="产出物路径列表"),
) -> str:
    """更新任务细节字段。仅传需要修改的字段。
    Args:
        task_id: 任务唯一标识
        其余字段均为可选，传了才更新
    Returns:
        JSON: 更新后的任务完整详情
    """
    body = {k: v for k, v in {
        "title": title, "description": description, "acceptance_criteria": acceptance_criteria,
        "due_at": due_at, "labels": labels, "project": project, "workspace": workspace,
        "files": files, "deliverables": deliverables,
    }.items() if v is not None}
    data = await _request("PATCH", f"/tasks/{task_id}", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_add_subtask", title="添加子任务", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_add_subtask(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    title: str = Field(description="子任务标题", min_length=1, max_length=200),
    order: int = Field(default=0, description="排序号"),
    status: str = Field(default="pending", description="状态：pending/in_progress/done/blocked"),
) -> str:
    """为任务添加一个子任务/计划步骤。"""
    data = await _request("POST", f"/tasks/{task_id}/subtasks",
                          body={"title": title, "order": order, "status": status})
    return _fmt(data)


@mcp.tool(name="taskhub_update_subtask", title="更新子任务状态", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_update_subtask(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    subtask_id: str = Field(description="子任务唯一标识", min_length=1),
    status: Optional[str] = Field(default=None, description="状态：pending/in_progress/done/blocked"),
    title: Optional[str] = Field(default=None, description="标题"),
    order: Optional[int] = Field(default=None, description="排序号"),
) -> str:
    """更新子任务的标题/排序/状态。"""
    body = {k: v for k, v in {"title": title, "order": order, "status": status}.items() if v is not None}
    data = await _request("PATCH", f"/tasks/{task_id}/subtasks/{subtask_id}", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_add_gitref", title="关联 Git 引用", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_add_gitref(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    ref_type: str = Field(default="branch", description="类型：branch/commit/pr/tag"),
    value: str = Field(description="引用值，如分支名或 commit hash", min_length=1),
    note: str = Field(default="", description="备注"),
) -> str:
    """为任务关联一个 Git 引用（分支/commit/PR/tag）。"""
    data = await _request("POST", f"/tasks/{task_id}/gitrefs",
                          body={"ref_type": ref_type, "value": value, "note": note})
    return _fmt(data)


@mcp.tool(name="taskhub_add_history", title="追加执行历史", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_add_history(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    type: str = Field(description="事件类型：created/claimed/heartbeat/result/discussion/...", max_length=50),
    payload: Optional[str] = Field(default=None, description="JSON 字符串格式的附加数据"),
) -> str:
    """为任务追加一条执行历史事件。"""
    import json as _json
    try:
        p = _json.loads(payload) if payload else None
    except Exception:
        p = {"raw": payload}
    data = await _request("POST", f"/tasks/{task_id}/history", body={"type": type, "payload": p})
    return _fmt(data)


@mcp.tool(name="taskhub_add_discussion", title="回写讨论结果", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_add_discussion(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    topic: str = Field(description="讨论主题", min_length=1, max_length=200),
    agent: str = Field(default="", description="发起讨论的 agent 名称", max_length=64),
    summary: str = Field(default="", description="讨论摘要"),
    conclusions: str = Field(default="", description="结论（非空则标记 closed）"),
    messages: Optional[list] = Field(default=None, description="消息列表：[{author, role, content}]"),
) -> str:
    """agent 将任务拉回独立会话讨论后，回写摘要与结论到任务。

    有 conclusions 时讨论标记为 closed。消息列表可选。
    """
    body = {"topic": topic, "agent": agent, "summary": summary,
            "conclusions": conclusions, "messages": messages or []}
    data = await _request("POST", f"/tasks/{task_id}/discussions", body=body)
    return _fmt(data)


@mcp.tool(
    name="taskhub_cancel_task",
    title="取消任务",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def taskhub_cancel_task(
    task_id: str = Field(description="任务唯一标识", min_length=1),
) -> str:
    """取消一个排队中的任务（标记为 cancelled）。

    Args:
        task_id: 任务唯一标识

    Returns:
        JSON: {"ok": true, "state": "cancelled"}
    """
    data = await _request("DELETE", f"/tasks/{task_id}")
    return _fmt(data)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
