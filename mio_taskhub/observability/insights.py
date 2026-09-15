import time
import logging
from mio_taskhub.db import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)

THRESHOLDS = {
    "task_failure_rate": {"warn": 0.05, "critical": 0.15},
    "taskhub_task_failure_rate": {"warn": 0.05, "critical": 0.15},
    "task_success_rate": {"warn_low": 0.90, "critical_low": 0.80},
    "taskhub_task_success_rate": {"warn_low": 0.90, "critical_low": 0.80},
    "cpu_percent": {"warn": 80, "critical": 95},
    "memory_percent": {"warn": 80, "critical": 95},
    "db_pool_utilization": {"warn": 0.80, "critical": 0.95},
    "slo_availability_30d": {"warn_low": 0.995, "critical_low": 0.99},
    "task_avg_completion_seconds": {"warn": 300, "critical": 600},
}

RECOMMENDATIONS = {
    "task_failure_rate": "Investigate failing tasks. Check agent health and task definitions.",
    "cpu_percent": "High CPU usage. Consider reducing concurrent workers or scaling horizontally.",
    "memory_percent": "High memory usage. Check for memory leaks or increase available memory.",
    "db_pool_utilization": "DB pool near capacity. Consider increasing pool size or optimizing queries.",
    "slo_availability_30d": "SLO availability dropping. Review recent failures and error budget.",
    "task_avg_completion_seconds": "Tasks taking too long. Review task complexity and worker capacity.",
}

class InsightsEngine:
    def evaluate(self, metrics: dict) -> list:
        insights = []
        for metric_name, value in metrics.items():
            if value is None or not isinstance(value, (int, float)):
                continue
            thresholds = THRESHOLDS.get(metric_name, {})
            if not thresholds:
                continue

            if "critical" in thresholds and value >= thresholds["critical"]:
                insight = self.store(
                    "anomaly", f"Critical: {metric_name}",
                    f"{metric_name} = {value:.4f} exceeds critical threshold {thresholds['critical']}",
                    severity="critical", metric_name=metric_name, metric_value=value,
                    baseline=thresholds["critical"], recommendation=RECOMMENDATIONS.get(metric_name, "")
                )
                insights.append(insight)
            elif "critical_low" in thresholds and value <= thresholds["critical_low"]:
                insight = self.store(
                    "anomaly", f"Critical: {metric_name} low",
                    f"{metric_name} = {value:.4f} below critical threshold {thresholds['critical_low']}",
                    severity="critical", metric_name=metric_name, metric_value=value,
                    baseline=thresholds["critical_low"], recommendation=RECOMMENDATIONS.get(metric_name, "")
                )
                insights.append(insight)
            elif "warn" in thresholds and value >= thresholds["warn"]:
                insight = self.store(
                    "anomaly", f"Warning: {metric_name}",
                    f"{metric_name} = {value:.4f} exceeds warning threshold {thresholds['warn']}",
                    severity="warning", metric_name=metric_name, metric_value=value,
                    baseline=thresholds["warn"], recommendation=RECOMMENDATIONS.get(metric_name, "")
                )
                insights.append(insight)
            elif "warn_low" in thresholds and value <= thresholds["warn_low"]:
                insight = self.store(
                    "anomaly", f"Warning: {metric_name} low",
                    f"{metric_name} = {value:.4f} below warning threshold {thresholds['warn_low']}",
                    severity="warning", metric_name=metric_name, metric_value=value,
                    baseline=thresholds["warn_low"], recommendation=RECOMMENDATIONS.get(metric_name, "")
                )
                insights.append(insight)
        return insights

    def store(self, kind: str, title: str, description: str, severity: str = "info",
              metric_name: str = None, metric_value: float = None, baseline: float = None,
              recommendation: str = None, auto_action: str = None) -> dict:
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("INSERT INTO insight (ts, kind, title, description, severity, metric_name, metric_value, baseline, recommendation, auto_action) VALUES (:ts, :kind, :title, :description, :severity, :metric_name, :metric_value, :baseline, :recommendation, :auto_action)"),
                    {"ts": time.time(), "kind": kind, "title": title, "description": description,
                     "severity": severity, "metric_name": metric_name, "metric_value": metric_value,
                     "baseline": baseline, "recommendation": recommendation, "auto_action": auto_action}
                )
                conn.commit()
                return {"id": result.lastrowid, "ts": time.time(), "kind": kind, "title": title, "severity": severity}
        except Exception:
            logger.exception("Failed to store insight")
            return {}

    def recent(self, limit: int = 20, kind: str = None) -> list:
        try:
            with engine.connect() as conn:
                if kind:
                    result = conn.execute(text("SELECT * FROM insight WHERE kind = :kind ORDER BY ts DESC LIMIT :limit"), {"kind": kind, "limit": limit})
                else:
                    result = conn.execute(text("SELECT * FROM insight ORDER BY ts DESC LIMIT :limit"), {"limit": limit})
                return [dict(row._mapping) for row in result]
        except Exception:
            logger.exception("Failed to query insights")
            return []

    def acknowledge(self, insight_id: int):
        try:
            with engine.connect() as conn:
                conn.execute(text("UPDATE insight SET acknowledged = 1 WHERE id = :id"), {"id": insight_id})
                conn.commit()
        except Exception:
            logger.exception("Failed to acknowledge insight")