"""Memory Gateway API: 6 个端点代理 mio-intelligence MCP 工具。

- GET  /api/memory/health
- GET  /api/memory/query
- POST /api/memory/record
- POST /api/memory/policy/check
- POST /api/memory/observer/ingest
- POST /api/memory/experience/reuse

v2 增强：写操作（record/ingest/experience_reuse）经 taskhub Event 表 + WS 广播；
metrics 端点暴露调用次数。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mio_taskhub.memory_gateway import (
    MCPUnavailable,
    MCPTimeout,
    MCPRPCError,
    get_client,
    record_call,
)
from mio_taskhub.events import emit_event, broadcast_for_event
from mio_taskhub.db import get_session


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


class ExperienceReuseRequest(BaseModel):
    sourceAgent: str = Field(..., description="原 agent")
    targetAgent: str = Field(..., description="新 agent")
    experienceId: str = Field(..., description="经验 ID（来自 memory.query）")
    reuse: bool = Field(..., description="是否复用")
    behaviorChanged: bool = Field(False, description="行为是否变化")
    outcomeImproved: Optional[bool] = Field(None, description="结果是否改善")


def _call(tool: str, params: dict, event_type: str = None, event_entity_id: str = ""):
    """统一调用入口：异常映射 HTTP 状态码，OK 时记录 metrics 与（可选）WS 广播。

    event_type: 非空时，把工具调用作为 Event 写库 + WS 广播。
    """
    try:
        result = get_client().call(tool, params)
        record_call(tool, "ok")
        if event_type:
            _broadcast_event(event_type, event_entity_id, {"tool": tool, "params": params, "result": result})
        return result
    except MCPUnavailable as e:
        record_call(tool, "unavailable")
        raise HTTPException(503, detail={"error": "memory_unavailable", "detail": str(e)})
    except MCPTimeout as e:
        record_call(tool, "timeout")
        raise HTTPException(504, detail={"error": "memory_timeout", "detail": str(e)})
    except MCPRPCError as e:
        record_call(tool, "rpc_error")
        raise HTTPException(502, detail={"error": "memory_rpc_error", "detail": str(e)})


def _broadcast_event(event_type: str, entity_id: str, payload: dict):
    """写 Event 表 + 广播到 WS。失败静默（不影响主流程）。"""
    try:
        with next(get_session()) as db:
            ev = emit_event(db, type=event_type, entity="memory", entity_id=entity_id, payload=payload)
            db.commit()
            db.refresh(ev)
            broadcast_for_event(ev)
    except Exception:
        pass


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
    """记录记忆：代理 mio_memory_record，事件广播。"""
    _call("mio_memory_record", body.model_dump(exclude_none=True),
          event_type="memory_record", event_entity_id=body.kind)
    return {"ok": True}


@router.post("/policy/check")
def policy_check(body: PolicyRequest):
    """高风险操作前策略检查：代理 mio_policy_check。"""
    return _call("mio_policy_check", body.model_dump())


@router.post("/observer/ingest")
def observer_ingest(body: IngestRequest):
    """上报观察事件：代理 mio_observer_ingest，事件广播。"""
    _call("mio_observer_ingest", body.model_dump(),
          event_type="memory_observer_ingest", event_entity_id=body.trace_id)
    return {"ok": True}


@router.post("/experience/reuse")
def experience_reuse(body: ExperienceReuseRequest):
    """复用经验：代理 mio_experience_reuse，事件广播。"""
    _call("mio_experience_reuse", body.model_dump(),
          event_type="memory_experience_reuse", event_entity_id=body.experienceId)
    return {"ok": True}
