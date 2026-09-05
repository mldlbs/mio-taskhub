"""Memory API: 本地 JSONL 知识图谱端点（替代 MCP 子进程方案）。

端点：
- GET  /api/memory/health    — 健康状态
- GET  /api/memory/query     — 查询记忆
- POST /api/memory/record    — 记录记忆
- POST /api/memory/policy/check — 策略检查
- POST /api/memory/observer/ingest — 观察事件
- POST /api/memory/experience/reuse — 经验复用
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from mio_taskhub import memory_store as store
from mio_taskhub.events import emit_event, broadcast_for_event
from mio_taskhub.db import get_session


router = APIRouter(prefix="/api/memory", tags=["memory"])


# ---------- Pydantic models ----------

class RecordRequest(BaseModel):
    kind: str = Field(..., description="decision/context/problem/note")
    context: str = Field("", description="决策/上下文简述")
    payload: dict = Field(default_factory=dict, description="结构化详情")
    project: Optional[str] = Field(None, description="项目名")


class PolicyRequest(BaseModel):
    operation: str = Field(..., description="操作名")
    context: dict = Field(default_factory=dict, description="操作上下文")


class IngestRequest(BaseModel):
    trace_id: str = Field(..., description="追踪 ID")
    event_type: str = Field(..., description="事件类型")
    payload: dict = Field(default_factory=dict)
    outcome: str = Field("success", description="success/failure/aborted")


class ExperienceReuseRequest(BaseModel):
    sourceAgent: str = Field(..., description="原 agent")
    targetAgent: str = Field(..., description="新 agent")
    experienceId: str = Field(..., description="经验 ID")
    reuse: bool = Field(..., description="是否复用")
    behaviorChanged: bool = Field(False, description="行为是否变化")
    outcomeImproved: Optional[bool] = Field(None, description="结果是否改善")


# ---------- 限流 ----------

_rate_buckets: dict[str, list[float]] = {}
_RATE_LIMIT = 60  # req/min


def _enforce_rate_limit(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    endpoint = request.url.path
    key = f"{client_ip}|{endpoint}"
    now = __import__("time").time()
    bucket = _rate_buckets.setdefault(key, [])
    # 滑动窗口
    cutoff = now - 60
    bucket[:] = [t for t in bucket if t > cutoff]
    if len(bucket) >= _RATE_LIMIT:
        retry_after = int(bucket[0] - cutoff) + 1
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limited", "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)


# ---------- 事件广播 ----------

def _broadcast_event(event_type: str, entity_id: str, payload: dict):
    try:
        with next(get_session()) as db:
            ev = emit_event(db, type=event_type, entity="memory",
                            entity_id=entity_id, payload=payload)
            db.commit()
            db.refresh(ev)
            broadcast_for_event(ev)
    except Exception:
        pass


# ---------- 端点 ----------

@router.get("/health")
def health():
    """Memory 健康状态。"""
    h = store.health()
    metrics = store.get_metrics()
    total_5m = sum(metrics.get("calls_5m", {}).values())
    return {
        "status": "ok" if h["available"] else "degraded",
        "mcp": {
            **h,
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
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    limit: int = Query(20, ge=1, le=200),
):
    """查询记忆。"""
    _enforce_rate_limit(request)
    try:
        result = store.query_memories(kind=kind, project=project,
                                      limit=limit, keyword=keyword)
        store.record_call("query", "ok")
        return result
    except Exception as e:
        store.record_call("query", "error")
        raise HTTPException(500, detail={"error": str(e)})


@router.post("/record")
def record(body: RecordRequest, request: Request):
    """记录记忆。"""
    _enforce_rate_limit(request)
    try:
        result = store.record_memory(
            kind=body.kind, context=body.context,
            payload=body.payload, project=body.project,
        )
        store.record_call("record", "ok")
        _broadcast_event("memory_record", body.kind,
                         {"kind": body.kind, "result": result})
        return result
    except Exception as e:
        store.record_call("record", "error")
        raise HTTPException(500, detail={"error": str(e)})


@router.post("/policy/check")
def policy_check(body: PolicyRequest, request: Request):
    """策略检查。"""
    _enforce_rate_limit(request)
    try:
        result = store.policy_check(body.operation, body.context)
        store.record_call("policy_check", "ok")
        return result
    except Exception as e:
        store.record_call("policy_check", "error")
        raise HTTPException(500, detail={"error": str(e)})


@router.post("/observer/ingest")
def observer_ingest(body: IngestRequest, request: Request):
    """观察事件。"""
    _enforce_rate_limit(request)
    try:
        result = store.observer_ingest(
            body.trace_id, body.event_type, body.payload, body.outcome,
        )
        store.record_call("observer_ingest", "ok")
        _broadcast_event("memory_observer_ingest", body.trace_id,
                         {"event_type": body.event_type, "result": result})
        return result
    except Exception as e:
        store.record_call("observer_ingest", "error")
        raise HTTPException(500, detail={"error": str(e)})


@router.post("/experience/reuse")
def experience_reuse(body: ExperienceReuseRequest, request: Request):
    """经验复用。"""
    _enforce_rate_limit(request)
    try:
        result = store.experience_reuse(
            body.sourceAgent, body.targetAgent, body.experienceId,
            body.reuse, body.behaviorChanged, body.outcomeImproved,
        )
        store.record_call("experience_reuse", "ok")
        _broadcast_event("memory_experience_reuse", body.experienceId,
                         {"source": body.sourceAgent, "result": result})
        return result
    except Exception as e:
        store.record_call("experience_reuse", "error")
        raise HTTPException(500, detail={"error": str(e)})
