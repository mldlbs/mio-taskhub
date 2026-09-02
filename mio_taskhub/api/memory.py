"""Memory Gateway API: 4 个端点代理 mio-intelligence MCP 工具。

- GET  /api/memory/query
- POST /api/memory/record
- POST /api/memory/policy/check
- POST /api/memory/observer/ingest
- GET  /api/memory/health
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mio_taskhub.memory_gateway import (
    MCPUnavailable,
    MCPTimeout,
    MCPRPCError,
    get_client,
)


router = APIRouter(prefix="/api/memory", tags=["memory-gateway"])


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


def _call(tool: str, params: dict) -> dict:
    """统一调用入口，异常映射为 HTTP 状态码。"""
    try:
        return get_client().call(tool, params)
    except MCPUnavailable as e:
        raise HTTPException(503, detail={"error": "memory_unavailable", "detail": str(e)})
    except MCPTimeout as e:
        raise HTTPException(504, detail={"error": "memory_timeout", "detail": str(e)})
    except MCPRPCError as e:
        raise HTTPException(502, detail={"error": "memory_rpc_error", "detail": str(e)})


@router.get("/health")
def health():
    """Memory Gateway 健康检查。"""
    available = get_client().is_available()
    return {"status": "ok" if available else "degraded", "mcp_available": available}


@router.get("/query")
def query(
    kind: Optional[str] = Query(None, description="decision/context/problem/note"),
    project: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
):
    """查询记忆：代理 mio_memory_query。"""
    return _call("mio_memory_query", {"kind": kind, "project": project, "limit": limit})


@router.post("/record")
def record(body: RecordRequest):
    """记录记忆：代理 mio_memory_record。"""
    _call("mio_memory_record", body.model_dump(exclude_none=True))
    return {"ok": True}


@router.post("/policy/check")
def policy_check(body: PolicyRequest):
    """高风险操作前策略检查：代理 mio_policy_check。"""
    return _call("mio_policy_check", body.model_dump())


@router.post("/observer/ingest")
def observer_ingest(body: IngestRequest):
    """上报观察事件：代理 mio_observer_ingest。"""
    _call("mio_observer_ingest", body.model_dump())
    return {"ok": True}
