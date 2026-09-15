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