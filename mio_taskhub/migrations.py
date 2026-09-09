"""Schema migrations for mio-taskhub. Runs on startup, idempotent."""
from sqlalchemy import inspect, text


def run_migrations(target_engine=None):
    """Apply all schema migrations. Idempotent — safe to run on every startup."""
    eng = target_engine
    if eng is None:
        from mio_taskhub.db import engine
        eng = engine

    with eng.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}

        if "task" in tables:
            _migrate_task(conn)
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


def _migrate_task(conn):
    """Task table migrations: stage columns, depends_on, indexes, retry, M1 timestamps."""
    cols = {c["name"] for c in inspect(conn).get_columns("task")}
    if "stage" not in cols:
        conn.execute(text("ALTER TABLE task ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'READY'"))
    if "spec_path" not in cols:
        conn.execute(text("ALTER TABLE task ADD COLUMN spec_path VARCHAR NOT NULL DEFAULT ''"))
    if "plan_path" not in cols:
        conn.execute(text("ALTER TABLE task ADD COLUMN plan_path VARCHAR NOT NULL DEFAULT ''"))
    if "review_result" not in cols:
        conn.execute(text("ALTER TABLE task ADD COLUMN review_result VARCHAR NOT NULL DEFAULT ''"))
    if "idea_id" not in cols:
        conn.execute(text("ALTER TABLE task ADD COLUMN idea_id VARCHAR NOT NULL DEFAULT ''"))
    if "task_kind" not in cols:
        conn.execute(text("ALTER TABLE task ADD COLUMN task_kind VARCHAR NOT NULL DEFAULT 'NORMAL'"))
    if "fallback_after" not in cols:
        conn.execute(text("ALTER TABLE task ADD COLUMN fallback_after INTEGER"))
    if "depends_on" in cols:
        from mio_taskhub.dependency import normalize_depends
        import json as _json
        dep_rows = conn.execute(text("SELECT id, depends_on FROM task")).fetchall()
        for _id, _val in dep_rows:
            norm = normalize_depends(_val)
            if _val != _json.dumps(norm, separators=(",", ":")):
                conn.execute(text("UPDATE task SET depends_on=:dp WHERE id=:tid"),
                             {"dp": _json.dumps(norm, separators=(",", ":")), "tid": _id})
    conn.execute(text(
        "UPDATE task SET stage = 'BRAINSTORMING' WHERE stage = 'brainstorming'"
    ))
    conn.execute(text("UPDATE task SET stage = 'DESIGN' WHERE stage = 'design'"))
    conn.execute(text("UPDATE task SET stage = 'PLANNING' WHERE stage = 'planning'"))
    conn.execute(text("UPDATE task SET stage = 'READY' WHERE stage = 'ready'"))
    conn.execute(text("UPDATE task SET stage = 'IMPLEMENTING' WHERE stage = 'implementing'"))
    conn.execute(text("UPDATE task SET stage = 'REVIEW' WHERE stage = 'review'"))
    conn.execute(text("UPDATE task SET stage = 'DONE' WHERE stage = 'done'"))
    conn.execute(text("UPDATE task SET stage = 'CANCELLED' WHERE stage = 'cancelled'"))
    _idx = conn.execute(text("PRAGMA index_list('task')")).fetchall()
    if not any(r[1] == 'uq_task_active_change_tracking' for r in _idx):
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_active_change_tracking "
            "ON task (idea_id) WHERE task_kind = 'CHANGE_TRACKING' "
            "AND state NOT IN ('COMPLETED', 'CANCELLED')"
        ))
    tcols_retry = {c["name"] for c in inspect(conn).get_columns("task")}
    if "retry_at" not in tcols_retry:
        conn.execute(text("ALTER TABLE task ADD COLUMN retry_at DATETIME"))
    if "retry_count" not in tcols_retry:
        conn.execute(text("ALTER TABLE task ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"))
    tcols_m1 = {c["name"] for c in inspect(conn).get_columns("task")}
    m1_additions = [
        ("claimed_at",          "DATETIME"),
        ("running_started_at",  "DATETIME"),
        ("review_started_at",   "DATETIME"),
        ("completed_at",        "DATETIME"),
        ("failed_at",           "DATETIME"),
        ("cancelled_at",        "DATETIME"),
        ("last_transition_at",  "DATETIME"),
        ("block_reason",        "VARCHAR NOT NULL DEFAULT ''"),
        ("bounce_count",        "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col, typedef in m1_additions:
        if col not in tcols_m1:
            conn.execute(text(f"ALTER TABLE task ADD COLUMN {col} {typedef}"))
    if "created_at" in tcols_m1:
        conn.execute(text(
            "UPDATE task SET last_transition_at = created_at "
            "WHERE last_transition_at IS NULL"
        ))


def _migrate_idea(conn):
    """Idea table migrations: version, review fields, ADR extension fields."""
    icols = {c["name"] for c in inspect(conn).get_columns("idea")}
    if "version" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))
    if "last_reviewed_at" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN last_reviewed_at DATETIME"))
    if "review_count" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0"))
    if "idea_type" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN idea_type VARCHAR NOT NULL DEFAULT 'IDEA'"))
    if "adr_number" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN adr_number INTEGER"))
    if "adr_status" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN adr_status VARCHAR"))
    if "superseded_by" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN superseded_by VARCHAR"))
    if "madr_context" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN madr_context TEXT"))
    if "madr_decision" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN madr_decision TEXT"))
    if "madr_consequences" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN madr_consequences TEXT"))
    if "madr_alternatives" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN madr_alternatives TEXT"))
    if "adr_file_path" not in icols:
        conn.execute(text("ALTER TABLE idea ADD COLUMN adr_file_path VARCHAR"))


def _migrate_ideahistory(conn):
    """IdeaHistory table migrations: actor/content columns."""
    icols = {c["name"] for c in inspect(conn).get_columns("ideahistory")}
    if "actor" not in icols:
        conn.execute(text("ALTER TABLE ideahistory ADD COLUMN actor VARCHAR NOT NULL DEFAULT ''"))
    if "content" not in icols:
        conn.execute(text("ALTER TABLE ideahistory ADD COLUMN content VARCHAR NOT NULL DEFAULT ''"))


def _migrate_discussion(conn):
    """Discussion table migrations: stage/idea_id columns."""
    dcols = {c["name"] for c in inspect(conn).get_columns("discussion")}
    if "stage" not in dcols:
        conn.execute(text("ALTER TABLE discussion ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'brainstorming'"))
    if "idea_id" not in dcols:
        conn.execute(text("ALTER TABLE discussion ADD COLUMN idea_id VARCHAR NOT NULL DEFAULT ''"))


def _migrate_event(conn):
    """Event table migrations: entity/entity_id columns + backfill."""
    ecols = {c["name"] for c in inspect(conn).get_columns("event")}
    if "entity" not in ecols:
        conn.execute(text("ALTER TABLE event ADD COLUMN entity VARCHAR NOT NULL DEFAULT ''"))
    if "entity_id" not in ecols:
        conn.execute(text("ALTER TABLE event ADD COLUMN entity_id VARCHAR NOT NULL DEFAULT ''"))
    conn.execute(text(
        "UPDATE event SET entity='run', entity_id=run_id WHERE entity='' AND run_id IS NOT NULL AND run_id != ''"
    ))


def _migrate_ideachange(conn):
    """IdeaChange table migrations: change_type column."""
    iccols = {c["name"] for c in inspect(conn).get_columns("ideachange")}
    if "change_type" not in iccols:
        conn.execute(text("ALTER TABLE ideachange ADD COLUMN change_type VARCHAR NOT NULL DEFAULT 'FIELD_CHANGE'"))
