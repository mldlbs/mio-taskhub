"""Memory Gateway API: 6 个端点代理 mio-intelligence MCP 工具 + UX 增强。

端点（v1+v2）：
- GET  /api/memory/health    — 详细健康状态
- GET  /api/memory/query
- POST /api/memory/record
- POST /api/memory/policy/check
- POST /api/memory/observer/ingest
- POST /api/memory/experience/reuse

UX 增强（v3）：
- /health 返回 MCP proc_alive / respawn_count / last_call_ms / last_error / 5min 计数
- 错误响应统一含 request_id + hint + docs
- 全局限流：60 req/min/(ip+endpoint)，超 429 + Retry-After
- GZip 压缩（在 main.py 启用）
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from mio_taskhub.memory_gateway import (
    MCPUnavailable,
    MCPTimeout,
    MCPRPCError,
    get_client,
    get_limiter,
    record_call,
)
from mio_taskhub.events import emit_event, broadcast_for_event
from mio_taskhub.db import get_session


router = APIRouter(prefix="/api/memory", tags=["memory-gateway"])


# ---------- Pydantic models ----------

class RecordRequest(BaseModel):
    kind: str = Field(..., description="decision/context/problem/note")
    context: str = Field("", description="决策/上下文简述")
    payload: dict = Field(default_factory=dict, description="结构化详情")
    project: Optional[str] = Field(None, description="项目名（自动从 cwd 推断）")


class PolicyRequest(BaseModel):
    operation: str = Field(..., description="操作名，如 delete_task/migrate_db")
    context: dict = Field(default_factory=dict, description="操作上下文")


class IngestRequest(BaseModel):
    trace_id: str = Field(..., description="追踪 ID")
    event_type: str = Field(..., description="事件类型")
    payload: dict = Field(default_factory=dict)
    outcome: str = Field("success", description="success/failure/aborted")


class ExperienceReuseRequest(BaseModel):
    sourceAgent: str = Field(..., description="原 agent")
    targetAgent: str = Field(..., description="新 agent")
    experienceId: str = Field(..., description="经验 ID（来自 memory.query）")
    reuse: bool = Field(..., description="是否复用")
    behaviorChanged: bool = Field(False, description="行为是否变化")
    outcomeImproved: Optional[bool] = Field(None, description="结果是否改善")


# ---------- 错误响应助手（v3 UX）----------

_ERROR_HINTS = {
    "memory_unavailable": "MCP 子进程未启动或已退出。检查环境变量 MIO_MEMORY_COMMAND / MIO_MEMORY_ARGS，或访问 /api/memory/health 确认 proc_alive。",
    "memory_timeout": "MCP 调用超时（默认 5s）。可设置 MIO_MEMORY_TIMEOUT 提高阈值。",
    "memory_rpc_error": "MCP 进程返回错误响应。检查子进程日志或降低并发。",
    "rate_limited": "请求频率超限。降低频率或提高 MIO_MEMORY_RATE_LIMIT。",
}


def _enforce_rate_limit(request: Request):
    """限流检查：超限直接 raise 429 + Retry-After。"""
    client_ip = request.client.host if request.client else "unknown"
    endpoint = request.url.path
    allowed, retry_after = get_limiter().check("{}|{}".format(client_ip, endpoint))
    if not allowed:
        request_id = (
            getattr(request.state, "request_id", None)
            or request.headers.get("X-Request-ID")
            or "unknown"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "detail": "max 60 req/min per (ip, endpoint)",
                "request_id": request_id,
                "hint": _ERROR_HINTS["rate_limited"],
                "docs": "/api/memory/health",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )


def _enrich_error(request: Request, error: str, detail: str) -> dict:
    """把异常 detail 扩展为带 request_id/hint/docs。"""
    request_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or "unknown"
    )
    return {
        "error": error,
        "detail": detail,
        "request_id": request_id,
        "hint": _ERROR_HINTS.get(error, "查看 /api/memory/health 排查。"),
        "docs": "/api/memory/health",
    }


# ---------- 核心调用 ----------

def _call(tool: str, params: dict, event_type: str = None, event_entity_id: str = ""):
    """统一调用入口。"""
    try:
        result = get_client().call(tool, params)
        record_call(tool, "ok")
        if event_type:
            _broadcast_event(event_type, event_entity_id, {"tool": tool, "params": params, "result": result})
        return result
    except MCPUnavailable as e:
        record_call(tool, "unavailable")
        raise HTTPException(503, detail={"error": "memory_unavailable", "_raw": str(e)})
    except MCPTimeout as e:
        record_call(tool, "timeout")
        raise HTTPException(504, detail={"error": "memory_timeout", "_raw": str(e)})
    except MCPRPCError as e:
        record_call(tool, "rpc_error")
        raise HTTPException(502, detail={"error": "memory_rpc_error", "_raw": str(e)})


def _broadcast_event(event_type: str, entity_id: str, payload: dict):
    """写 Event 表 + WS 广播。失败静默。"""
    try:
        with next(get_session()) as db:
            ev = emit_event(db, type=event_type, entity="memory", entity_id=entity_id, payload=payload)
            db.commit()
            db.refresh(ev)
            broadcast_for_event(ev)
    except Exception:
        pass


# ---------- 端点 ----------

@router.get("/health")
def health():
    """Memory Gateway 详细健康状态（v3）。"""
    from mio_taskhub.memory_gateway import get_metrics as _mem_metrics
    client = get_client()
    mcp_health = client.health()
    metrics = _mem_metrics()
    total_5m = sum(metrics.get("calls_5m", {}).values())
    status = "ok" if mcp_health["proc_alive"] else "degraded"
    return {
        "status": status,
        "mcp": {
            **mcp_health,
            "calls_total_5m": total_5m,
            "per_tool_5m": metrics.get("calls_5m", {}),
            "last_error_per_tool": metrics.get("last_error", {}),
        },
    }


@router.get("/query")
def query(
    request: Request,
    kind: Optional[str] = Query(None, description="decision/context/problem/note"),
    project: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
):
    """查询记忆：代理 mio_memory_query。"""
    _enforce_rate_limit(request)
    try:
        return _call("mio_memory_query", {"kind": kind, "project": project, "limit": limit})
    except HTTPException as e:
        if isinstance(e.detail, dict) and "_raw" in e.detail:
            e.detail = _enrich_error(request, e.detail["error"], e.detail["_raw"])
        raise


@router.post("/record")
def record(body: RecordRequest, request: Request):
    """记录记忆：代理 mio_memory_record，事件广播。"""
    _enforce_rate_limit(request)
    try:
        _call("mio_memory_record", body.model_dump(exclude_none=True),
              event_type="memory_record", event_entity_id=body.kind)
        return {"ok": True}
    except HTTPException as e:
        if isinstance(e.detail, dict) and "_raw" in e.detail:
            e.detail = _enrich_error(request, e.detail["error"], e.detail["_raw"])
        raise


@router.post("/policy/check")
def policy_check(body: PolicyRequest, request: Request):
    """高风险操作前策略检查：代理 mio_policy_check。"""
    _enforce_rate_limit(request)
    try:
        return _call("mio_policy_check", body.model_dump())
    except HTTPException as e:
        if isinstance(e.detail, dict) and "_raw" in e.detail:
            e.detail = _enrich_error(request, e.detail["error"], e.detail["_raw"])
        raise


@router.post("/observer/ingest")
def observer_ingest(body: IngestRequest, request: Request):
    """上报观察事件：代理 mio_observer_ingest，事件广播。"""
    _enforce_rate_limit(request)
    try:
        _call("mio_observer_ingest", body.model_dump(),
              event_type="memory_observer_ingest", event_entity_id=body.trace_id)
        return {"ok": True}
    except HTTPException as e:
        if isinstance(e.detail, dict) and "_raw" in e.detail:
            e.detail = _enrich_error(request, e.detail["error"], e.detail["_raw"])
        raise


@router.post("/experience/reuse")
def experience_reuse(body: ExperienceReuseRequest, request: Request):
    """复用经验：代理 mio_experience_reuse，事件广播。"""
    _enforce_rate_limit(request)
    try:
        _call("mio_experience_reuse", body.model_dump(),
              event_type="memory_experience_reuse", event_entity_id=body.experienceId)
        return {"ok": True}
    except HTTPException as e:
        if isinstance(e.detail, dict) and "_raw" in e.detail:
            e.detail = _enrich_error(request, e.detail["error"], e.detail["_raw"])
        raise
