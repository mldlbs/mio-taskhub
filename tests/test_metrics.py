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


def _seed_terminal(task_id, state, *, days_ago=0):
    """插入一条已进终态的任务，last_transition_at 相对今天回溯 days_ago 天。"""
    with Session(engine) as s:
        when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
        s.add(Task(id=task_id, title="seed", state=state,
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
