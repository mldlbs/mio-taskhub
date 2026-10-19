import time
import pytest
from mio_taskhub.db import init_db

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield

def test_insights_engine_stores_insights():
    from mio_taskhub.observability.insights import InsightsEngine
    engine = InsightsEngine()
    result = engine.store("test_kind", "Test Title", "Test description", severity="warning", recommendation="Do something")
    recent = engine.recent(limit=10)
    assert len(recent) >= 1
    assert recent[0]["title"] == "Test Title"

def test_insights_engine_recent_limit():
    from mio_taskhub.observability.insights import InsightsEngine
    engine = InsightsEngine()
    rows = engine.recent(limit=3)
    assert len(rows) <= 3

def test_insights_engine_acknowledge():
    from mio_taskhub.observability.insights import InsightsEngine
    engine = InsightsEngine()
    result = engine.store("test", "Ack Test", "desc", severity="info")
    engine.acknowledge(result["id"])
    recent = engine.recent(limit=10)
    acknowledged = [r for r in recent if r["id"] == result["id"]]
    assert len(acknowledged) == 1
    assert acknowledged[0]["acknowledged"] == 1

def test_insights_engine_evaluate():
    from mio_taskhub.observability.insights import InsightsEngine
    engine = InsightsEngine()
    metrics = {"taskhub_task_failure_rate": 0.15}
    insights = engine.evaluate(metrics)
    assert isinstance(insights, list)
    # Should detect critical anomaly
    critical = [i for i in insights if i["severity"] == "critical"]
    assert len(critical) >= 1


# ---------- 变更 4：dedup 刷新 + 恢复检测（task e83cc9e2） ----------

def _ack_of(engine, insight_id):
    from mio_taskhub.db import engine as db_engine
    from sqlalchemy import text
    with db_engine.connect() as conn:
        row = conn.execute(text("SELECT acknowledged FROM insight WHERE id = :i"),
                           {"i": insight_id}).fetchone()
    return row[0]


def test_dedup_refreshes_metric_value():
    """命中未确认同 title 洞察 → 刷新 metric_value（不留化石快照）。"""
    from mio_taskhub.observability.insights import InsightsEngine
    engine = InsightsEngine()
    first = engine.store(
        "anomaly", "Critical: taskhub_task_failure_rate", "x = 0.25",
        severity="critical", metric_name="taskhub_task_failure_rate",
        metric_value=0.25, baseline=0.15,
    )
    second = engine.store(
        "anomaly", "Critical: taskhub_task_failure_rate", "x = 0.37",
        severity="critical", metric_name="taskhub_task_failure_rate",
        metric_value=0.37, baseline=0.15,
    )
    # 同一行被复用（dedup），值已刷新到 0.37
    assert second["id"] == first["id"]
    from mio_taskhub.db import engine as db_engine
    from sqlalchemy import text
    with db_engine.connect() as conn:
        row = conn.execute(text("SELECT metric_value FROM insight WHERE id = :i"),
                           {"i": first["id"]}).fetchone()
    assert abs(row[0] - 0.37) < 1e-9


def test_store_marks_recovered_when_value_within_threshold():
    """恢复检测：值回到 baseline 以内 → 该行标 acknowledged（不再驻留）。"""
    from mio_taskhub.observability.insights import InsightsEngine
    engine = InsightsEngine()
    first = engine.store(
        "anomaly", "Critical: taskhub_task_failure_rate", "x = 0.25",
        severity="critical", metric_name="taskhub_task_failure_rate",
        metric_value=0.25, baseline=0.15,
    )
    assert _ack_of(engine, first["id"]) == 0
    recovered = engine.store(
        "anomaly", "Critical: taskhub_task_failure_rate", "x = 0.02",
        severity="critical", metric_name="taskhub_task_failure_rate",
        metric_value=0.02, baseline=0.15,
    )
    assert recovered.get("recovered") is True
    assert _ack_of(engine, recovered["id"]) == 1


def test_store_not_recovered_when_still_breaching():
    """仍超阈值 → 保持未确认，避免误 ack 掉仍需处置的告警。"""
    from mio_taskhub.observability.insights import InsightsEngine
    engine = InsightsEngine()
    first = engine.store(
        "anomaly", "Critical: taskhub_task_failure_rate", "x = 0.25",
        severity="critical", metric_name="taskhub_task_failure_rate",
        metric_value=0.25, baseline=0.15,
    )
    still = engine.store(
        "anomaly", "Critical: taskhub_task_failure_rate", "x = 0.31",
        severity="critical", metric_name="taskhub_task_failure_rate",
        metric_value=0.31, baseline=0.15,
    )
    assert still.get("recovered") is False
    assert _ack_of(engine, first["id"]) == 0


def test_store_missing_baseline_not_recovered():
    """无 baseline 时保守判未恢复，不误 ack。"""
    from mio_taskhub.observability.insights import InsightsEngine
    engine = InsightsEngine()
    row = engine.store(
        "anomaly", "Critical: no-baseline", "x",
        severity="critical", metric_name="no_baseline", metric_value=0.5,
    )
    assert row.get("recovered") is False

def test_is_recovered_low_threshold_direction():
    """P1-OBS：低阈值洞察（success_rate low）不应被 `value < baseline` 误判为已恢复。"""
    from mio_taskhub.observability.insights import InsightsEngine
    e = InsightsEngine()
    # 低阈值型：0.86 < 0.90 仍在告警 → 未恢复
    assert e._is_recovered(0.86, 0.90, low=True) is False
    # 低阈值型：0.95 >= 0.90 → 恢复
    assert e._is_recovered(0.95, 0.90, low=True) is True
    # 高阈值型（默认）：0.03 < 0.05 → 恢复
    assert e._is_recovered(0.03, 0.05) is True
    # 高阈值型：0.10 >= 0.05 → 未恢复
    assert e._is_recovered(0.10, 0.05) is False


def test_low_insight_not_acked_when_still_breaching():
    """低阈值指标仍在告警时，重复 evaluate 不应产生新洞察（不误 ack）。"""
    from mio_taskhub.observability.insights import InsightsEngine
    from mio_taskhub.db import engine
    from sqlalchemy import text
    e = InsightsEngine()
    e.evaluate({"taskhub_task_success_rate": 0.86})   # 首次
    e.evaluate({"taskhub_task_success_rate": 0.86})   # 再次（同值）
    with engine.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM insight WHERE title='Warning: taskhub_task_success_rate low'")).fetchone()[0]
    assert n == 1, f"同值重复 evaluate 应去重为 1 条，实得 {n}"
