"""Custom alert rules: user-definable thresholds + notification channels."""
import json
import logging
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable
from sqlmodel import SQLModel, Field, Session, Column, JSON, text
from mio_taskhub.db import engine
from mio_taskhub.observability.alerts import Alert, get_alert_manager
from mio_taskhub.observability.metrics import render_metrics

logger = logging.getLogger("mio_taskhub.observability.alert_rules")


class NotifyChannel(str, Enum):
    WEBHOOK = "webhook"
    EMAIL = "email"
    DINGTALK = "dingtalk"


class AlertRuleMetric(str, Enum):
    TASK_FAILURE_RATE = "task_failure_rate"
    TASK_SUCCESS_RATE = "task_success_rate"
    HTTP_ERROR_RATE = "http_error_rate"
    ACTIVE_REQUESTS = "active_requests"
    CPU_PERCENT = "cpu_percent"
    MEMORY_PERCENT = "memory_percent"
    DB_POOL_UTILIZATION = "db_pool_utilization"
    THREAD_FAILURES = "thread_failures"
    DEP_LATENCY_P90 = "dep_latency_p90"
    DEP_ERROR_RATE = "dep_error_rate"
    SLO_AVAILABILITY = "slo_availability"
    TASK_STALLED = "task_stalled"
    UPTIME = "uptime"


class AlertRuleSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertRule(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=lambda: __import__("uuid").uuid4().hex[:8], primary_key=True)
    name: str = Field(index=True)
    metric: str  # AlertRuleMetric value
    condition: str  # gt, lt, eq, gte, lte
    threshold: float
    severity: str = "warning"  # AlertRuleSeverity
    enabled: bool = True
    cooldown_seconds: int = 300
    last_fired_at: Optional[float] = None
    notify_channels: Optional[list] = Field(default=None, sa_column=Column(JSON))
    notify_config: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


def _parse_prometheus_value(lines: str, metric_name: str, labels: dict = None) -> Optional[float]:
    """Extract a single metric value from Prometheus text format."""
    for line in lines.split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        if metric_name not in line:
            continue
        if labels:
            match = True
            for k, v in labels.items():
                if f'{k}="{v}"' not in line:
                    match = False
                    break
            if not match:
                continue
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            try:
                return float(parts[1])
            except ValueError:
                pass
    return None


def _evaluate_metric(rule: AlertRule) -> Optional[float]:
    """Fetch current value for the rule's metric."""
    lines = render_metrics()
    m = rule.metric
    cond = rule.condition
    threshold = rule.threshold

    if m == AlertRuleMetric.TASK_FAILURE_RATE:
        val = _parse_prometheus_value(lines, "taskhub_task_failure_rate")
    elif m == AlertRuleMetric.TASK_SUCCESS_RATE:
        val = _parse_prometheus_value(lines, "taskhub_task_success_rate")
    elif m == AlertRuleMetric.HTTP_ERROR_RATE:
        total = _parse_prometheus_value(lines, "taskhub_http_request_count_total") or 0
        if total < 10:
            return None
        errors = sum(
            float(line.split()[-1])
            for line in lines.split("\n")
            if "taskhub_http_errors_total" in line and not line.startswith("#")
        )
        val = errors / total if total > 0 else 0
    elif m == AlertRuleMetric.ACTIVE_REQUESTS:
        val = _parse_prometheus_value(lines, "taskhub_http_active_requests")
    elif m == AlertRuleMetric.CPU_PERCENT:
        val = _parse_prometheus_value(lines, "taskhub_process_cpu_percent")
    elif m == AlertRuleMetric.MEMORY_PERCENT:
        val = _parse_prometheus_value(lines, "taskhub_process_memory_percent")
    elif m == AlertRuleMetric.DB_POOL_UTILIZATION:
        val = _parse_prometheus_value(lines, "taskhub_db_pool_utilization")
    elif m == AlertRuleMetric.THREAD_FAILURES:
        val = _parse_prometheus_value(lines, "taskhub_thread_consecutive_failures")
    elif m == AlertRuleMetric.SLO_AVAILABILITY:
        val = _parse_prometheus_value(lines, "taskhub_slo_availability_30d")
    elif m == AlertRuleMetric.UPTIME:
        val = _parse_prometheus_value(lines, "taskhub_uptime_seconds")
    else:
        return None
    return val


def _check_condition(val: float, condition: str, threshold: float) -> bool:
    ops = {
        "gt": lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
        "eq": lambda a, b: abs(a - b) < 1e-9,
    }
    return ops.get(condition, lambda a, b: False)(val, threshold)


def _send_webhook(url: str, payload: dict, timeout: int = 10):
    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except Exception as e:
        logger.error("Webhook failed: %s", e)
        return None


def _send_email(config: dict, subject: str, body: str):
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.get("from", "")
    msg["To"] = config.get("to", "")
    try:
        with smtplib.SMTP(config.get("host", "localhost"), config.get("port", 25)) as s:
            if config.get("tls"):
                s.starttls()
            if config.get("username"):
                s.login(config["username"], config["password"])
            s.send_message(msg)
            return True
    except Exception as e:
        logger.error("Email failed: %s", e)
        return False


def _send_dingtalk(webhook_url: str, title: str, text: str):
    import urllib.request
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
    }
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except Exception as e:
        logger.error("DingTalk failed: %s", e)
        return None


def _notify(rule: AlertRule, alert: Alert):
    channels = rule.notify_channels or []
    config = rule.notify_config or {}
    title = f"[{alert.severity.upper()}] {alert.name}"
    body = f"Alert: {alert.name}\nSeverity: {alert.severity}\nMessage: {alert.message}\nValue: {alert.value}"

    for ch in channels:
        if ch == NotifyChannel.WEBHOOK:
            url = config.get("webhook_url")
            if url:
                _send_webhook(url, {"title": title, "body": body, "alert": alert.to_dict()})
        elif ch == NotifyChannel.EMAIL:
            _send_email(config.get("email", {}), title, body)
        elif ch == NotifyChannel.DINGTALK:
            url = config.get("dingtalk_webhook")
            if url:
                _send_dingtalk(url, title, body)


class CustomAlertEvaluator:
    def __init__(self):
        self._last_eval = 0.0
        self._eval_interval = 30
        self._fired_cooldown: dict[str, float] = {}

    def evaluate(self):
        now = time.time()
        if now - self._last_eval < self._eval_interval:
            return
        self._last_eval = now

        try:
            with Session(engine) as db:
                rules = db.exec(text("SELECT * FROM alertrule WHERE enabled = 1")).all()
        except Exception:
            return

        for row in rules:
            rule = AlertRule(
                id=row.id, name=row.name, metric=row.metric,
                condition=row.condition, threshold=row.threshold,
                severity=row.severity, enabled=row.enabled,
                cooldown_seconds=row.cooldown_seconds,
                last_fired_at=row.last_fired_at,
                notify_channels=json.loads(row.notify_channels) if row.notify_channels else None,
                notify_config=json.loads(row.notify_config) if row.notify_config else None,
            )
            val = _evaluate_metric(rule)
            if val is None:
                continue

            if _check_condition(val, rule.condition, rule.threshold):
                last_fired = self._fired_cooldown.get(rule.id, 0)
                if now - last_fired < rule.cooldown_seconds:
                    continue

                alert = Alert(
                    name=f"Custom_{rule.name}",
                    severity=rule.severity,
                    message=f"{rule.metric} {rule.condition} {rule.threshold} (current: {val:.4f})",
                    fired_at=now,
                    value=val,
                    labels={"rule_id": rule.id, "metric": rule.metric},
                )

                mgr = get_alert_manager()
                if mgr:
                    mgr._alerts[alert.name] = alert

                self._fired_cooldown[rule.id] = now

                try:
                    with Session(engine) as db:
                        db.exec(
                            text("UPDATE alertrule SET last_fired_at = :t WHERE id = :id"),
                            {"t": now, "id": rule.id},
                        )
                        db.commit()
                except Exception:
                    pass

                _notify(rule, alert)
                logger.warning("Custom alert fired: %s (val=%.4f)", rule.name, val)


_evaluator: CustomAlertEvaluator | None = None


def get_custom_evaluator() -> CustomAlertEvaluator | None:
    return _evaluator


def init_custom_evaluator() -> CustomAlertEvaluator:
    global _evaluator
    _evaluator = CustomAlertEvaluator()
    logger.info("CustomAlertEvaluator initialized")
    return _evaluator
