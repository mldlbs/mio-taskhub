# mio_taskhub/db.py
import os
from sqlmodel import SQLModel, create_engine, Session
from typing import Generator
from sqlalchemy import inspect, text

# Allow overriding the DB path (e.g. tests use a throwaway DB so the
# production data in ~/.mio_taskhub/taskhub.db is never wiped).
DB_PATH = os.environ.get("MIO_TASKHUB_DB")
if DB_PATH:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
else:
    DATA_DIR = os.path.expanduser("~/.mio_taskhub")
    DB_PATH = os.path.join(DATA_DIR, "taskhub.db")
    os.makedirs(DATA_DIR, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)

def _migrate_stage_column(target_engine=None):
    eng = target_engine or engine
    with eng.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "task" in tables:
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
                # depends_on 归一化（旧 VARCHAR 单值 → JSON 数组文本）
                from mio_taskhub.status import normalize_depends
                import json as _json
                dep_rows = conn.execute(text("SELECT id, depends_on FROM task")).fetchall()
                for _id, _val in dep_rows:
                    norm = normalize_depends(_val)
                    if _val != _json.dumps(norm, separators=(",", ":")):
                        conn.execute(text("UPDATE task SET depends_on=:dp WHERE id=:tid"),
                                     {"dp": _json.dumps(norm, separators=(",", ":")), "tid": _id})
            # SQLModel stores str-enums by .name (uppercase). Fix any lowercase
            # legacy values (e.g. from a pre-fix migration) to uppercase names.
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
            # 部分唯一索引：一个 idea 最多一条活跃（非终态）变更跟踪任务
            _idx = conn.execute(text("PRAGMA index_list('task')")).fetchall()
            if not any(r[1] == 'uq_task_active_change_tracking' for r in _idx):
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_active_change_tracking "
                    "ON task (idea_id) WHERE task_kind = 'CHANGE_TRACKING' "
                    "AND state NOT IN ('COMPLETED', 'CANCELLED')"
                ))
        # Idea table: add version column if missing (existing installs).
        if "idea" in tables:
            icols = {c["name"] for c in inspect(conn).get_columns("idea")}
            if "version" not in icols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))
            if "last_reviewed_at" not in icols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN last_reviewed_at DATETIME"))
            if "review_count" not in icols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0"))
        # IdeaHistory table: add actor/content columns if missing (existing installs).
        if "ideahistory" in tables:
            icols = {c["name"] for c in inspect(conn).get_columns("ideahistory")}
            if "actor" not in icols:
                conn.execute(text("ALTER TABLE ideahistory ADD COLUMN actor VARCHAR NOT NULL DEFAULT ''"))
            if "content" not in icols:
                conn.execute(text("ALTER TABLE ideahistory ADD COLUMN content VARCHAR NOT NULL DEFAULT ''"))
        # Discussion table: add stage/idea_id column if missing (existing installs).
        if "discussion" in tables:
            dcols = {c["name"] for c in inspect(conn).get_columns("discussion")}
            if "stage" not in dcols:
                conn.execute(text("ALTER TABLE discussion ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'brainstorming'"))
            if "idea_id" not in dcols:
                conn.execute(text("ALTER TABLE discussion ADD COLUMN idea_id VARCHAR NOT NULL DEFAULT ''"))
        # Task table: retry_at / retry_count for exponential backoff (existing installs).
        if "task" in tables:
            tcols_retry = {c["name"] for c in inspect(conn).get_columns("task")}
            if "retry_at" not in tcols_retry:
                conn.execute(text("ALTER TABLE task ADD COLUMN retry_at DATETIME"))
            if "retry_count" not in tcols_retry:
                conn.execute(text("ALTER TABLE task ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"))
        # Event table: backfill entity/entity_id from run_id (existing installs).
        if "event" in tables:
            ecols = {c["name"] for c in inspect(conn).get_columns("event")}
            if "entity" not in ecols:
                conn.execute(text("ALTER TABLE event ADD COLUMN entity VARCHAR NOT NULL DEFAULT ''"))
            if "entity_id" not in ecols:
                conn.execute(text("ALTER TABLE event ADD COLUMN entity_id VARCHAR NOT NULL DEFAULT ''"))
            conn.execute(text(
                "UPDATE event SET entity='run', entity_id=run_id WHERE entity='' AND run_id IS NOT NULL AND run_id != ''"
            ))
        # ADR 扩展字段迁移（existing installs）
        if "idea" in tables:
            acols = {c["name"] for c in inspect(conn).get_columns("idea")}
            if "idea_type" not in acols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN idea_type VARCHAR NOT NULL DEFAULT 'IDEA'"))
            if "adr_number" not in acols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN adr_number INTEGER"))
            if "adr_status" not in acols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN adr_status VARCHAR"))
            if "superseded_by" not in acols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN superseded_by VARCHAR"))
            if "madr_context" not in acols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN madr_context TEXT"))
            if "madr_decision" not in acols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN madr_decision TEXT"))
            if "madr_consequences" not in acols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN madr_consequences TEXT"))
            if "madr_alternatives" not in acols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN madr_alternatives TEXT"))
            if "adr_file_path" not in acols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN adr_file_path VARCHAR"))
        # IdeaChange 表添加 change_type 字段
        if "ideachange" in tables:
            iccols = {c["name"] for c in inspect(conn).get_columns("ideachange")}
            if "change_type" not in iccols:
                conn.execute(text("ALTER TABLE ideachange ADD COLUMN change_type VARCHAR NOT NULL DEFAULT 'FIELD_CHANGE'"))
        # OutboxEvent 表由 SQLModel.metadata.create_all 自动创建，无需迁移
        conn.commit()

def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_stage_column()

def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
