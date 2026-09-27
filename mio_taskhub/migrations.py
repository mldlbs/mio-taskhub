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
            _migrate_idea_assumption_link(conn)
        if "ideahistory" in tables:
            _migrate_ideahistory(conn)
        if "discussion" in tables:
            _migrate_discussion(conn)
        if "event" in tables:
            _migrate_event(conn)
        if "ideachange" in tables:
            _migrate_ideachange(conn)
        if "ideauserpref" in tables:
            _migrate_idea_user_pref(conn)
        if "readevidence" in tables:
            _migrate_read_evidence(conn)
        if "ratchetbaseline" in tables:
            _migrate_ratchet_baseline(conn)

        _migrate_observability(conn)

        conn.commit()


def _migrate_ratchet_baseline(conn):
    """棘轮基线：一个 (task_id, kind, metric) 只保留一行。"""
    try:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ratchet_task_kind_metric "
            "ON ratchetbaseline (task_id, kind, metric)"
        ))
    except Exception:
        # 历史重复行等异常不阻塞启动；写入端已按 upsert 保证唯一。
        pass


def _migrate_read_evidence(conn):
    """Read Evidence：一个 (run_id, document_kind) 只保留一行（upsert 幂等兜底）。"""
    try:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_read_evidence_run_kind "
            "ON readevidence (run_id, document_kind)"
        ))
    except Exception:
        # 历史重复行等异常不阻塞启动；写入端已按 upsert 保证唯一。
        pass


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
    if "doc_paths" not in cols:
        # kind -> path 映射（JSON）。历史行回填 '{}'，避免 SQLAlchemy JSON 读出 NULL。
        conn.execute(text("ALTER TABLE task ADD COLUMN doc_paths JSON"))
        conn.execute(text("UPDATE task SET doc_paths = '{}' WHERE doc_paths IS NULL"))
        # 已有 spec_path/plan_path 的历史任务迁移进 doc_paths（Python 组装 JSON，
        # 避免路径含引号/反斜杠时拼接出非法 JSON）；二者此后保持同步。
        import json as _json_dp
        doc_rows = conn.execute(text(
            "SELECT id, spec_path, plan_path FROM task "
            "WHERE (spec_path IS NOT NULL AND spec_path != '') "
            "   OR (plan_path IS NOT NULL AND plan_path != '')"
        )).fetchall()
        for _id, _spec, _plan in doc_rows:
            migrated = {}
            if _spec and _spec.strip():
                migrated["spec"] = _spec.strip()
            if _plan and _plan.strip():
                migrated["plan"] = _plan.strip()
            if migrated:
                conn.execute(
                    text("UPDATE task SET doc_paths=:dp WHERE id=:tid"),
                    {"dp": _json_dp.dumps(migrated, ensure_ascii=False), "tid": _id},
                )
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
    if "doc_statuses" not in tcols_m1:
        # kind -> {state, at, note}（文档生命周期状态机，JSON）。历史行回填 '{}'。
        conn.execute(text("ALTER TABLE task ADD COLUMN doc_statuses JSON"))
        conn.execute(text("UPDATE task SET doc_statuses = '{}' WHERE doc_statuses IS NULL"))


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
    # 想法落地闭环 P0 结构化字段（FR-1）：可空列，旧行保持 NULL
    for col, ddl in (
        ("goal", "TEXT"),
        ("success_metric", "TEXT"),
        ("constraints", "TEXT"),
        ("out_of_scope", "TEXT"),
        ("assumptions", "JSON"),
        ("risks", "JSON"),
        ("mvp_scope", "TEXT"),
        ("tags", "JSON"),
        # 想法落地闭环 P1 包 B（FR-11）：Mio 假设 id 引用列表，可空旧行 NULL
        ("hypotheses", "JSON"),
    ):
        if col not in icols:
            conn.execute(text(f"ALTER TABLE idea ADD COLUMN {col} {ddl}"))


def _migrate_idea_assumption_link(conn):
    """P3 FR-28：假设关联表建表 + 幂等回填。

    行来源 = Idea.hypotheses[]（关联本身）；status/note/confirmed_by 从
    Idea.assumptions JSON 按 hid 匹配回填（容忍 {hid: {...}} 与含 id/hid 对象两种形态）。
    已有 (idea_id, hypothesis_id) 行跳过 —— 可重复启动。
    """
    import json
    import uuid
    from datetime import datetime, timezone

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS ideaassumptionlink ("
        "id VARCHAR PRIMARY KEY, "
        "idea_id VARCHAR NOT NULL, "
        "hypothesis_id VARCHAR NOT NULL, "
        "status VARCHAR NOT NULL DEFAULT 'unverified', "
        "note VARCHAR NOT NULL DEFAULT '', "
        "confirmed_by VARCHAR NOT NULL DEFAULT '', "
        "updated_at DATETIME)"
    ))
    try:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_idea_assumption_link "
            "ON ideaassumptionlink (idea_id, hypothesis_id)"
        ))
    except Exception:
        pass  # 历史重复行不阻塞启动；写入端按 upsert 保证唯一

    rows = conn.execute(text(
        "SELECT id, hypotheses, assumptions FROM idea"
    )).fetchall()
    existing = {}
    for r in conn.execute(text(
        "SELECT idea_id, hypothesis_id FROM ideaassumptionlink"
    )).fetchall():
        existing.setdefault(r[0], set()).add(r[1])

    now_iso = datetime.now(timezone.utc).isoformat()
    for idea_id, hyp_raw, asm_raw in rows:
        try:
            hyps = json.loads(hyp_raw) if hyp_raw else []
        except Exception:
            hyps = []
        if not isinstance(hyps, list):
            continue
        try:
            asms = json.loads(asm_raw) if asm_raw else []
        except Exception:
            asms = []
        # 假设缓存两种形态 → {hid: entry}
        asm_by_hid: dict = {}
        if isinstance(asms, dict):
            asm_by_hid = {k: (v if isinstance(v, dict) else {}) for k, v in asms.items()}
        elif isinstance(asms, list):
            for e in asms:
                if isinstance(e, dict):
                    key = e.get("hid") or e.get("id")
                    if key:
                        asm_by_hid[str(key)] = e

        have = existing.get(idea_id, set())
        for hid in hyps:
            if not isinstance(hid, str) or not hid or hid in have:
                continue
            entry = asm_by_hid.get(hid, {})
            conn.execute(text(
                "INSERT INTO ideaassumptionlink "
                "(id, idea_id, hypothesis_id, status, note, confirmed_by, updated_at) "
                "VALUES (:id, :idea_id, :hid, :status, :note, :cb, :at)"
            ), {
                "id": str(uuid.uuid4())[:8],
                "idea_id": idea_id,
                "hid": hid,
                "status": str(entry.get("status") or "unverified"),
                "note": str(entry.get("note") or ""),
                "cb": str(entry.get("confirmed_by") or ""),
                "at": now_iso,
            })


def _migrate_ideahistory(conn):
    """IdeaHistory table migrations: actor/content columns."""
    icols = {c["name"] for c in inspect(conn).get_columns("ideahistory")}
    if "actor" not in icols:
        conn.execute(text("ALTER TABLE ideahistory ADD COLUMN actor VARCHAR NOT NULL DEFAULT ''"))
    if "content" not in icols:
        conn.execute(text("ALTER TABLE ideahistory ADD COLUMN content VARCHAR NOT NULL DEFAULT ''"))


def _migrate_discussion(conn):
    """Discussion table migrations: stage/idea_id + P2 双模式四列（FR-18）。"""
    dcols = {c["name"] for c in inspect(conn).get_columns("discussion")}
    if "stage" not in dcols:
        conn.execute(text("ALTER TABLE discussion ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'brainstorming'"))
    if "idea_id" not in dcols:
        conn.execute(text("ALTER TABLE discussion ADD COLUMN idea_id VARCHAR NOT NULL DEFAULT ''"))
    # 想法落地闭环 P2 包 C（FR-18）：mode 默认 free 兼容旧行，三个 JSON 列可空
    if "mode" not in dcols:
        conn.execute(text("ALTER TABLE discussion ADD COLUMN mode VARCHAR NOT NULL DEFAULT 'free'"))
    if "roles" not in dcols:
        conn.execute(text("ALTER TABLE discussion ADD COLUMN roles JSON"))
    if "review" not in dcols:
        conn.execute(text("ALTER TABLE discussion ADD COLUMN review JSON"))
    if "prompt_snapshot" not in dcols:
        conn.execute(text("ALTER TABLE discussion ADD COLUMN prompt_snapshot JSON"))


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


def _migrate_idea_user_pref(conn):
    """IdeaUserPref：一个 (idea_id, user, rule_id) 只保留一行（dismiss upsert 幂等）。"""
    try:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_idea_user_pref "
            "ON ideauserpref (idea_id, user, rule_id)"
        ))
    except Exception:
        # 历史重复行不阻塞启动；写入端 upsert 保证唯一
        pass


def _migrate_observability(conn):
    """Create observability tables: alertrule, slosnapshot, insight."""
    tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    if "insight" not in tables:
        conn.execute(text("""
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
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_insight_ts ON insight(ts)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_insight_kind ON insight(kind)"))
    if "alertrule" not in tables:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alertrule (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                metric VARCHAR NOT NULL,
                condition VARCHAR NOT NULL,
                threshold FLOAT NOT NULL,
                severity VARCHAR NOT NULL DEFAULT 'warning',
                enabled BOOLEAN NOT NULL DEFAULT 1,
                cooldown_seconds INTEGER NOT NULL DEFAULT 300,
                last_fired_at FLOAT,
                notify_channels TEXT,
                notify_config TEXT,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
        """))
    if "slosnapshot" not in tables:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS slosnapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts FLOAT NOT NULL,
                availability FLOAT,
                error_budget_remaining FLOAT,
                latency_avg_ms FLOAT,
                success_rate FLOAT,
                failure_rate FLOAT,
                throughput_24h INTEGER,
                task_total INTEGER,
                cpu_percent FLOAT,
                memory_percent FLOAT,
                db_pool_utilization FLOAT,
                extra TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_slosnapshot_ts ON slosnapshot(ts)"))
    # 兼容已有库：补 model 后续新增的列（否则 INSERT 报 no column 被静默吞掉 → 快照恒为 0 行）
    if "slosnapshot" in tables:
        _cols = {r[1] for r in conn.execute(text("PRAGMA table_info(slosnapshot)")).fetchall()}
        if "task_total" not in _cols:
            conn.execute(text("ALTER TABLE slosnapshot ADD COLUMN task_total INTEGER"))
    if "alertaudit" not in tables:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alertaudit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts FLOAT NOT NULL,
                alert_name VARCHAR NOT NULL,
                severity VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                message TEXT,
                metric_value FLOAT,
                threshold FLOAT,
                duration_seconds FLOAT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_alertaudit_ts ON alertaudit(ts)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_alertaudit_name ON alertaudit(alert_name)"))
    if "depmetricssnapshot" not in tables:
        conn.execute(text("""
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
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_depmetricssnapshot_ts ON depmetricssnapshot(ts)"))
