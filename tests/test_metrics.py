"""Metrics endpoint tests."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task
from mio_taskhub.observability.metrics import (
    DEFAULT_TERMINAL_WINDOW_DAYS, _terminal_window_days,
)
from sqlmodel import Session

client = TestClient(app, raise_server_exceptions=False)

def _seed_task():
    """Insert a minimal task so counts > 0."""
    with Session(engine) as s:
        t = Task(id="metrics-test-1", title="seed", state="QUEUED")
        s.add(t)
        s.commit()

def test_metrics_returns_prometheus_format():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "taskhub_tasks_total" in text
    assert "taskhub_uptime_seconds" in text
    assert "# HELP" in text

def test_metrics_counts():
    _seed_task()
    resp = client.get("/metrics")
    text = resp.text
    assert "taskhub_tasks_total{state=" in text

def test_metrics_db_failure():
    """metrics should still return valid output even if DB queries fail."""
    client_local = TestClient(app, raise_server_exceptions=False)

    def fail_exec(*args, **kwargs):
        raise ConnectionError("DB down")

    with patch.object(Session, 'exec', fail_exec):
        resp = client_local.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "taskhub_uptime_seconds" in text


# ---------- 终态口径（task b1667eae：success_rate 长期 critical 的根因修复） ----------

def _gauge(text, name):
    for line in text.splitlines():
        if line.startswith(name + " "):
            return float(line.rsplit(" ", 1)[1])
    return None


def _seed_terminal(task_id, state, *, days_ago=0, labels=None):
    """插入一条已进终态的任务，last_transition_at 相对今天回溯 days_ago 天。"""
    with Session(engine) as s:
        when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
        s.add(Task(id=task_id, title="seed", state=state, labels=labels or [],
                   last_transition_at=when, completed_at=when, failed_at=when))
        s.commit()


def test_success_rate_excludes_cancelled(monkeypatch):
    """取消是独立终态（已有 cancel_rate 单独度量），不得算进 success 分母。"""
    monkeypatch.setenv("MIO_TASKHUB_TERMINAL_WINDOW_DAYS", "30")
    for i in range(9):
        _seed_terminal(f"ok-{i}", "COMPLETED")
    _seed_terminal("cancelled-1", "CANCELLED")
    text = client.get("/metrics").text
    # 9 完成 / 1 取消 → 成功率应为 1.0（旧口径会是 0.9）
    assert _gauge(text, "taskhub_task_success_rate") == 1.0
    assert _gauge(text, "taskhub_task_cancel_rate") == 0.1
    assert _gauge(text, "taskhub_task_attempted_total") == 9


def test_success_rate_counts_failed_in_denominator():
    _seed_terminal("c-1", "COMPLETED")
    _seed_terminal("c-2", "COMPLETED")
    _seed_terminal("c-3", "COMPLETED")
    _seed_terminal("f-1", "FAILED")
    text = client.get("/metrics").text
    assert _gauge(text, "taskhub_task_success_rate") == 0.75
    assert _gauge(text, "taskhub_task_failure_rate") == 0.25


def test_window_excludes_old_terminal_tasks(monkeypatch):
    """滚动窗口：被历史噪音污染的旧任务不应让指标永久不可恢复。"""
    monkeypatch.setenv("MIO_TASKHUB_TERMINAL_WINDOW_DAYS", "7")
    _seed_terminal("old-1", "CANCELLED", days_ago=90)
    _seed_terminal("old-2", "CANCELLED", days_ago=90)
    _seed_terminal("new-1", "COMPLETED")
    text = client.get("/metrics").text
    assert _gauge(text, "taskhub_task_success_rate") == 1.0
    assert _gauge(text, "taskhub_task_terminal_window_days") == 7


def test_invalid_window_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MIO_TASKHUB_TERMINAL_WINDOW_DAYS", "abc")
    assert _terminal_window_days() == DEFAULT_TERMINAL_WINDOW_DAYS
    monkeypatch.setenv("MIO_TASKHUB_TERMINAL_WINDOW_DAYS", "0")
    assert _terminal_window_days() == DEFAULT_TERMINAL_WINDOW_DAYS
    monkeypatch.setenv("MIO_TASKHUB_TERMINAL_WINDOW_DAYS", "14")
    assert _terminal_window_days() == 14


def test_success_and_failure_rate_are_complementary():
    _seed_terminal("c-1", "COMPLETED")
    _seed_terminal("f-1", "FAILED")
    _seed_terminal("f-2", "FAILED")
    text = client.get("/metrics").text
    assert (_gauge(text, "taskhub_task_success_rate")
            + _gauge(text, "taskhub_task_failure_rate")) == 1.0


def test_no_terminal_tasks_reports_zero_not_null():
    """空库时显式输出 0，避免前端显示 —。"""
    text = client.get("/metrics").text
    assert _gauge(text, "taskhub_task_success_rate") == 0.0
    assert _gauge(text, "taskhub_task_terminal_total") == 0


# ---------- active 口径（task 78d5fac5：指标被自己的告警闭环压低） ----------

def test_active_rate_excludes_insight_auto_noise():
    """insight-auto 跟进任务是控制面产物，其失败不得计入交付成功率。"""
    _seed_terminal("real-1", "COMPLETED")
    _seed_terminal("real-2", "COMPLETED")
    _seed_terminal("real-3", "COMPLETED")
    _seed_terminal("real-f-1", "FAILED")
    _seed_terminal("noise-f-1", "FAILED", labels=["insight-auto", "metric:x"])
    _seed_terminal("noise-f-2", "FAILED", labels=["insight-auto", "metric:x"])
    _seed_terminal("noise-c-1", "COMPLETED", labels=["insight-auto", "metric:x"])
    text = client.get("/metrics").text
    # 主口径已排除监控噪音（task e83cc9e2）：真实交付 3/4 = 0.75；
    # active 口径与主口径同义（此处无 archived:spinning）。
    assert _gauge(text, "taskhub_task_success_rate") == 0.75
    assert _gauge(text, "taskhub_task_failure_rate") == 0.25
    assert _gauge(text, "taskhub_task_attempted_total") == 4
    assert _gauge(text, "taskhub_task_success_rate_active") == 0.75
    assert _gauge(text, "taskhub_task_failure_rate_active") == 0.25
    assert _gauge(text, "taskhub_task_attempted_total_active") == 4
    # 被排除的监控工件单独暴露，便于审计；监控自身完成率 1/3 = 0.3333。
    assert _gauge(text, "taskhub_task_excluded_monitoring_total") == 3
    assert _gauge(text, "taskhub_task_monitoring_success_rate") == 0.3333


def test_active_rate_excludes_archived_spinning():
    """housekeeping 自动归档任务（archived:spinning）不是交付失败。"""
    _seed_terminal("real-1", "COMPLETED")
    _seed_terminal("spin-1", "FAILED", labels=["auto", "archived:spinning"])
    text = client.get("/metrics").text
    assert _gauge(text, "taskhub_task_success_rate_active") == 1.0
    assert _gauge(text, "taskhub_task_attempted_total_active") == 1


def test_active_rate_zero_when_only_noise():
    """窗口内只剩噪音任务时显式输出 0，不留 null。"""
    _seed_terminal("noise-f-1", "FAILED", labels=["insight-auto", "metric:x"])
    text = client.get("/metrics").text
    assert _gauge(text, "taskhub_task_success_rate_active") == 0.0
    assert _gauge(text, "taskhub_task_attempted_total_active") == 0


def test_active_rate_shares_same_window(monkeypatch):
    """active 与全量口径共用同一滚动窗口，避免两套口径时间范围不一致。"""
    monkeypatch.setenv("MIO_TASKHUB_TERMINAL_WINDOW_DAYS", "7")
    _seed_terminal("old-ok", "COMPLETED", days_ago=90)
    _seed_terminal("old-bad", "FAILED", days_ago=90)
    _seed_terminal("new-ok", "COMPLETED")
    _seed_terminal("new-bad", "FAILED")
    text = client.get("/metrics").text
    assert _gauge(text, "taskhub_task_terminal_window_days") == 7
    # 90 天前的 2 条被窗口排除，窗口内 1 成 1 败
    assert _gauge(text, "taskhub_task_attempted_total_active") == 2
    assert _gauge(text, "taskhub_task_success_rate_active") == 0.5


# ---------- 主口径排除监控自造任务（task e83cc9e2 变更 1，唯一能清 critical 的改动） ----------

def test_primary_rate_excludes_insight_auto_and_followup():
    """insight-auto/insight-followup 是控制面产物，失败不得计入主口径。

    主口径是 alert_rules + InsightsEngine 读取的那一行，必须与 active 同口径
    排除噪音，否则 failure_rate 永久 critical、每周期再生 critical insight。
    """
    _seed_terminal("real-1", "COMPLETED")
    _seed_terminal("real-2", "COMPLETED")
    _seed_terminal("real-3", "COMPLETED")
    _seed_terminal("real-f-1", "FAILED")
    _seed_terminal("noise-f-1", "FAILED", labels=["insight-auto", "metric:x"])
    _seed_terminal("noise-f-2", "FAILED", labels=["insight-followup", "metric:x"])
    _seed_terminal("noise-c-1", "COMPLETED", labels=["insight-auto", "metric:x"])
    text = client.get("/metrics").text
    # 主口径 = 真实交付 3/4 = 0.75；failure 0.25（<0.15 已不成立 critical）
    assert _gauge(text, "taskhub_task_success_rate") == 0.75
    assert _gauge(text, "taskhub_task_failure_rate") == 0.25
    assert _gauge(text, "taskhub_task_attempted_total") == 4
    # 被排除的监控工件单独暴露（3 条：2 failed + 1 completed）
    assert _gauge(text, "taskhub_task_excluded_monitoring_total") == 3
    assert _gauge(text, "taskhub_task_monitoring_success_rate") == 0.3333


def test_primary_rate_zero_excluded_when_no_monitoring_noise():
    """无监控噪音时 excluded=0、monitoring_success_rate=0，主口径为纯交付口径。"""
    _seed_terminal("real-1", "COMPLETED")
    _seed_terminal("real-2", "FAILED")
    text = client.get("/metrics").text
    assert _gauge(text, "taskhub_task_success_rate") == 0.5
    assert _gauge(text, "taskhub_task_excluded_monitoring_total") == 0
    assert _gauge(text, "taskhub_task_monitoring_success_rate") == 0.0


def test_primary_rate_null_labels_not_excluded():
    """labels 为 NULL 的任务属正常任务，不得被监控过滤误排除。"""
    with Session(engine) as s:
        when = datetime.now(timezone.utc).replace(tzinfo=None)
        s.add(Task(id="null-label-1", title="seed", state="FAILED",
                   labels=None, last_transition_at=when,
                   completed_at=when, failed_at=when))
        s.commit()
    text = client.get("/metrics").text
    assert _gauge(text, "taskhub_task_failure_rate") == 1.0
    assert _gauge(text, "taskhub_task_attempted_total") == 1
