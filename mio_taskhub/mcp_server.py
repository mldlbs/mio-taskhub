#!/usr/bin/env python3
"""MCP server for mio-taskhub.

Exposes the local task hub as MCP tools so any MCP-capable agent
(opencode, claude code, codex, hermes, workbuddy, ...) can register,
claim tasks, send heartbeats and submit results as native tool calls.

Run:    python -m mio_taskhub.mcp_server
Config: MIO_TASKHUB_URL (default http://127.0.0.1:48620/api/v1)
"""

import json
import os
from typing import Optional
import httpx
from pydantic import Field
from mcp.server.fastmcp import FastMCP

HUB_URL = os.environ.get("MIO_TASKHUB_URL", "http://127.0.0.1:48620/api/v1")
TIMEOUT = 15.0

_headers = {}
_token = os.environ.get("MIO_TASKHUB_TOKEN", "")
if _token:
    _headers["Authorization"] = f"Bearer {_token}"
_client = httpx.AsyncClient(timeout=TIMEOUT, headers=_headers)

mcp = FastMCP(
    "mio_taskhub_mcp",
    instructions=(
        "mio-taskhub 是一个本地跨 agent 任务中心，通过本服务以工具调用的方式使用。\n"
        "对话使用规范：\n"
        "- 用户提到 任务/看板/派活/进度/待办/活干完没/安排 时，优先调用 taskhub_status 获取全局上下文。\n"
        "- 看板渲染：把 taskhub_status 结果渲染成 markdown 表格（阶段|数量|任务列表），不要贴原始 JSON。\n"
        "- 只读询问（看进度/看板）时只调 taskhub_status / taskhub_get_task / taskhub_list_tasks，不创建不修改。\n"
        "- 建任务用 taskhub_create_task，写清描述/验收标准；先给用户复述标题+描述，确认后再提交。\n"
        "- 推进阶段用 taskhub_advance_stage，需带产出物路径/审查结论，缺失时先向用户要。\n"
        "- 任务完成后用 taskhub_submit_result 提交，并向用户一句话汇报结果（成功/失败+原因）。\n"
        "执行类流程：taskhub_register 注册 → taskhub_claim 领取 → taskhub_heartbeat 心跳 → taskhub_submit_result 提交结果。"
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
        return {"error": f"无法连接 mio-taskhub 服务（{HUB_URL}）。请先启动：python -m uvicorn mio_taskhub.main:app --port 48620"}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except httpx.HTTPError as e:
        return {"error": f"请求失败: {e}"}


def _fmt(data: dict, pretty: bool = True) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2 if pretty else None)


@mcp.tool(
    name="taskhub_status",
    title="查看任务中心全局状态",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def taskhub_status(
    agent: str = Field(default="", description="当前 agent 名称（可选），传入后 running 只列该 agent 的任务", max_length=64),
) -> str:
    """一次获取全局上下文：各阶段任务计数、待领取队列、执行中任务、心跳超时/超期告警、最近完成与下一步建议。

    对话中用户提到 任务/看板/进度/待办/派活 时优先调用本工具，再把结果渲染成
    markdown 表格展示给用户，而不是贴原始 JSON。

    Args:
        agent: 可选，只显示该 agent 的执行中任务

    Returns:
        JSON: {updated_at, counts, ready_queue, running, alerts, recent_done, next_steps}
    """
    params = {"agent": agent} if agent else None
    data = await _request("GET", "/board/summary", params=params)
    return _fmt(data)


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
    stage: str = Field(default="brainstorming", description="讨论发生的研发阶段：brainstorming/design/planning/review/..."),
    messages: Optional[list] = Field(default=None, description="消息列表：[{author, role, content}]"),
) -> str:
    """agent 将任务拉回独立会话讨论后，回写摘要与结论到任务。

    有 conclusions 时讨论标记为 closed。消息列表可选。
    """
    body = {"topic": topic, "agent": agent, "summary": summary,
            "conclusions": conclusions, "stage": stage, "messages": messages or []}
    data = await _request("POST", f"/tasks/{task_id}/discussions", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_advance_stage", title="推进研发阶段", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_advance_stage(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    target_stage: str = Field(description="目标阶段：design/planning/ready/review/done/cancelled"),
    spec_path: Optional[str] = Field(default=None, description="设计文档路径（进 design 必填）"),
    plan_path: Optional[str] = Field(default=None, description="计划文档路径（进 planning 必填）"),
    review_result: Optional[str] = Field(default=None, description="审查结论（进 done 必填）"),
) -> str:
    """推进任务到下一研发阶段，需带对应产出物。

    brainstorming→design 需 spec_path 且任务下有讨论记录；
    design→planning 需 plan_path；review→done 需 review_result。
    """
    body = {"target_stage": target_stage}
    if spec_path is not None: body["spec_path"] = spec_path
    if plan_path is not None: body["plan_path"] = plan_path
    if review_result is not None: body["review_result"] = review_result
    data = await _request("POST", f"/tasks/{task_id}/stage", body=body)
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


@mcp.tool(name="taskhub_add_idea", title="记录想法/需求", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_add_idea(
    title: str = Field(description="想法/需求标题", min_length=1, max_length=200),
    description: str = Field(default="", description="详细描述", max_length=4000),
    project: str = Field(default="", description="关联项目名", max_length=200),
    labels: Optional[list] = Field(default=None, description="标签列表"),
) -> str:
    """把用户的一个想法/需求记录下来（状态 new），后续可开会讨论、拆解为任务。"""
    body = {"title": title, "description": description, "project": project, "labels": labels or []}
    data = await _request("POST", "/ideas", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_ideas", title="查看想法列表", annotations={
    "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
async def taskhub_ideas(
    status: Optional[str] = Field(default=None, description="状态过滤：new/fermenting/formed/broken_down/archived/cancelled"),
) -> str:
    """列出想法/需求（含状态），供用户随时回顾和管理。"""
    params = {"status": status} if status else None
    data = await _request("GET", "/ideas", params=params)
    return _fmt(data)


@mcp.tool(name="taskhub_update_idea", title="更新想法/需求", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_update_idea(
    idea_id: str = Field(description="想法唯一标识", min_length=1),
    title: Optional[str] = Field(default=None, description="标题"),
    description: Optional[str] = Field(default=None, description="描述"),
    status: Optional[str] = Field(default=None, description="状态：new/fermenting/formed/broken_down/archived/cancelled"),
) -> str:
    """更新想法内容或推进其状态（发酵/成形/已拆解），体现需求演进过程。"""
    body = {}
    if title is not None: body["title"] = title
    if description is not None: body["description"] = description
    if status is not None:
        data = await _request("POST", f"/ideas/{idea_id}/status", body={"status": status})
        return _fmt(data)
    data = await _request("PATCH", f"/ideas/{idea_id}", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_open_discussion", title="打开讨论会话", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_open_discussion(
    topic: str = Field(description="讨论主题", min_length=1, max_length=200),
    idea_id: str = Field(default="", description="绑定想法 id（idea 或 task 至少一个）", max_length=64),
    task_id: str = Field(default="", description="绑定任务 id（idea 或 task 至少一个）", max_length=64),
    agent: str = Field(default="", description="发起方 agent 名称", max_length=64),
    stage: str = Field(default="brainstorming", description="研发阶段：brainstorming/design/planning/review/..."),
) -> str:
    """针对某个想法或任务开启一个讨论会话，用户与 agent 可双向发消息。"""
    body = {"topic": topic, "idea_id": idea_id, "task_id": task_id,
            "agent": agent, "stage": stage}
    data = await _request("POST", "/discussions", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_discussion_messages", title="查看讨论消息", annotations={
    "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
async def taskhub_discussion_messages(
    discussion_id: Optional[str] = Field(default=None, description="讨论 id（或用 idea_id/task_id 拉取该对象的讨论）", max_length=64),
    idea_id: Optional[str] = Field(default=None, description="按想法查看其全部讨论", max_length=64),
    task_id: Optional[str] = Field(default=None, description="按任务查看其全部讨论", max_length=64),
) -> str:
    """读取讨论内容（含用户提问/agent 回复），用于加入或续接讨论。"""
    if discussion_id:
        data = await _request("GET", f"/discussions/{discussion_id}")
        return _fmt(data)
    if idea_id:
        data = await _request("GET", "/discussions", params={"ref_type": "idea", "ref_id": idea_id})
        return _fmt(data)
    if task_id:
        data = await _request("GET", "/discussions", params={"ref_type": "task", "ref_id": task_id})
        return _fmt(data)
    return _fmt({"error": "provide discussion_id or idea_id or task_id"})


@mcp.tool(name="taskhub_reply_discussion", title="讨论回消息", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_reply_discussion(
    discussion_id: str = Field(description="讨论唯一标识", min_length=1),
    content: str = Field(description="消息内容", min_length=1, max_length=4000),
    role: str = Field(default="agent", description="角色：user/agent/ask"),
    author: str = Field(default="", description="发言人", max_length=64),
) -> str:
    """在讨论会话中发一条消息（agent 回复或向用户提问 role=ask）。"""
    body = {"content": content, "role": role, "author": author}
    data = await _request("POST", f"/discussions/{discussion_id}/messages", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_close_discussion", title="关闭讨论", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_close_discussion(
    discussion_id: str = Field(description="讨论唯一标识", min_length=1),
    conclusions: str = Field(description="讨论结论", max_length=4000),
    summary: str = Field(default="", description="讨论摘要"),
) -> str:
    """结束讨论并回写结论，供后续拆解任务参考。"""
    body = {"conclusions": conclusions, "summary": summary}
    data = await _request("POST", f"/discussions/{discussion_id}/close", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_poll_events", title="增量订阅全局事件", annotations={
    "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
async def taskhub_poll_events(
    seq: int = Field(default=0, ge=0, description="上次消费的 seq；0 表示从头订阅全部（分页取回，每页最多 200 条）"),
) -> str:
    """增量订阅全局变更事件（建任务、领取、心跳、完成、阶段推进、想法、讨论等都会产生）。

    调用后记录返回的 next_seq，下次以其为 seq 即可拿到增量；一次最多返回 200 条，
    落后较多时需按 next_seq 多次轮询。心跳事件量大，可按需忽略 type=heartbeat。
    """
    params = {"after_seq": seq}
    data = await _request("GET", "/events", params=params)
    return _fmt(data)


@mcp.tool(name="taskhub_breakdown_idea", title="把想法拆解为任务集", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_breakdown_idea(
    idea_id: str = Field(description="想法唯一标识", min_length=1),
    tasks: list = Field(description="任务列表，每项含 title/ref/depends_on/priority 等字段"),
) -> str:
    """把一个已成形想法拆解为多个任务，子任务用 ref 互相引用依赖（如 depends_on: [\"t1\"]）。

    成功后想法状态置 broken_down，任务通过 idea_id 关联回该想法。
    """
    body = {"tasks": tasks}
    data = await _request("POST", f"/ideas/{idea_id}/breakdown", body=body)
    return _fmt(data)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
