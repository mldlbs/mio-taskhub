# -*- coding: utf-8 -*-
"""闭环洞察消费测试（task b71206fe / P2-4）。

验证：critical 未确认洞察 → 建跟进任务（幂等/开关/severity/已确认不触发）；
消费动作记入 remediation log。
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select, text

from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskState
from mio_taskhub.observability.insight_remediator import (
    DEFAULT_COOLDOWN_HOURS, InsightsRemediator, INSIGHT_LABEL, TITLE_PREFIX,
    autotask_enabled, precheck_enabled, _cooldown_hours,
)


def _no_precheck(monkeypatch):
    """关闭派生前实时指标复核，隔离单测对线上 /metrics 的依赖。"""
    monkeypatch.setenv("MIO_INSIGHT_AUTOTASK_PRECHECK", "0")


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
    _no_precheck(monkeypatch)
    r = InsightsRemediator()
    created = r.consume([_insight()])
    assert len(created) == 1
    assert created[0].title.startswith(TITLE_PREFIX)
    assert INSIGHT_LABEL in (created[0].labels or [])
    assert created[0].state == TaskState.QUEUED


def test_dedup_same_metric_not_recreated(monkeypatch):
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    _no_precheck(monkeypatch)
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
    _no_precheck(monkeypatch)
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
    _no_precheck(monkeypatch)
    InsightsRemediator().consume([_insight(metric="taskhub_task_success_rate")])
    from mio_taskhub.observability.remediation import RemediationEngine
    logs = RemediationEngine().recent(limit=20)
    assert any("insight_autotask" in (l.get("title") or "") for l in logs)


# ---------- 冷却期：切断 insight-auto 自我喂养（task b1667eae 复核结论） ----------

def _seed_followup(title, state, age_hours):
    with Session(engine) as db:
        last = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=age_hours)
        t = Task(title=title, state=state, labels=[INSIGHT_LABEL], last_transition_at=last)
        db.add(t)
        db.commit()
        db.refresh(t)
        return t.id


def test_completed_followup_suppresses_recreate_within_cooldown(monkeypatch):
    """上一轮已完成 → 冷却期内不得重建（否则 60s 后自我喂养）。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK_COOLDOWN_HOURS", raising=False)
    title = f"{TITLE_PREFIX} taskhub_task_failure_rate"
    _seed_followup(title, TaskState.COMPLETED, age_hours=1)
    assert InsightsRemediator().consume([_insight()]) == []
    assert len(_followups()) == 1


def test_completed_followup_allows_recreate_after_cooldown(monkeypatch):
    """冷却期过后仍可重建，说明去重是窗口而非永久封禁。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    _no_precheck(monkeypatch)
    monkeypatch.setenv("MIO_INSIGHT_AUTOTASK_COOLDOWN_HOURS", "24")
    title = f"{TITLE_PREFIX} taskhub_task_failure_rate"
    _seed_followup(title, TaskState.COMPLETED, age_hours=48)
    assert len(InsightsRemediator().consume([_insight()])) == 1
    assert len(_followups()) == 2


def test_cooldown_zero_disables_suppression(monkeypatch):
    """冷却期 0 → 退回旧行为（只挡未完成任务），便于紧急排障。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    _no_precheck(monkeypatch)
    monkeypatch.setenv("MIO_INSIGHT_AUTOTASK_COOLDOWN_HOURS", "0")
    title = f"{TITLE_PREFIX} taskhub_task_failure_rate"
    _seed_followup(title, TaskState.COMPLETED, age_hours=1)
    assert len(InsightsRemediator().consume([_insight()])) == 1


def test_invalid_cooldown_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MIO_INSIGHT_AUTOTASK_COOLDOWN_HOURS", "not-a-number")
    assert _cooldown_hours() == DEFAULT_COOLDOWN_HOURS


def test_cancelled_followup_still_suppressed_within_cooldown(monkeypatch):
    """CANCELLED/FAILED 跟进任务同样进入冷却，避免反复派活。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK_COOLDOWN_HOURS", raising=False)
    title = f"{TITLE_PREFIX} taskhub_task_failure_rate"
    _seed_followup(title, TaskState.CANCELLED, age_hours=1)
    assert InsightsRemediator().consume([_insight()]) == []
    assert len(_followups()) == 1


def test_completed_followup_acknowledges_source_insight(monkeypatch):
    """跟进任务完成 → 源洞察 acknowledged，insights.py 不再重复上报。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK_COOLDOWN_HOURS", raising=False)
    with Session(engine) as db:
        db.exec(text(
            "INSERT INTO insight (ts, kind, title, description, severity, metric_name, "
            "metric_value, baseline, acknowledged) VALUES (0, 'anomaly', 'Critical: x', "
            "'x = 0.24', 'critical', 'taskhub_task_failure_rate', 0.24, 0.15, 0)"
        ))
        db.commit()
        row = db.exec(text(
            "SELECT id FROM insight WHERE metric_name='taskhub_task_failure_rate'"
        )).first()
        ins_id = row[0]

    _seed_followup(f"{TITLE_PREFIX} taskhub_task_failure_rate", TaskState.COMPLETED, age_hours=1)
    InsightsRemediator().consume([_insight(ack=0) | {"id": ins_id}])

    with Session(engine) as db:
        got = db.exec(text("SELECT acknowledged FROM insight WHERE id = :i"),
                      params={"i": ins_id}).first()[0]
    assert got == 1


# ---------- 变更 2：终态即 ack（task e83cc9e2） ----------

def _fresh_insight(metric="taskhub_task_failure_rate", val=0.24):
    """插入一条未确认洞察并返回其 id，用于验证 ack 行为。"""
    with Session(engine) as db:
        db.exec(text(
            "INSERT INTO insight (ts, kind, title, description, severity, metric_name, "
            "metric_value, baseline, acknowledged) VALUES (0, 'anomaly', 'Critical: x', "
            "'x = 0.24', 'critical', :m, :v, 0.15, 0)"
        ), params={"m": metric, "v": val})
        db.commit()
        return db.exec(text(
            "SELECT id FROM insight WHERE metric_name = :m ORDER BY id DESC LIMIT 1"
        ), params={"m": metric}).first()[0]


def test_failed_followup_acknowledges_source_insight(monkeypatch):
    """FAILED 终态也 ack：否则源洞察永不确认、冷却期满无限慢环派生。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    _no_precheck(monkeypatch)
    ins_id = _fresh_insight()
    _seed_followup(f"{TITLE_PREFIX} taskhub_task_failure_rate", TaskState.FAILED, age_hours=1)
    assert InsightsRemediator().consume([_insight() | {"id": ins_id}]) == []
    with Session(engine) as db:
        got = db.exec(text("SELECT acknowledged FROM insight WHERE id = :i"),
                      params={"i": ins_id}).first()[0]
    assert got == 1


def test_cancelled_followup_acknowledges_source_insight(monkeypatch):
    """CANCELLED 终态同样 ack（早期只 ack COMPLETED 的结构性缺陷）。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    _no_precheck(monkeypatch)
    ins_id = _fresh_insight()
    _seed_followup(f"{TITLE_PREFIX} taskhub_task_failure_rate", TaskState.CANCELLED, age_hours=1)
    assert InsightsRemediator().consume([_insight() | {"id": ins_id}]) == []
    with Session(engine) as db:
        got = db.exec(text("SELECT acknowledged FROM insight WHERE id = :i"),
                      params={"i": ins_id}).first()[0]
    assert got == 1


# ---------- 变更 3：派生前复核实时指标（task e83cc9e2） ----------

def test_precheck_skips_and_acks_when_metric_recovered(monkeypatch):
    """实时值已回 baseline 以内 → ack 源洞察且不派生（防 8.7h 延迟快照派单）。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    monkeypatch.setenv("MIO_INSIGHT_AUTOTASK_PRECHECK", "1")
    ins_id = _fresh_insight(val=0.24)
    import mio_taskhub.observability.insight_remediator as mod
    monkeypatch.setattr(mod, "_current_metrics",
                        lambda: {"taskhub_task_failure_rate": 0.02})
    created = InsightsRemediator().consume([_insight() | {"id": ins_id}])
    assert created == []
    assert len(_followups()) == 0
    with Session(engine) as db:
        got = db.exec(text("SELECT acknowledged FROM insight WHERE id = :i"),
                      params={"i": ins_id}).first()[0]
    assert got == 1


def test_precheck_creates_when_metric_still_breaching(monkeypatch):
    """实时值仍超阈值 → 正常派生（复核不误吞仍成立的告警）。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    monkeypatch.setenv("MIO_INSIGHT_AUTOTASK_PRECHECK", "1")
    import mio_taskhub.observability.insight_remediator as mod
    monkeypatch.setattr(mod, "_current_metrics",
                        lambda: {"taskhub_task_failure_rate": 0.31})
    created = InsightsRemediator().consume([_insight()])
    assert len(created) == 1


def test_precheck_disabled_env_ignores_recovery(monkeypatch):
    """env 关闭复核 → 即使实时值已恢复也照常派生（隔离/紧急排障）。"""
    monkeypatch.delenv("MIO_INSIGHT_AUTOTASK", raising=False)
    monkeypatch.setenv("MIO_INSIGHT_AUTOTASK_PRECHECK", "0")
    assert precheck_enabled() is False
    import mio_taskhub.observability.insight_remediator as mod
    monkeypatch.setattr(mod, "_current_metrics",
                        lambda: {"taskhub_task_failure_rate": 0.0})
    created = InsightsRemediator().consume([_insight()])
    assert len(created) == 1
