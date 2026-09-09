# P5: DB Layer Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the DB layer by extracting migration code, adding graceful shutdown, and improving connection lifecycle management.

**Architecture:** Three focused improvements: (1) extract 140-line migration into its own module, (2) add engine.dispose() on shutdown, (3) add connection health check with auto-recovery.

**Tech Stack:** SQLAlchemy engine, FastAPI lifespan, pytest

---

## Context: Current DB State

- **StaticPool** with `check_same_thread=False` — correct for SQLite, no change needed
- **WAL mode** + busy_timeout + synchronous=NORMAL — already optimal
- **Migration code** (`_migrate_stage_column`): 140 lines in db.py, mixes concerns
- **No engine.dispose()** on shutdown — connections may not close cleanly
- **No connection health monitoring** beyond readyz probe

---

## Task 1: Extract migration code to `migrations.py`

**Files:**
- Create: `mio_taskhub/migrations.py`
- Modify: `mio_taskhub/db.py` (remove migration code, import from migrations)

- [ ] **Step 1: Create `migrations.py`**

Move the entire `_migrate_stage_column` function from db.py to a new `mio_taskhub/migrations.py` file. Rename to `run_migrations(target_engine=None)`.

```python
# mio_taskhub/migrations.py
"""Schema migrations for mio-taskhub. Runs on startup, idempotent."""
from sqlalchemy import inspect, text


def run_migrations(target_engine=None):
    """Apply all schema migrations. Idempotent — safe to run on every startup."""
    from sqlmodel import Session
    eng = target_engine
    if eng is None:
        from mio_taskhub.db import engine
        eng = engine

    with eng.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table"))}

        if "task" in tables:
            _migrate_task(conn, tables)
        if "idea" in tables:
            _migrate_idea(conn)
        if "ideahistory" in tables:
            _migrate_ideahistory(conn)
        if "discussion" in tables:
            _migrate_discussion(conn)
        if "event" in tables:
            _migrate_event(conn)
        if "ideachange" in tables:
            _migrate_ideachange(conn)

        conn.commit()


def _migrate_task(conn, tables):
    cols = {c["name"] for c in inspect(conn).get_columns("task")}
    additions = [
        ("stage", "VARCHAR NOT NULL DEFAULT 'READY'"),
        ("spec_path", "VARCHAR NOT NULL DEFAULT ''"),
        ("plan_path", "VARCHAR NOT NULL DEFAULT ''"),
        ("review_result", "VARCHAR NOT NULL DEFAULT ''"),
        ("idea_id", "VARCHAR NOT NULL DEFAULT ''"),
        ("task_kind", "VARCHAR NOT NULL DEFAULT 'NORMAL'"),
        ("fallback_after", "INTEGER"),
        ("retry_at", "DATETIME"),
        ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("claimed_at", "DATETIME"),
        ("running_started_at", "DATETIME"),
        ("review_started_at", "DATETIME"),
        ("completed_at", "DATETIME"),
        ("failed_at", "DATETIME"),
        ("cancelled_at", "DATETIME"),
        ("last_transition_at", "DATETIME"),
        ("block_reason", "VARCHAR NOT NULL DEFAULT ''"),
        ("bounce_count", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col, typedef in additions:
        if col not in cols:
            conn.execute(text(f"ALTER TABLE task ADD COLUMN {col} {typedef}"))

    # depends_on normalization
    if "depends_on" in cols:
        from mio_taskhub.status import normalize_depends
        import json as _json
        dep_rows = conn.execute(text("SELECT id, depends_on FROM task")).fetchall()
        for _id, _val in dep_rows:
            norm = normalize_depends(_val)
            if _val != _json.dumps(norm, separators=(",", ":")):
                conn.execute(text("UPDATE task SET depends_on=:dp WHERE id=:tid"),
                             {"dp": _json.dumps(norm, separators=(",", ":")), "tid": _id})

    # Legacy lowercase stage fix
    for stage in ("brainstorming", "design", "planning", "ready", "implementing", "review", "done", "cancelled"):
        conn.execute(text(f"UPDATE task SET stage = '{stage.upper()}' WHERE stage = '{stage}'"))

    # Unique index for change tracking
    _idx = conn.execute(text("PRAGMA index_list('task')")).fetchall()
    if not any(r[1] == 'uq_task_active_change_tracking' for r in _idx):
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_active_change_tracking "
            "ON task (idea_id) WHERE task_kind = 'CHANGE_TRACKING' "
            "AND state NOT IN ('COMPLETED', 'CANCELLED')"
        ))

    # last_transition_at backfill
    if "created_at" in cols:
        conn.execute(text(
            "UPDATE task SET last_transition_at = created_at "
            "WHERE last_transition_at IS NULL"
        ))


def _migrate_idea(conn):
    cols = {c["name"] for c in inspect(conn).get_columns("idea")}
    for col, typedef in [
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("last_reviewed_at", "DATETIME"),
        ("review_count", "INTEGER NOT NULL DEFAULT 0"),
        ("idea_type", "VARCHAR NOT NULL DEFAULT 'IDEA'"),
        ("adr_number", "INTEGER"),
        ("adr_status", "VARCHAR"),
        ("superseded_by", "VARCHAR"),
        ("madr_context", "TEXT"),
        ("madr_decision", "TEXT"),
        ("madr_consequences", "TEXT"),
        ("madr_alternatives", "TEXT"),
        ("adr_file_path", "VARCHAR"),
    ]:
        if col not in cols:
            conn.execute(text(f"ALTER TABLE idea ADD COLUMN {col} {typedef}"))


def _migrate_ideahistory(conn):
    cols = {c["name"] for c in inspect(conn).get_columns("ideahistory")}
    for col, typedef in [("actor", "VARCHAR NOT NULL DEFAULT ''"), ("content", "VARCHAR NOT NULL DEFAULT ''")]:
        if col not in cols:
            conn.execute(text(f"ALTER TABLE ideahistory ADD COLUMN {col} {typedef}"))


def _migrate_discussion(conn):
    cols = {c["name"] for c in inspect(conn).get_columns("discussion")}
    for col, typedef in [("stage", "VARCHAR NOT NULL DEFAULT 'brainstorming'"), ("idea_id", "VARCHAR NOT NULL DEFAULT ''")]:
        if col not in cols:
            conn.execute(text(f"ALTER TABLE discussion ADD COLUMN {col} {typedef}"))


def _migrate_event(conn):
    cols = {c["name"] for c in inspect(conn).get_columns("event")}
    for col, typedef in [("entity", "VARCHAR NOT NULL DEFAULT ''"), ("entity_id", "VARCHAR NOT NULL DEFAULT ''")]:
        if col not in cols:
            conn.execute(text(f"ALTER TABLE event ADD COLUMN {col} {typedef}"))
    conn.execute(text(
        "UPDATE event SET entity='run', entity_id=run_id WHERE entity='' AND run_id IS NOT NULL AND run_id != ''"
    ))


def _migrate_ideachange(conn):
    cols = {c["name"] for c in inspect(conn).get_columns("ideachange")}
    if "change_type" not in cols:
        conn.execute(text("ALTER TABLE ideachange ADD COLUMN change_type VARCHAR NOT NULL DEFAULT 'FIELD_CHANGE'"))
```

- [ ] **Step 2: Update db.py**

Replace the `_migrate_stage_column` function body with an import:

```python
def _migrate_stage_column(target_engine=None):
    from mio_taskhub.migrations import run_migrations
    run_migrations(target_engine)
```

Remove all the migration code (lines 35-175) from db.py.

- [ ] **Step 3: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 4: Commit**

```bash
git add mio_taskhub/migrations.py mio_taskhub/db.py
git commit -m "refactor(p5): extract migration code to migrations.py"
```

---

## Task 2: Add graceful engine disposal on shutdown

**Files:**
- Modify: `mio_taskhub/main.py`

- [ ] **Step 1: Add engine.dispose() to lifespan shutdown**

In `main.py`, update the `lifespan` function to dispose the engine after stopping background jobs:

```python
@asynccontextmanager
async def lifespan(app):
    from mio_taskhub.wiring import start_background_jobs
    from mio_taskhub.git_sync import start_git_sync_worker, stop_git_sync_worker
    from mio_taskhub.night_runner import start_night_runner, stop_night_runner
    from mio_taskhub.cron_engine import start_cron_engine, stop_cron_engine
    app.state.background = start_background_jobs()
    start_git_sync_worker()
    start_night_runner()
    cron_engine = start_cron_engine()
    yield
    jobs = getattr(app.state, "background", None)
    if jobs:
        for job in jobs:
            job.stop()
    stop_git_sync_worker()
    stop_night_runner()
    stop_cron_engine()
    # Gracefully close DB connections
    from mio_taskhub.db import engine
    engine.dispose()
```

- [ ] **Step 2: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 3: Commit**

```bash
git add mio_taskhub/main.py
git commit -m "fix(p5): add engine.dispose() on shutdown for clean connection cleanup"
```

---

## Task 3: Add connection health check utility

**Files:**
- Modify: `mio_taskhub/db.py`

- [ ] **Step 1: Add check_connection utility to db.py**

```python
def check_connection() -> dict:
    """Verify DB connectivity. Returns {"ok": bool, "error": str|None}."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 2: Update readyz endpoint to use check_connection**

In `main.py`, update the `readyz` endpoint:

```python
@app.get("/readyz", tags=["health"], summary="Readiness probe")
def readyz():
    from mio_taskhub.db import check_connection
    result = check_connection()
    status = "ok" if result["ok"] else "degraded"
    return Response(
        content='{"status":"' + status + '","db":"' + ("ok" if result["ok"] else "error") + '"}',
        media_type="application/json",
        status_code=200 if result["ok"] else 503,
    )
```

- [ ] **Step 3: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 4: Commit**

```bash
git add mio_taskhub/db.py mio_taskhub/main.py
git commit -m "feat(p5): add check_connection utility, simplify readyz probe"
```

---

## Task 4: Final verification

- [ ] **Step 1: Verify db.py line count**

Run: `(Get-Content "mio_taskhub/db.py").Count`
Expected: ~50 lines (was 191)

- [ ] **Step 2: Verify migrations.py exists and works**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -c "from mio_taskhub.migrations import run_migrations; print('migrations OK')"`

- [ ] **Step 3: Run full test suite**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed
