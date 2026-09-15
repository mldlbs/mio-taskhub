import time
import logging
from mio_taskhub.db import engine
from sqlalchemy import text

logger = logging.getLogger("mio_taskhub.observability.audit")


def _execute(query, params=()):
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query), params)
            conn.commit()
            return result
    except Exception:
        logger.exception("Audit DB error")
        return None


class AlertAudit:
    def log_fire(self, alert_name: str, severity: str, message: str = "", metric_value: float = None, threshold: float = None):
        _execute(
            "INSERT INTO alertaudit (ts, alert_name, severity, action, message, metric_value, threshold) VALUES (:ts, :alert_name, :severity, :action, :message, :metric_value, :threshold)",
            {"ts": time.time(), "alert_name": alert_name, "severity": severity, "action": "fire", "message": message, "metric_value": metric_value, "threshold": threshold}
        )

    def log_resolve(self, alert_name: str, message: str = "", duration_seconds: float = None, severity: str = ""):
        _execute(
            "INSERT INTO alertaudit (ts, alert_name, severity, action, message, duration_seconds) VALUES (:ts, :alert_name, :severity, :action, :message, :duration_seconds)",
            {"ts": time.time(), "alert_name": alert_name, "severity": severity, "action": "resolve", "message": message, "duration_seconds": duration_seconds}
        )

    def recent(self, limit: int = 50) -> list:
        result = _execute("SELECT * FROM alertaudit ORDER BY ts DESC LIMIT :limit", {"limit": limit})
        if result is None:
            return []
        return [dict(row) for row in result.mappings()]

    def stats(self, hours: int = 24) -> dict:
        result = _execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN action='fire' THEN 1 ELSE 0 END) as fires, SUM(CASE WHEN action='resolve' THEN 1 ELSE 0 END) as resolves FROM alertaudit WHERE ts > :cutoff",
            {"cutoff": time.time() - hours * 3600}
        )
        if result is None:
            return {"total": 0, "fires": 0, "resolves": 0}
        row = result.mappings().fetchone()
        return dict(row) if row else {"total": 0, "fires": 0, "resolves": 0}
