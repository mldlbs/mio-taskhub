# -*- coding: utf-8 -*-
"""闭环洞察消费测试（task b71206fe / P2-4）。

验证：critical 未确认洞察 → 建跟进任务（幂等/开关/severity/已确认不触发）；
消费动作记入 remediation log。
"""
import uuid

from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskState
from mio_taskhub.observability.insight_remediator import (
    InsightsRemediator, INSIGHT_LABEL, TITLE_PREFIX, autotask_enabled,
)


def _insight(metric="taskhub_task_failure_rate", sev="critical", ack=0, val=0.24):
    return {
        "id": 1, "kind": "anomaly", "severity": sev, "acknowledged": ack,
        "metric_name": metric, "metric_value": val, "baseline": 0.15,
        "title": f"Critical: {metric}", "description": f"{metric} = {val}",
        "recommendation": "investigate",
    }


def _followups(title_prefix=TITLE_PREFIX):
    with Session(engine) as db:
        return db.exec(select(Task).where(Task.title.startswith(title_prefix))).all()


def test_critical_insight_creates_followup(monkeypatch):
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    r = InsightsRemediator()
    created = r.consume([_insight()])
    assert len(created) == 1
    assert created[0].title.startswith(TITLE_PREFIX)
    assert INSIGHT_LABEL in (created[0].labels or [])
    assert created[0].state == TaskState.QUEUED


def test_dedup_same_metric_not_recreated(monkeypatch):
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    r = InsightsRemediator()
    r.consume([_insight()])
    r.consume([_insight()])  # 再来一次不应重复建
    assert len(_followups()) == 1


def test_disabled_env_creates_nothing(monkeypatch):
    monkeypatch.setenv("MIO_INSIGHT_AUTOTASK", "0")
    assert autotask_enabled() is False
    created = InsightsRemediator().consume([_insight()])
    assert created == []
    assert len(_followups()) == 0


def test_non_critical_not_consumed_by_default(monkeypatch):
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK_SEVERITY", raising=False)
    created = InsightsRemediator().consume([_insight(sev="warning")])
    assert created == []
    assert len(_followups()) == 0


def test_warning_consumed_when_configured(monkeypatch):
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    monkeypatch.setenv("MIO_INSIGHT_AUTOTASK_SEVERITY", "critical,warning")
    created = InsightsRemediator().consume([_insight(sev="warning")])
    assert len(created) == 1


def test_acknowledged_insight_not_consumed(monkeypatch):
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    created = InsightsRemediator().consume([_insight(ack=1)])
    assert created == []
    assert len(_followups()) == 0


def test_consumption_logged_as_remediation(monkeypatch):
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    InsightsRemediator().consume([_insight(metric="taskhub_task_success_rate")])
    from mio_taskhub.observability.remediation import RemediationEngine
    logs = RemediationEngine().recent(limit=20)
    assert any("insight_autotask" in (l.get("title") or "") for l in logs)
