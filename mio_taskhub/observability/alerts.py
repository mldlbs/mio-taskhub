"""Built-in lightweight alerting for mio-taskhub.

Evaluates alert rules against live metrics and exposes them via /api/v1/alerts.
No external Prometheus/Alertmanager required — suitable for single-EXE deployment.
"""
import time
import logging
from dataclasses import dataclass, field
from threading import Lock

from mio_taskhub.background import get_thread_health
from mio_taskhub.middleware import get_http_metrics
from mio_taskhub.observability.audit import AlertAudit

logger = logging.getLogger("mio_taskhub.observability.alerts")

_audit = AlertAudit()

_status_lock = Lock()


@dataclass
class Alert:
    name: str
    severity: str
    message: str
    fired_at: float = 0.0
    resolved_at: float = 0.0
    value: float = 0.0
    labels: dict = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.fired_at > 0 and self.resolved_at == 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
            "active": self.active,
            "fired_at": self.fired_at or None,
            "resolved_at": self.resolved_at or None,
            "value": self.value,
            "labels": self.labels,
        }


class AlertManager:
    """Evaluates alert rules against live metrics and exposes status."""

    def __init__(self):
        self._alerts: dict[str, Alert] = {}
        self._last_eval = 0.0
        self._eval_interval = 30

    def _check_threads(self) -> list[Alert]:
        results: list[Alert] = []
        ts = get_thread_health()
        now = time.time()

        for thread_name, data in ts.items():
            alive = data.get("alive", False)
            age = data.get("age_seconds", 0)
            if age == float("inf"):
                age = now
            failures = data.get("consecutive_failures", 0)

            if not alive:
                results.append(Alert(
                    name=f"ThreadDead_{thread_name}",
                    severity="critical",
                    message=f"Thread {thread_name} is dead (no heartbeat)",
                    fired_at=now,
                    value=age,
                    labels={"thread": thread_name},
                ))
            elif age > 120:
                results.append(Alert(
                    name=f"ThreadStalled_{thread_name}",
                    severity="critical",
                    message=f"Thread {thread_name} heartbeat age {age:.0f}s > 120s",
                    fired_at=now - max(age - 120, 0),
                    value=age,
                    labels={"thread": thread_name},
                ))
            elif failures > 3:
                results.append(Alert(
                    name=f"ThreadFailures_{thread_name}",
                    severity="critical",
                    message=f"Thread {thread_name} has {failures} consecutive failures",
                    fired_at=now,
                    value=failures,
                    labels={"thread": thread_name},
                ))

        return results

    def _check_http(self) -> list[Alert]:
        results: list[Alert] = []
        hm = get_http_metrics()
        now = time.time()

        total = hm.get("request_count_total", 0)
        errors = sum(hm.get("error_count", {}).values())
        if total > 10 and errors / total > 0.05:
            results.append(Alert(
                name="HighHttpErrorRate",
                severity="warning",
                message=f"HTTP 错误率 {errors/total*100:.1f}%（{errors}/{total} 请求）超过 5% 阈值",
                fired_at=now,
                value=errors / total,
            ))

        active = hm.get("active_requests", 0)
        if active > 100:
            results.append(Alert(
                name="HighActiveRequests",
                severity="warning",
                message=f"{active} active HTTP requests > 100",
                fired_at=now,
                value=active,
            ))

        return results

    def evaluate(self) -> list[Alert]:
        now = time.time()
        if now - self._last_eval < self._eval_interval:
            return [a for a in self._alerts.values() if a.active]

        with _status_lock:
            previously_active = {n for n, a in self._alerts.items() if a.active}

            new_alerts: dict[str, Alert] = {}
            for alert in self._check_threads():
                new_alerts[alert.name] = alert
            for alert in self._check_http():
                new_alerts[alert.name] = alert

            currently_active = {n for n in new_alerts if new_alerts[n].active}

            # Collect audit events (no DB writes under lock)
            audit_events = []
            for name in currently_active - previously_active:
                a = new_alerts[name]
                audit_events.append(("fire", name, a.severity, a.message, a.value, None))

            for name, old in self._alerts.items():
                if old.active and name not in new_alerts:
                    old.resolved_at = now
                    new_alerts[name] = old
                    duration = now - old.fired_at if old.fired_at else None
                    audit_events.append(("resolve", name, old.severity, f"resolved: {old.message}", duration, None))

            self._alerts = new_alerts
            self._last_eval = now

        # Flush audit events outside the lock
        for ev in audit_events:
            try:
                if ev[0] == "fire":
                    _audit.log_fire(ev[1], ev[2], message=ev[3], metric_value=ev[4])
                else:
                    _audit.log_resolve(ev[1], message=ev[3], duration_seconds=ev[4], severity=ev[2])
            except Exception:
                logger.exception("Failed to write audit event")

        active = [a for a in self._alerts.values() if a.active]
        if active:
            logger.warning("Active alerts: %s", [a.name for a in active])
        return active

    def get_all(self) -> list[dict]:
        with _status_lock:
            return [a.to_dict() for a in self._alerts.values()]

    def get_active(self) -> list[dict]:
        self.evaluate()
        with _status_lock:
            return [a.to_dict() for a in self._alerts.values() if a.active]


_alert_manager: AlertManager | None = None


def get_alert_manager() -> AlertManager | None:
    return _alert_manager


def init_alert_manager() -> AlertManager:
    global _alert_manager
    _alert_manager = AlertManager()
    logger.info("AlertManager initialized (eval interval: %ds)", _alert_manager._eval_interval)
    return _alert_manager
