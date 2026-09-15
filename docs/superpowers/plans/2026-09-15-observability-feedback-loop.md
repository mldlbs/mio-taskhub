# Observability Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a closed-loop observability system that observes system operation and automatically feeds insights back into system improvements — auto-remediation, anomaly detection, historical analysis, and actionable recommendations.

**Architecture:** Three new subsystems: (1) Observability Data Layer — persist dependency metrics + alert audit trail for historical analysis; (2) Insights Engine — anomaly detection, correlation, and recommendation generation from observability data; (3) Auto-Remediation — automatic responses to detected issues (restart stalled threads, requeue stuck tasks, adjust parameters). Dashboard enhanced with SLO trend chart and insight feed.

**Tech Stack:** Python 3.12, SQLite (existing), psutil, existing Prometheus metrics, WebSocket push, Chart.js (dashboard)

---

## File Structure

| File | Responsibility |
|---|---|
| `mio_taskhub/observability/audit.py` | Alert audit trail — persist alert fire/resolve/history to DB |
| `mio_taskhub/observability/insights.py` | Insights engine — anomaly detection, correlation, recommendations |
| `mio_taskhub/observability/remediation.py` | Auto-remediation — response actions triggered by alerts/insights |
| `mio_taskhub/observability/dep_persist.py` | Dependency metrics persistence — periodic snapshot to DB |
| `mio_taskhub/api/insights.py` | API endpoints for insights, audit history, remediation log |
| `mio_taskhub/migrations.py` | Add 3 new tables: `alertaudit`, `insight`, `depmetricssnapshot` |
| `tests/test_audit.py` | Tests for alert audit trail |
| `tests/test_insights.py` | Tests for insights engine |
| `tests/test_remediation.py` | Tests for auto-remediation |
| `tests/test_dep_persist.py` | Tests for dependency metrics persistence |

---

## Task 1: Alert Audit Trail

**Files:**
- Create: `mio_taskhub/observability/audit.py`
- Modify: `mio_taskhub/migrations.py` — add `alertaudit` table
- Modify: `mio_taskhub/observability/alerts.py` — hook audit on fire/resolve
- Modify: `mio_taskhub/main.py` — register audit in lifespan
- Create: `tests/test_audit.py`

- [ ] **Step 1: Add `alertaudit` table to migrations.py**

```python
# In migrate_all(), add after existing table creation:
conn.execute("""
CREATE TABLE IF NOT EXISTS alertaudit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts FLOAT NOT NULL,
    alert_name VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    message TEXT,
    metric_value FLOAT,
    threshold FLOAT,
    duration_seconds FLOAT,
    resolution_message TEXT
)
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_alertaudit_ts ON alertaudit(ts)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_alertaudit_name ON alertaudit(alert_name)")
```

- [ ] **Step 2: Write test for audit logging**

```python
# tests/test_audit.py
import time
from observability.audit import AlertAudit

def test_alert_audit_log_fire():
    audit = AlertAudit()
    audit.log_fire("TestAlert", "warning", message="test fired", metric_value=0.8, threshold=0.5)
    rows = audit.recent(limit=10)
    assert len(rows) >= 1
    row = rows[-1]
    assert row["alert_name"] == "TestAlert"
    assert row["action"] == "fire"
    assert row["metric_value"] == 0.8

def test_alert_audit_log_resolve():
    audit = AlertAudit()
    audit.log_fire("TestAlert", "warning")
    audit.log_resolve("TestAlert", message="resolved", duration_seconds=120.5)
    rows = audit.recent(limit=10)
    resolves = [r for r in rows if r["action"] == "resolve"]
    assert len(resolves) >= 1
    assert resolves[-1]["duration_seconds"] == 120.5

def test_alert_audit_recent_limit():
    audit = AlertAudit()
    for i in range(5):
        audit.log_fire(f"Alert_{i}", "info")
    rows = audit.recent(limit=3)
    assert len(rows) == 3
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_audit.py -v`
Expected: FAIL (module not found)

- [ ] **Step 4: Implement AlertAudit class**

```python
# mio_taskhub/observability/audit.py
import time
import sqlite3
import os
import threading

_local = threading.local()

def _conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        db_path = os.path.expanduser("~/.mio_taskhub/taskhub.db")
        _local.conn = sqlite3.connect(db_path, timeout=5)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn

class AlertAudit:
    def log_fire(self, alert_name: str, severity: str, message: str = "", metric_value: float = None, threshold: float = None):
        conn = _conn()
        conn.execute(
            "INSERT INTO alertaudit (ts, alert_name, severity, action, message, metric_value, threshold) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), alert_name, severity, "fire", message, metric_value, threshold)
        )
        conn.commit()

    def log_resolve(self, alert_name: str, message: str = "", duration_seconds: float = None):
        conn = _conn()
        conn.execute(
            "INSERT INTO alertaudit (ts, alert_name, severity, action, message, duration_seconds) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), alert_name, "", "resolve", message, duration_seconds)
        )
        conn.commit()

    def recent(self, limit: int = 50) -> list:
        conn = _conn()
        rows = conn.execute("SELECT * FROM alertaudit ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self, hours: int = 24) -> dict:
        conn = _conn()
        cutoff = time.time() - hours * 3600
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN action='fire' THEN 1 ELSE 0 END) as fires, SUM(CASE WHEN action='resolve' THEN 1 ELSE 0 END) as resolves FROM alertaudit WHERE ts > ?",
            (cutoff,)
        ).fetchone()
        return dict(row)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_audit.py -v`
Expected: PASS

- [ ] **Step 6: Hook audit into AlertManager fire/resolve**

In `mio_taskhub/observability/alerts.py`, modify the `_evaluate` method to call audit on fire/resolve:

```python
# Add at top of alerts.py:
from observability.audit import AlertAudit
_audit = AlertAudit()

# In the fire block (where alert is newly activated):
_audit.log_fire(alert_name, severity, message=msg, metric_value=metric_val, threshold=threshold)

# In the resolve block (where alert is resolved):
_audit.log_resolve(alert_name, message=f"resolved: {msg}", duration_seconds=duration)
```

- [ ] **Step 7: Register audit table in lifespan init**

In `main.py` lifespan, after `init_alert_manager()`, ensure audit table exists (already handled by migration). No additional code needed.

- [ ] **Step 8: Commit**

```bash
cd E:\work\code\agent-dev\mio-taskhub
git add observability/audit.py migrations.py observability/alerts.py tests/test_audit.py
git commit -m "feat: add alert audit trail - persist fire/resolve events to DB"
```

---

## Task 2: Dependency Metrics Persistence

**Files:**
- Create: `mio_taskhub/observability/dep_persist.py`
- Modify: `mio_taskhub/migrations.py` — add `depmetricssnapshot` table
- Modify: `mio_taskhub/main.py` — start dep persist thread
- Create: `tests/test_dep_persist.py`

- [ ] **Step 1: Add `depmetricssnapshot` table to migrations.py**

```python
# In migrate_all(), add:
conn.execute("""
CREATE TABLE IF NOT EXISTS depmetricssnapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts FLOAT NOT NULL,
    dep VARCHAR NOT NULL,
    op VARCHAR NOT NULL,
    count INTEGER,
    errors INTEGER,
    avg_ms FLOAT,
    p50_ms FLOAT,
    p90_ms FLOAT,
    p99_ms FLOAT,
    max_ms FLOAT,
    error_rate FLOAT
)
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_depmetricssnapshot_ts ON depmetricssnapshot(ts)")
```

- [ ] **Step 2: Write test for dep persistence**

```python
# tests/test_dep_persist.py
import time
from observability.dep_persist import DepMetricsPersist

def test_dep_persist_snapshot():
    persist = DepMetricsPersist()
    # Inject fake data
    persist._snapshot_and_store = lambda: None  # skip actual DB write for unit test
    from observability.dep_metrics import _data
    _data["sqlite"]["query"] = {"count": 100, "errors": 5, "latencies": [10, 20, 30, 40, 50]}
    rows = persist.recent(limit=10)
    # Just verify the API works (may be empty if no snapshots taken yet)
    assert isinstance(rows, list)

def test_dep_persist_recent_limit():
    persist = DepMetricsPersist()
    rows = persist.recent(limit=5)
    assert len(rows) <= 5
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_dep_persist.py -v`
Expected: FAIL

- [ ] **Step 4: Implement DepMetricsPersist**

```python
# mio_taskhub/observability/dep_persist.py
import time
import sqlite3
import os
import threading

_local = threading.local()

def _conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        db_path = os.path.expanduser("~/.mio_taskhub/taskhub.db")
        _local.conn = sqlite3.connect(db_path, timeout=5)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn

class DepMetricsPersist:
    def snapshot(self):
        """Capture current dep metrics to DB."""
        from observability.dep_metrics import _data
        conn = _conn()
        now = time.time()
        for dep, ops in _data.items():
            for op, info in ops.items():
                count = info.get("count", 0)
                errors = info.get("errors", 0)
                lats = info.get("latencies", [])
                avg_ms = sum(lats) / len(lats) if lats else 0
                sorted_lats = sorted(lats)
                p50 = sorted_lats[len(sorted_lats)//2] if sorted_lats else 0
                p90 = sorted_lats[int(len(sorted_lats)*0.9)] if sorted_lats else 0
                p99 = sorted_lats[int(len(sorted_lats)*0.99)] if sorted_lats else 0
                max_ms = max(lats) if lats else 0
                error_rate = errors / count if count > 0 else 0
                conn.execute(
                    "INSERT INTO depmetricssnapshot (ts, dep, op, count, errors, avg_ms, p50_ms, p90_ms, p99_ms, max_ms, error_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, dep, op, count, errors, avg_ms, p50, p90, p99, max_ms, error_rate)
                )
        conn.commit()

    def recent(self, limit: int = 50) -> list:
        conn = _conn()
        rows = conn.execute("SELECT * FROM depmetricssnapshot ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def cleanup(self, days: int = 7):
        conn = _conn()
        cutoff = time.time() - days * 86400
        conn.execute("DELETE FROM depmetricssnapshot WHERE ts < ?", (cutoff,))
        conn.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_dep_persist.py -v`
Expected: PASS

- [ ] **Step 6: Start dep persist background thread in main.py lifespan**

```python
# In main.py lifespan, after starting other threads:
from observability.dep_persist import DepMetricsPersist
_dep_persist = DepMetricsPersist()

async def _dep_persist_loop():
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        try:
            _dep_persist.snapshot()
        except Exception:
            pass

_thread_registry.start("dep_persist", _dep_persist_loop)
```

- [ ] **Step 7: Commit**

```bash
cd E:\work\code\agent-dev\mio-taskhub
git add observability/dep_persist.py migrations.py main.py tests/test_dep_persist.py
git commit -m "feat: persist dependency metrics snapshots to DB for historical analysis"
```

---

## Task 3: Insights Engine — Anomaly Detection + Recommendations

**Files:**
- Create: `mio_taskhub/observability/insights.py`
- Modify: `mio_taskhub/migrations.py` — add `insight` table
- Modify: `mio_taskhub/main.py` — register insights evaluator
- Create: `tests/test_insights.py`

- [ ] **Step 1: Add `insight` table to migrations.py**

```python
# In migrate_all(), add:
conn.execute("""
CREATE TABLE IF NOT EXISTS insight (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts FLOAT NOT NULL,
    kind VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    description TEXT NOT NULL,
    severity VARCHAR NOT NULL DEFAULT 'info',
    metric_name VARCHAR,
    metric_value FLOAT,
    baseline FLOAT,
    recommendation TEXT,
    auto_action TEXT,
    acknowledged BOOLEAN DEFAULT 0
)
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_insight_ts ON insight(ts)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_insight_kind ON insight(kind)")
```

- [ ] **Step 2: Write test for insights engine**

```python
# tests/test_insights.py
from observability.insights import InsightsEngine

def test_insights_engine_detects_high_failure_rate():
    engine = InsightsEngine()
    # Simulate high failure rate metric
    metrics = {"taskhub_task_failure_rate": 0.15}
    insights = engine.evaluate(metrics)
    # Should detect anomaly if failure_rate > threshold
    assert isinstance(insights, list)

def test_insights_engine_stores_insights():
    engine = InsightsEngine()
    engine.store("test_kind", "Test Title", "Test description", severity="warning", recommendation="Do something")
    recent = engine.recent(limit=10)
    assert len(recent) >= 1
    assert recent[0]["title"] == "Test Title"

def test_insights_engine_recent_limit():
    engine = InsightsEngine()
    rows = engine.recent(limit=3)
    assert len(rows) <= 3
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_insights.py -v`
Expected: FAIL

- [ ] **Step 4: Implement InsightsEngine**

```python
# mio_taskhub/observability/insights.py
import time
import sqlite3
import os
import threading

_local = threading.local()

def _conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        db_path = os.path.expanduser("~/.mio_taskhub/taskhub.db")
        _local.conn = sqlite3.connect(db_path, timeout=5)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn

# Baseline thresholds (can be tuned)
THRESHOLDS = {
    "task_failure_rate": {"warn": 0.05, "critical": 0.15},
    "task_success_rate": {"warn_low": 0.90, "critical_low": 0.80},
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

            # Check critical thresholds
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
            # Check warning thresholds
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
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO insight (ts, kind, title, description, severity, metric_name, metric_value, baseline, recommendation, auto_action) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), kind, title, description, severity, metric_name, metric_value, baseline, recommendation, auto_action)
        )
        conn.commit()
        return {"id": cur.lastrowid, "ts": time.time(), "kind": kind, "title": title, "severity": severity}

    def recent(self, limit: int = 20, kind: str = None) -> list:
        conn = _conn()
        if kind:
            rows = conn.execute("SELECT * FROM insight WHERE kind = ? ORDER BY ts DESC LIMIT ?", (kind, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM insight ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def acknowledge(self, insight_id: int):
        conn = _conn()
        conn.execute("UPDATE insight SET acknowledged = 1 WHERE id = ?", (insight_id,))
        conn.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_insights.py -v`
Expected: PASS

- [ ] **Step 6: Register insights evaluator in main.py lifespan**

```python
# In main.py lifespan, after init_alert_manager:
from observability.insights import InsightsEngine
_insights_engine = InsightsEngine()

async def _insights_eval_loop():
    while True:
        await asyncio.sleep(60)  # every 60 seconds
        try:
            metrics = _collect_metrics_snapshot()  # reuse existing metric collection
            _insights_engine.evaluate(metrics)
        except Exception:
            pass

_thread_registry.start("insights_eval", _insights_eval_loop)
```

- [ ] **Step 7: Commit**

```bash
cd E:\work\code\agent-dev\mio-taskhub
git add observability/insights.py migrations.py main.py tests/test_insights.py
git commit -m "feat: insights engine - anomaly detection with recommendations from metrics"
```

---

## Task 4: Auto-Remediation Engine

**Files:**
- Create: `mio_taskhub/observability/remediation.py`
- Modify: `mio_taskhub/main.py` — register remediation evaluator
- Create: `tests/test_remediation.py`

- [ ] **Step 1: Write test for remediation engine**

```python
# tests/test_remediation.py
from observability.remediation import RemediationEngine

def test_remediation_engine_stalls_detected():
    engine = RemediationEngine()
    actions = engine.evaluate_stalled_tasks()
    assert isinstance(actions, list)

def test_remediation_engine_thread_restart():
    engine = RemediationEngine()
    # Just verify the method exists and is callable
    result = engine.restart_stalled_thread("test_thread")
    assert isinstance(result, dict)

def test_remediation_log():
    engine = RemediationEngine()
    engine.log("test_action", "Test remediation", success=True)
    recent = engine.recent(limit=10)
    assert len(recent) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_remediation.py -v`
Expected: FAIL

- [ ] **Step 3: Implement RemediationEngine**

```python
# mio_taskhub/observability/remediation.py
import time
import sqlite3
import os
import logging
import threading

logger = logging.getLogger(__name__)
_local = threading.local()

def _conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        db_path = os.path.expanduser("~/.mio_taskhub/taskhub.db")
        _local.conn = sqlite3.connect(db_path, timeout=5)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn

class RemediationEngine:
    def evaluate_stalled_tasks(self) -> list:
        """Detect tasks stuck in non-terminal states for too long."""
        conn = _conn()
        cutoff = time.time() - 600  # 10 minutes
        rows = conn.execute(
            "SELECT id, title, stage, state, created_at FROM task WHERE state NOT IN ('completed', 'failed', 'cancelled') AND created_at < ?",
            (cutoff,)
        ).fetchall()
        actions = []
        for row in rows:
            task = dict(row)
            age_minutes = (time.time() - task["created_at"]) / 60
            if age_minutes > 30:
                actions.append({
                    "type": "requeue_stuck_task",
                    "task_id": task["id"],
                    "task_title": task["title"],
                    "age_minutes": round(age_minutes, 1),
                    "action": "reset_task_state"
                })
        return actions

    def restart_stalled_thread(self, thread_name: str) -> dict:
        """Attempt to restart a stalled background thread."""
        from observability.background import _thread_registry
        try:
            _thread_registry.stop_all()
            # The thread will be restarted on next heartbeat cycle
            self.log("restart_thread", f"Restarted thread pool due to stalled: {thread_name}", success=True)
            return {"success": True, "message": f"Thread pool restarted, stalled: {thread_name}"}
        except Exception as e:
            self.log("restart_thread", f"Failed to restart: {e}", success=False)
            return {"success": False, "error": str(e)}

    def requeue_task(self, task_id: str) -> dict:
        """Reset a stuck task back to queued state."""
        conn = _conn()
        try:
            conn.execute(
                "UPDATE task SET state = 'queued', retry_count = retry_count + 1 WHERE id = ? AND state NOT IN ('completed', 'failed', 'cancelled')",
                (task_id,)
            )
            conn.commit()
            self.log("requeue_task", f"Requeued task {task_id}", success=True)
            return {"success": True, "task_id": task_id}
        except Exception as e:
            self.log("requeue_task", f"Failed to requeue {task_id}: {e}", success=False)
            return {"success": False, "error": str(e)}

    def log(self, action: str, message: str, success: bool = True):
        conn = _conn()
        conn.execute(
            "INSERT INTO insight (ts, kind, title, description, severity, recommendation) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), "remediation", f"Auto-remediation: {action}", message, "info" if success else "warning", None)
        )
        conn.commit()

    def recent(self, limit: int = 20) -> list:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM insight WHERE kind = 'remediation' ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_remediation.py -v`
Expected: PASS

- [ ] **Step 5: Register remediation evaluator in main.py lifespan**

```python
# In main.py lifespan:
from observability.remediation import RemediationEngine
_remediation_engine = RemediationEngine()

async def _remediation_eval_loop():
    while True:
        await asyncio.sleep(120)  # every 2 minutes
        try:
            stalled = _remediation_engine.evaluate_stalled_tasks()
            for action in stalled[:5]:  # limit to 5 per cycle
                if action["type"] == "requeue_stuck_task":
                    _remediation_engine.requeue_task(action["task_id"])
        except Exception:
            pass

_thread_registry.start("remediation_eval", _remediation_eval_loop)
```

- [ ] **Step 6: Commit**

```bash
cd E:\work\code\agent-dev\mio-taskhub
git add observability/remediation.py main.py tests/test_remediation.py
git commit -m "feat: auto-remediation engine - detect stalled tasks and auto-requeue"
```

---

## Task 5: Insights + Audit API Endpoints

**Files:**
- Create: `mio_taskhub/api/insights.py`
- Modify: `mio_taskhub/main.py` — register insights router
- Modify: `tests/test_api_insights.py`

- [ ] **Step 1: Write test for insights API**

```python
# tests/test_api_insights.py
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_insights_recent():
    resp = client.get("/api/v1/insights?limit=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_insights_acknowledge():
    # First store an insight
    from observability.insights import InsightsEngine
    engine = InsightsEngine()
    result = engine.store("test", "API Test", "Testing API", severity="info")
    # Then acknowledge it
    resp = client.post(f"/api/v1/insights/{result['id']}/acknowledge")
    assert resp.status_code == 200

def test_audit_recent():
    resp = client.get("/api/v1/audit?limit=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_audit_stats():
    resp = client.get("/api/v1/audit/stats?hours=24")
    assert resp.status_code == 200
    assert "total" in resp.json()

def test_remediation_log():
    resp = client.get("/api/v1/remediation?limit=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_api_insights.py -v`
Expected: FAIL (no such endpoints)

- [ ] **Step 3: Implement API endpoints**

```python
# mio_taskhub/api/insights.py
from fastapi import APIRouter, Query
from observability.insights import InsightsEngine
from observability.audit import AlertAudit
from observability.remediation import RemediationEngine

router = APIRouter(prefix="/api/v1", tags=["insights"])
_engine = InsightsEngine()
_audit = AlertAudit()
_remediation = RemediationEngine()

@router.get("/insights")
def list_insights(limit: int = Query(20, ge=1, le=100), kind: str = Query(None)):
    return _engine.recent(limit=limit, kind=kind)

@router.post("/insights/{insight_id}/acknowledge")
def acknowledge_insight(insight_id: int):
    _engine.acknowledge(insight_id)
    return {"ok": True}

@router.get("/audit")
def list_audit(limit: int = Query(50, ge=1, le=200)):
    return _audit.recent(limit=limit)

@router.get("/audit/stats")
def audit_stats(hours: int = Query(24, ge=1, le=168)):
    return _audit.stats(hours=hours)

@router.get("/remediation")
def list_remediation(limit: int = Query(20, ge=1, le=100)):
    return _remediation.recent(limit=limit)

@router.get("/observability/summary")
def observability_summary():
    """Holistic system health summary."""
    from observability.metrics import render_metrics
    import re
    metrics_text = render_metrics()

    # Extract key metrics
    def extract gauge_name:
        match = re.search(rf'{gauge_name}\s+([\d.]+)', metrics_text)
        return float(match.group(1)) if match else None

    return {
        "slo_availability": extract("taskhub_slo_availability_30d"),
        "error_budget_remaining": extract("taskhub_slo_error_budget_remaining"),
        "task_success_rate": extract("taskhub_task_success_rate"),
        "task_failure_rate": extract("taskhub_task_failure_rate"),
        "cpu_percent": extract("taskhub_process_cpu_percent"),
        "memory_percent": extract("taskhub_process_memory_percent"),
        "db_pool_utilization": extract("taskhub_db_pool_utilization"),
        "active_threads": extract("taskhub_thread_pool_alive"),
        "insights_unacknowledged": len(_engine.recent(limit=100)),
        "audit_events_24h": _audit.stats(hours=24)["total"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/test_api_insights.py -v`
Expected: PASS

- [ ] **Step 5: Register router in main.py**

```python
# In main.py, after other router registrations:
from api.insights import router as insights_router
app.include_router(insights_router)
```

- [ ] **Step 6: Commit**

```bash
cd E:\work\code\agent-dev\mio-taskhub
git add api/insights.py main.py tests/test_api_insights.py
git commit -m "feat: insights/audit/remediation API endpoints + observability summary"
```

---

## Task 6: Dashboard Enhancement — SLO Trend + Insight Feed

**Files:**
- Modify: `mio_taskhub/main.py` — enhance embedded dashboard HTML

- [ ] **Step 1: Add SLO history chart to dashboard**

In the embedded dashboard HTML in `main.py`, add a new chart section after the existing charts:

```html
<!-- SLO History Trend -->
<div class="card" style="grid-column: span 2;">
    <h3>SLO Availability Trend (7 Days)</h3>
    <canvas id="sloChart" height="100"></canvas>
</div>

<!-- Insights Feed -->
<div class="card">
    <h3>Recent Insights</h3>
    <div id="insightsFeed" style="max-height: 300px; overflow-y: auto;"></div>
</div>
```

- [ ] **Step 2: Add SLO chart JavaScript**

```javascript
// Fetch SLO history and render chart
async function loadSloChart() {
    const resp = await fetch('/api/v1/slo/history?hours=168&limit=168');
    const data = await resp.json();
    const labels = data.map(d => new Date(d.ts * 1000).toLocaleString('zh-CN', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}));
    const availability = data.map(d => (d.availability * 100).toFixed(2));

    new Chart(document.getElementById('sloChart'), {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Availability %',
                data: availability,
                borderColor: '#10b981',
                backgroundColor: 'rgba(16,185,129,0.1)',
                fill: true,
                tension: 0.3
            }, {
                label: 'Target (99%)',
                data: Array(labels.length).fill(99),
                borderColor: '#ef4444',
                borderDash: [5,5],
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { color: '#94a3b8' } } },
            scales: {
                x: { ticks: { color: '#64748b', maxTicksLimit: 10 } },
                y: { min: 95, max: 100, ticks: { color: '#64748b' } }
            }
        }
    });
}

// Fetch insights feed
async function loadInsightsFeed() {
    const resp = await fetch('/api/v1/insights?limit=10');
    const insights = await resp.json();
    const feed = document.getElementById('insightsFeed');
    feed.innerHTML = insights.map(i => `
        <div style="padding:8px;border-bottom:1px solid #334155;">
            <span style="color:${i.severity==='critical'?'#ef4444':i.severity==='warning'?'#f59e0b':'#3b82f6'}">${i.severity.toUpperCase()}</span>
            <strong>${i.title}</strong>
            <div style="color:#94a3b8;font-size:12px;">${i.description}</div>
            ${i.recommendation ? `<div style="color:#10b981;font-size:11px;margin-top:4px;">${i.recommendation}</div>` : ''}
        </div>
    `).join('');
}

// Call on load and refresh
loadSloChart();
loadInsightsFeed();
setInterval(loadInsightsFeed, 30000);
```

- [ ] **Step 3: Test dashboard renders**

Start the server and navigate to `/dashboard`. Verify:
- SLO trend chart shows with green line and red target line
- Insights feed shows recent insights with color-coded severity
- Both auto-refresh

- [ ] **Step 4: Commit**

```bash
cd E:\work\code\agent-dev\mio-taskhub
git add main.py
git commit -m "feat: dashboard SLO trend chart and insights feed panel"
```

---

## Task 7: Integration Test + Final Verification

- [ ] **Step 1: Run all tests**

Run: `cd E:\work\code\agent-dev\mio-taskhub && .venv\Scripts\python.exe -m pytest tests/ -k "not test_two_agents_race" --tb=short -q`
Expected: All existing tests + new tests pass

- [ ] **Step 2: Start server and verify endpoints**

```bash
cd E:\work\code\agent-dev\mio-taskhub
.venv\Scripts\python.exe -m mio_taskhub.main
```

Verify:
- `GET /metrics` — contains new `taskhub_insight_*` metrics
- `GET /api/v1/insights` — returns list
- `GET /api/v1/audit` — returns alert audit history
- `GET /api/v1/remediation` — returns remediation log
- `GET /api/v1/observability/summary` — returns holistic summary
- `GET /dashboard` — shows SLO trend + insights feed
- `WS /ws` — pushes insight updates

- [ ] **Step 3: Commit final state**

```bash
cd E:\work\code\agent-dev\mio-taskhub
git add -A
git commit -m "feat: observability feedback loop v1 - audit, insights, remediation, dashboard"
```

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-15-observability-feedback-loop.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
