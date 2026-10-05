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
    from mio_taskhub.observability.report import collect_observability
    obs = collect_observability()
    return {
        "slo_availability": obs["slo"].get("availability_30d"),
        "error_budget_remaining": obs["slo"].get("error_budget_remaining"),
        "task_success_rate": obs["tasks"].get("success_rate"),
        "task_failure_rate": obs["tasks"].get("failure_rate"),
        "cpu_percent": obs["process"].get("cpu_percent"),
        "memory_percent": obs["process"].get("memory_percent"),
        "db_pool_utilization": obs["database"].get("pool_utilization"),
        "active_threads": obs["threads"].get("alive"),
        "insights_unacknowledged": obs["insights"]["unacknowledged"],
        "audit_events_24h": _audit.stats(hours=24).get("total", 0),
    }
