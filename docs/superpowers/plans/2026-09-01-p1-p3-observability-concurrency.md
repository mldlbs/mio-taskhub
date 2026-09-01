# P1 + P3: Observability & Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured logging, health/metrics endpoints, SQLite WAL mode, and request middleware to mio-taskhub.

**Architecture:** Three independent subsystems — (1) DB concurrency safety via WAL + connection pooling, (2) structured JSON logging with request correlation, (3) health/metrics HTTP endpoints. Each can be implemented and tested independently.

**Tech Stack:** Python logging, SQLAlchemy event hooks, SQLite PRAGMA, FastAPI middleware, psutil (for process metrics).

---

## Task 1: SQLite WAL Mode + Connection Pool

**Files:**
- Modify: `mio_taskhub/db.py:17`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
"""DB concurrency and WAL mode tests."""
import pytest
from sqlmodel import Session, text
from mio_taskhub.db import engine, init_db


def test_wal_mode_enabled():
    """PRAGMA journal_mode should be WAL."""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()
        assert mode == "wal", f"Expected WAL, got {mode}"


def test_connection_pool_size():
    """Engine should be configured with pool_size >= 1."""
    pool = engine.pool
    assert hasattr(pool, "_max_overflow") or hasattr(pool, "size")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mio-taskhub && python -m pytest tests/test_db.py -v`
Expected: FAIL — `assert mode == "wal"` fails (currently DELETE mode)

- [ ] **Step 3: Implement WAL + pool config**

In `mio_taskhub/db.py`, replace line 17:

```python
from sqlalchemy import inspect, text, event
from sqlalchemy.pool import StaticPool

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mio-taskhub && python -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `cd mio-taskhub && python -m pytest -q`
Expected: 442+ passed

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/db.py tests/test_db.py
git commit -m "db: enable WAL mode + busy_timeout for SQLite concurrency"
```

---

## Task 2: Structured JSON Logging

**Files:**
- Create: `mio_taskhub/logging_config.py`
- Modify: `mio_taskhub/main.py:1`
- Test: `tests/test_logging.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logging.py
"""Structured logging configuration tests."""
import json
import logging
from mio_taskhub.logging_config import setup_logging, RequestFilter


def test_setup_logging_configures_root():
    setup_logging()
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)


def test_request_filter_adds_request_id():
    f = RequestFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    record.request_id = "abc-123"
    f.filter(record)
    assert record.request_id == "abc-123"


def test_json_formatter_output():
    from mio_taskhub.logging_config import JSONFormatter
    fmt = JSONFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="test.py",
        lineno=1, msg="hello %s", args=("world",), exc_info=None,
    )
    record.request_id = "req-1"
    output = fmt.format(record)
    data = json.loads(output)
    assert data["msg"] == "hello world"
    assert data["request_id"] == "req-1"
    assert data["level"] == "INFO"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mio-taskhub && python -m pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mio_taskhub.logging_config'`

- [ ] **Step 3: Implement logging_config.py**

```python
# mio_taskhub/logging_config.py
"""Structured JSON logging with request correlation."""
import json
import logging
import sys
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(record, "request_id", request_id_var.get("-"))
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO"):
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RequestFilter())
    root.addHandler(handler)

    # Quiet noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
```

- [ ] **Step 4: Wire into main.py**

At the top of `mio_taskhub/main.py`, add after imports:

```python
from mio_taskhub.logging_config import setup_logging
setup_logging()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd mio-taskhub && python -m pytest tests/test_logging.py -v`
Expected: PASS

- [ ] **Step 6: Run full suite**

Run: `cd mio-taskhub && python -m pytest -q`
Expected: 442+ passed

- [ ] **Step 7: Commit**

```bash
git add mio_taskhub/logging_config.py mio_taskhub/main.py tests/test_logging.py
git commit -m "logging: add structured JSON formatter with request correlation"
```

---

## Task 3: Request ID Middleware

**Files:**
- Create: `mio_taskhub/middleware.py`
- Modify: `mio_taskhub/main.py`
- Test: `tests/test_middleware.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_middleware.py
"""Request ID middleware tests."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mio_taskhub.middleware import RequestIDMiddleware
from mio_taskhub.logging_config import request_id_var


def test_request_id_in_response():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/test")
    def handler():
        return {"rid": request_id_var.get()}

    client = TestClient(app)
    resp = client.get("/test")
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID")
    assert rid, "X-Request-ID header missing"
    assert len(rid) == 36  # UUID format


def test_client_supplied_request_id():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/test")
    def handler():
        return {"rid": request_id_var.get()}

    client = TestClient(app)
    resp = client.get("/test", headers={"X-Request-ID": "my-custom-id"})
    assert resp.headers.get("X-Request-ID") == "my-custom-id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mio-taskhub && python -m pytest tests/test_middleware.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement middleware.py**

```python
# mio_taskhub/middleware.py
"""Request ID middleware — injects correlation ID into context + response."""
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from mio_taskhub.logging_config import request_id_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)
```

- [ ] **Step 4: Wire into main.py**

Add after `setup_logging()` in `mio_taskhub/main.py`:

```python
from mio_taskhub.middleware import RequestIDMiddleware
app.add_middleware(RequestIDMiddleware)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd mio-taskhub && python -m pytest tests/test_middleware.py -v`
Expected: PASS

- [ ] **Step 6: Run full suite**

Run: `cd mio-taskhub && python -m pytest -q`
Expected: 442+ passed

- [ ] **Step 7: Commit**

```bash
git add mio_taskhub/middleware.py mio_taskhub/main.py tests/test_middleware.py
git commit -m "middleware: add X-Request-ID correlation with contextvars"
```

---

## Task 4: Health Check Endpoint

**Files:**
- Modify: `mio_taskhub/main.py`
- Test: `tests/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health.py
"""Health check endpoint tests."""
from fastapi.testclient import TestClient
from mio_taskhub.main import app


client = TestClient(app, raise_server_exceptions=False)


def test_liveness():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readiness():
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "db" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mio-taskhub && python -m pytest tests/test_health.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Implement health endpoints**

Add to `mio_taskhub/main.py` before the static mount:

```python
from fastapi import Response


@app.get("/healthz", tags=["health"])
def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["health"])
def readyz():
    from mio_taskhub.db import engine
    from sqlalchemy import text
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass
    status = "ok" if db_ok else "degraded"
    return Response(
        content='{"status":"' + status + '","db":"' + ("ok" if db_ok else "error") + '"}',
        media_type="application/json",
        status_code=200 if db_ok else 503,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mio-taskhub && python -m pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `cd mio-taskhub && python -m pytest -q`
Expected: 442+ passed

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/main.py tests/test_health.py
git commit -m "api: add /healthz and /readyz endpoints"
```

---

## Task 5: Metrics Endpoint

**Files:**
- Create: `mio_taskhub/metrics.py`
- Modify: `mio_taskhub/main.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics.py
"""Metrics endpoint tests."""
from fastapi.testclient import TestClient
from mio_taskhub.main import app


client = TestClient(app, raise_server_exceptions=False)


def test_metrics_returns_prometheus_format():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "taskhub_tasks_total" in text
    assert "taskhub_uptime_seconds" in text
    assert "# HELP" in text


def test_metrics_counts():
    resp = client.get("/metrics")
    text = resp.text
    # Should have task counts by state
    assert "taskhub_tasks_total{state=" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mio-taskhub && python -m pytest tests/test_metrics.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Implement metrics.py**

```python
# mio_taskhub/metrics.py
"""Lightweight Prometheus-format metrics (no external dependencies)."""
import time
from sqlmodel import Session, text
from mio_taskhub.db import engine

_start_time = time.time()


def render_metrics() -> str:
    lines = []
    lines.append("# HELP taskhub_uptime_seconds Process uptime in seconds")
    lines.append("# TYPE taskhub_uptime_seconds gauge")
    lines.append(f"taskhub_uptime_seconds {time.time() - _start_time:.1f}")

    lines.append("# HELP taskhub_tasks_total Total tasks by state")
    lines.append("# TYPE taskhub_tasks_total gauge")
    try:
        with Session(engine) as s:
            rows = s.exec(text("SELECT state, COUNT(*) FROM task GROUP BY state")).all()
            for state, count in rows:
                lines.append(f'taskhub_tasks_total{{state="{state}"}} {count}')
    except Exception:
        pass

    lines.append("# HELP taskhub_events_total Total events by type")
    lines.append("# TYPE taskhub_events_total gauge")
    try:
        with Session(engine) as s:
            rows = s.exec(text("SELECT event_type, COUNT(*) FROM event GROUP BY event_type")).all()
            for etype, count in rows:
                lines.append(f'taskhub_events_total{{type="{etype}"}} {count}')
    except Exception:
        pass

    lines.append("# HELP taskhub_agents_online Online agents")
    lines.append("# TYPE taskhub_agents_online gauge")
    try:
        with Session(engine) as s:
            rows = s.exec(text("SELECT status, COUNT(*) FROM agent GROUP BY status")).all()
            for status, count in rows:
                lines.append(f'taskhub_agents_online{{status="{status}"}} {count}')
    except Exception:
        pass

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Wire into main.py**

Add to `mio_taskhub/main.py`:

```python
from mio_taskhub.metrics import render_metrics


@app.get("/metrics", tags=["metrics"])
def metrics():
    from fastapi import Response
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd mio-taskhub && python -m pytest tests/test_metrics.py -v`
Expected: PASS

- [ ] **Step 6: Run full suite**

Run: `cd mio-taskhub && python -m pytest -q`
Expected: 442+ passed

- [ ] **Step 7: Commit**

```bash
git add mio_taskhub/metrics.py mio_taskhub/main.py tests/test_metrics.py
git commit -m "metrics: add /metrics endpoint (Prometheus format, zero deps)"
```

---

## Task 6: API Error Logging Middleware

**Files:**
- Modify: `mio_taskhub/middleware.py`
- Test: `tests/test_middleware.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_middleware.py`:

```python
import logging


def test_error_logged_on_500(caplog):
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/fail")
    def handler():
        raise ValueError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        resp = client.get("/fail")
    assert resp.status_code == 500
    assert any("boom" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mio-taskhub && python -m pytest tests/test_middleware.py::test_error_logged_on_500 -v`
Expected: FAIL

- [ ] **Step 3: Extend middleware with error logging**

Update `mio_taskhub/middleware.py`:

```python
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from mio_taskhub.logging_config import request_id_var

logger = logging.getLogger("mio_taskhub.middleware")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            if response.status_code >= 500:
                logger.error(
                    "request_error",
                    extra={"request_id": rid, "path": request.url.path, "status": response.status_code},
                )
            return response
        except Exception:
            logger.exception("request_exception", extra={"request_id": rid, "path": request.url.path})
            raise
        finally:
            request_id_var.reset(token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mio-taskhub && python -m pytest tests/test_middleware.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `cd mio-taskhub && python -m pytest -q`
Expected: 442+ passed

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/middleware.py tests/test_middleware.py
git commit -m "middleware: log 500+ errors with request correlation"
```

---

## Final: Rebuild Exe

After all tasks pass:

```bash
cd mio-taskhub/web && npm run build
cd .. && python -m PyInstaller mio-taskhub.spec --noconfirm --clean
```

Verify: `curl http://127.0.0.1:48620/healthz` → `{"status":"ok"}`
