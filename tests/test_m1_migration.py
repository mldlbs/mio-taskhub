"""M1 Step 2 测试：Schema 迁移 + 脏数据修复。

覆盖：
 - Task 表新增 8 字段（claimed_at 等 + block_reason + bounce_count）存在
 - TaskEvent 表创建 + 字段映射（event_metadata → metadata 列）
 - init_db 后 last_transition_at 回填为 created_at
 - migrate_v1.fix_state_stage 各规则
 - 幂等：跑两次无副作用
"""
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import inspect, text
from sqlmodel import Session

from mio_taskhub.db import engine, init_db
from mio_taskhub.models import Task, TaskEvent, TaskState, TaskStage, Run, RunState, SQLModel
from mio_taskhub.migrate_v1 import fix_state_stage, _legal_combo_strings


# ---------- Schema 迁移 ----------
class TestM1Schema:
    def test_task_new_fields_exist(self):
        init_db()
        with engine.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("task")}
        for col in [
            "claimed_at", "running_started_at", "review_started_at",
            "completed_at", "failed_at", "cancelled_at",
            "last_transition_at", "block_reason", "bounce_count",
        ]:
            assert col in cols, f"task.{col} 应已迁移添加"

    def test_task_event_table_created(self):
        init_db()
        with engine.connect() as conn:
            tables = {r[0] for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "taskevent" in tables

    def test_task_event_columns(self):
        init_db()
        with engine.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("taskevent")}
        for col in ["id", "task_id", "event_type", "from_state", "from_stage",
                    "to_state", "to_stage", "actor_type", "actor_id",
                    "reason", "metadata", "created_at"]:
            assert col in cols, f"taskevent.{col} 缺失"

    def test_event_metadata_python_attr(self):
        # Python 属性 event_metadata → DB 列 metadata
        e = TaskEvent(task_id="t1", event_type="created", actor_type="user", actor_id="u1")
        assert hasattr(e, "event_metadata")
        # sa_column 映射
        col = TaskEvent.__table__.c.metadata
        assert col is not None

    def test_migration_idempotent(self):
        init_db()
        init_db()  # 第二次不应抛错
        with engine.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("task")}
        assert "bounce_count" in cols

    def test_last_transition_at_backfilled(self):
        # 创建一个 task 走 init_db（conftest 已自动调），应被回填
        with Session(engine) as s:
            t = Task(title="backfill test", state=TaskState.QUEUED, stage=TaskStage.READY)
            s.add(t); s.commit(); s.refresh(t)
        init_db()  # 再跑一次回填
        with Session(engine) as s:
            t2 = s.get(Task, t.id)
            assert t2.last_transition_at is not None
            # 应等于 created_at（兜底）
            assert t2.last_transition_at == t2.created_at


# ---------- migrate_v1 脏数据修复 ----------
def _mk_task(state_val, stage_val, title="t"):
    """走 ORM 创建（拿到所有 NOT NULL 默认），再 UPDATE state/stage 制造脏数据。"""
    with Session(engine) as s:
        t = Task(id=title, title=title, state=TaskState.QUEUED, stage=TaskStage.READY)
        s.add(t); s.commit()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE task SET state=:s, stage=:st WHERE id=:id"),
            {"s": state_val, "st": stage_val, "id": title},
        )


def _read_task(tid):
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT state, stage, block_reason FROM task WHERE id=:id"
        ), {"id": tid}).first()
    return row


class TestFixRules:
    def test_rule1_running_wrong_stage_to_claimed(self):
        _mk_task("running", "DESIGN", "r1")
        with engine.connect() as conn:
            fix_state_stage(conn, dry_run=False)
        assert _read_task("r1")[0] == "claimed"
        assert _read_task("r1")[1] == "DESIGN"

    def test_rule2_retrying_wrong_stage_to_claimed(self):
        _mk_task("retrying", "PLANNING", "r2")
        with engine.connect() as conn:
            fix_state_stage(conn)
        assert _read_task("r2")[0] == "claimed"
        assert _read_task("r2")[1] == "PLANNING"

    def test_rule3_completed_impl_to_review(self):
        _mk_task("completed", "IMPLEMENTING", "r3")
        with engine.connect() as conn:
            fix_state_stage(conn)
        row = _read_task("r3")
        assert row[0] == "completed" and row[1] == "REVIEW"

    def test_rule4_completed_review_to_done(self):
        _mk_task("completed", "REVIEW", "r4")
        with engine.connect() as conn:
            fix_state_stage(conn)
        row = _read_task("r4")
        assert row[0] == "completed" and row[1] == "DONE"

    def test_rule5_completed_in_pre_impl_needs_review(self):
        _mk_task("completed", "DESIGN", "r5")
        with engine.connect() as conn:
            summary = fix_state_stage(conn)
        assert any(t[0] == "r5" for t in summary["needs_review_list"])
        # 数据未改
        assert _read_task("r5")[0] == "completed"
        assert _read_task("r5")[1] == "DESIGN"

    def test_rule6_queued_impl_no_run_to_ready(self):
        _mk_task("queued", "IMPLEMENTING", "r6")
        with engine.connect() as conn:
            fix_state_stage(conn)
        row = _read_task("r6")
        assert row[0] == "queued" and row[1] == "READY"
        assert "回退" in row[2]

    def test_rule6_queued_impl_with_active_run_to_claimed(self):
        _mk_task("queued", "IMPLEMENTING", "r6b")
        # 插入一个 active run（走 ORM 拿所有 NOT NULL 默认）
        with Session(engine) as s:
            s.add(Run(id="run1", task_id="r6b", agent_name="a1",
                      state=RunState.RUNNING, attempt=1))
            s.commit()
        with engine.connect() as conn:
            fix_state_stage(conn)
        row = _read_task("r6b")
        assert row[0] == "claimed" and row[1] == "IMPLEMENTING"

    def test_rule7_queued_done_needs_review(self):
        _mk_task("queued", "DONE", "r7")
        with engine.connect() as conn:
            summary = fix_state_stage(conn)
        assert any(t[0] == "r7" for t in summary["needs_review_list"])

    def test_rule8_blocked_failed_to_queued(self):
        _mk_task("blocked_failed", "IMPLEMENTING", "r8")
        with engine.connect() as conn:
            fix_state_stage(conn)
        row = _read_task("r8")
        assert row[0] == "queued"
        assert "依赖" in row[2]

    def test_rule9_stage_cancelled_to_state_cancelled(self):
        _mk_task("claimed", "CANCELLED", "r9")
        with engine.connect() as conn:
            fix_state_stage(conn)
        row = _read_task("r9")
        assert row[0] == "cancelled"
        assert row[1] == "BRAINSTORMING"
        assert "归一" in row[2]

    def test_legal_combo_unchanged(self):
        _mk_task("claimed", "BRAINSTORMING", "r10")
        with engine.connect() as conn:
            fix_state_stage(conn)
        # 已合法，不动
        row = _read_task("r10")
        assert row[0] == "claimed" and row[1] == "BRAINSTORMING"


class TestFixIdempotency:
    def test_running_twice_no_change(self):
        _mk_task("running", "DESIGN", "i1")
        with engine.connect() as conn:
            s1 = fix_state_stage(conn)
        row1 = _read_task("i1")
        with engine.connect() as conn:
            s2 = fix_state_stage(conn)
        row2 = _read_task("i1")
        assert row1 == row2
        # 第二次应无新 fix
        assert s2["fixed"] == 0


class TestLegalComboStrings:
    def test_returns_lowercase_pairs(self):
        s = _legal_combo_strings()
        assert ("completed", "done") in s
        assert ("running", "implementing") in s
        assert ("cancelled", "ready") in s
        # 全部小写
        for st, stg in s:
            assert st == st.lower() and stg == stg.lower()
