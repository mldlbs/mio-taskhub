"""Observability API: alerts, SLO history, task tracing, custom rules."""
import json
import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from sqlmodel import Session, text
from mio_taskhub.db import engine
from mio_taskhub.policy_guard import guard_action

router = APIRouter(prefix="/api/v1", tags=["observability"])


# ── SLO History ──────────────────────────────────────────────────────

@router.get("/slo/history")
def slo_history(hours: int = 24, limit: int = 288):
    from mio_taskhub.observability.slo_history import get_slo_history
    return {"snapshots": get_slo_history(hours=hours, limit=limit)}


@router.post("/slo/snapshot")
def slo_snapshot_now():
    from mio_taskhub.observability.slo_history import capture_slo_snapshot
    capture_slo_snapshot()
    return {"ok": True}


# ── Structured Observability Report (agent-readable, no Prometheus scraping) ──

@router.get("/observability/report")
def observability_report():
    """完整结构化可观测性快照（database/http/process/threads/tasks/agents/slo/alerts/insights）。
    供 agent 或脚本直接消费，无需解析 Prometheus 文本。"""
    from mio_taskhub.observability.report import collect_observability
    return collect_observability()


@router.get("/observability/integrity")
def integrity_check():
    """integrity_check 风格总报告：每个组件 PASS/WARN/FAIL + overall_status + 完整 snapshot。"""
    from mio_taskhub.observability.report import integrity_check as _run
    return _run()


# ── Task Tracing ─────────────────────────────────────────────────────

@router.get("/traces/{task_id}")
def task_trace(task_id: str):
    from mio_taskhub.observability.task_trace import get_task_trace
    result = get_task_trace(task_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/traces")
def task_traces_summary(limit: int = 50):
    from mio_taskhub.observability.task_trace import get_task_traces_summary
    return {"traces": get_task_traces_summary(limit=limit)}


# ── Custom Alert Rules ──────────────────────────────────────────────

class AlertRuleCreate(BaseModel):
    name: str
    metric: str
    condition: str  # gt, lt, eq, gte, lte
    threshold: float
    severity: str = "warning"
    cooldown_seconds: int = 300
    notify_channels: Optional[list] = None
    notify_config: Optional[dict] = None


@router.get("/alert-rules")
def list_alert_rules():
    try:
        with Session(engine) as db:
            rows = db.exec(text("SELECT * FROM alertrule ORDER BY created_at DESC")).all()
            rules = []
            for r in rows:
                rules.append({
                    "id": r.id,
                    "name": r.name,
                    "metric": r.metric,
                    "condition": r.condition,
                    "threshold": r.threshold,
                    "severity": r.severity,
                    "enabled": r.enabled,
                    "cooldown_seconds": r.cooldown_seconds,
                    "last_fired_at": r.last_fired_at,
                    "notify_channels": json.loads(r.notify_channels) if r.notify_channels else None,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                })
            return {"rules": rules}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alert-rules")
def create_alert_rule(body: AlertRuleCreate):
    rule_id = __import__("uuid").uuid4().hex[:8]
    now = time.time()
    try:
        with Session(engine) as db:
            db.execute(text("""
                INSERT INTO alertrule (id, name, metric, condition, threshold, severity,
                    cooldown_seconds, notify_channels, notify_config, created_at, updated_at)
                VALUES (:id, :name, :metric, :cond, :thresh, :sev, :cooldown, :channels, :config, :now, :now)
            """), {
                "id": rule_id, "name": body.name, "metric": body.metric,
                "cond": body.condition, "thresh": body.threshold,
                "sev": body.severity, "cooldown": body.cooldown_seconds,
                "channels": json.dumps(body.notify_channels) if body.notify_channels else None,
                "config": json.dumps(body.notify_config) if body.notify_config else None,
                "now": now,
            })
            db.commit()
            return {"id": rule_id, "ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/alert-rules/{rule_id}")
def delete_alert_rule(rule_id: str, confirm: bool = Query(False)):
    policy = guard_action("taskhub:delete-alert-rule", confirm=confirm)
    try:
        with Session(engine) as db:
            db.execute(text("DELETE FROM alertrule WHERE id = :id"), {"id": rule_id})
            db.commit()
            return {"ok": True, "policy": policy}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/alert-rules/{rule_id}/toggle")
def toggle_alert_rule(rule_id: str):
    try:
        with Session(engine) as db:
            db.execute(text("UPDATE alertrule SET enabled = NOT enabled, updated_at = :t WHERE id = :id"),
                       {"t": time.time(), "id": rule_id})
            db.commit()
            return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── WebSocket Status ─────────────────────────────────────────────────

@router.get("/ws/status")
def ws_status():
    from mio_taskhub.events import ws_manager
    return {"connected_clients": len(ws_manager.active)}


# ── Process Info ─────────────────────────────────────────────────────

@router.get("/process")
def process_info():
    import os
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        return {
            "pid": os.getpid(),
            "cpu_percent": proc.cpu_percent(interval=0.1),
            "memory_rss_mb": round(mem.rss / 1048576, 1),
            "memory_vms_mb": round(mem.vms / 1048576, 1),
            "threads": proc.num_threads(),
            "uptime_seconds": round(time.time() - __import__("mio_taskhub.observability.metrics", fromlist=["_start_time"])._start_time, 1),
        }
    except ImportError:
        return {"pid": os.getpid(), "error": "psutil not installed"}
