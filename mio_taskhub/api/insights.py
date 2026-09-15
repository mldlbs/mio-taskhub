from fastapi import APIRouter, Query
from mio_taskhub.observability.insights import InsightsEngine
from mio_taskhub.observability.audit import AlertAudit
from mio_taskhub.observability.remediation import RemediationEngine

router = APIRouter(prefix="/api/v1", tags=["insights"])
_engine = InsightsEngine()
_audit = AlertAudit()
_remediation = RemediationEngine()

@router.get("/insights")
def list_insights(limit: int = Query(20, ge=1, le=100), kind: str = Query(None)):
    return _engine.recent(limit=limit, kind=kind)

@router.post("/insights/{insight_id}/acknowledge")
def acknowledge_insight(insight_id: int):
    _engine.acknowledge(insight_id)
    return {"ok": True}

@router.get("/audit")
def list_audit(limit: int = Query(50, ge=1, le=200)):
    return _audit.recent(limit=limit)

@router.get("/audit/stats")
def audit_stats(hours: int = Query(24, ge=1, le=168)):
    return _audit.stats(hours=hours)

@router.get("/remediation")
def list_remediation(limit: int = Query(20, ge=1, le=100)):
    return _remediation.recent(limit=limit)

@router.get("/observability/summary")
def observability_summary():
    from mio_taskhub.observability.metrics import render_metrics
    import re
    metrics_text = render_metrics()

    def extract(gauge_name):
        match = re.search(rf'{gauge_name}\s+([\d.]+)', metrics_text)
        return float(match.group(1)) if match else None

    return {
        "slo_availability": extract("taskhub_slo_availability_30d"),
        "error_budget_remaining": extract("taskhub_slo_error_budget_remaining"),
        "task_success_rate": extract("taskhub_task_success_rate"),
        "task_failure_rate": extract("taskhub_task_failure_rate"),
        "cpu_percent": extract("taskhub_process_cpu_percent"),
        "memory_percent": extract("taskhub_process_memory_percent"),
        "db_pool_utilization": extract("taskhub_db_pool_utilization"),
        "active_threads": extract("taskhub_thread_pool_alive"),
        "insights_unacknowledged": len(_engine.recent(limit=100)),
        "audit_events_24h": _audit.stats(hours=24).get("total", 0),
    }
