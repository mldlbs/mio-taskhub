# -*- coding: utf-8 -*-
"""窄门禁迁移幂等性验证（d468e633 FR-10）。

场景：生产库尚未迁移（没有新列）→ 启动 hub → 迁移应：
  1. 加上新列（task.diff_gate_exempt / run.diff_summary / run.annotations / run.rejections）
  2. 把**存量任务全部置 diff_gate_exempt=1**（保持迁移前行为不变）
  3. 重复执行不报错（幂等）
  4. 迁移**之后**新建的任务默认不受门禁约束（豁免值 = 0）

做法：复制生产库 → 删掉新列（模拟未迁移）→ 跑迁移 → 断言。
用生产库副本而非裸 SQL 造表：其他既有迁移需要 state 等列，
简化结构会撞出与本次改动无关的报错，掩盖真正要验的东西。

用法：.venv/Scripts/python.exe scripts/verify_diff_gate_migration.py
"""
import os
import shutil
import sqlite3
import sys

DB = os.environ.get("MIG_TEST_DB", "E:/hermes/cache/scratch/mig_test.db")
PROD = os.path.expanduser("~/.mio_taskhub/taskhub.db")

NEW_TASK_COL = "diff_gate_exempt"
NEW_RUN_COLS = ("diff_summary", "annotations", "rejections")


def _fresh():
    for suffix in ("", "-wal", "-shm"):
        p = DB + suffix
        if os.path.exists(p):
            os.remove(p)


def _cols(table):
    con = sqlite3.connect(DB)
    out = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    con.close()
    return out


def _task_exempt():
    con = sqlite3.connect(DB)
    rows = dict(con.execute("SELECT id, diff_gate_exempt FROM task"))
    con.close()
    return rows


def _drop_new_cols():
    """把新列删掉，模拟「迁移前的库」。"""
    con = sqlite3.connect(DB)
    dropped = []
    for stmt in ([f"ALTER TABLE task DROP COLUMN {NEW_TASK_COL}"]
                 + [f"ALTER TABLE run DROP COLUMN {c}" for c in NEW_RUN_COLS]):
        try:
            con.execute(stmt)
            dropped.append(stmt.split()[-1])
        except sqlite3.OperationalError:
            pass  # 列本来就不存在（生产库尚未迁移）→ 正是我们要的状态
    con.commit()
    con.close()
    return dropped


def main() -> int:
    if not os.path.exists(PROD):
        print(f"生产库不存在，跳过：{PROD}")
        return 0
    os.environ["MIO_TASKHUB_DB"] = DB
    _fresh()
    shutil.copy2(PROD, DB)

    dropped = _drop_new_cols()
    print("=== 迁移前 ===")
    print(f"  已删除的新列: {dropped or '（生产库本就未迁移）'}")
    tcols = _cols("task")
    rcols = _cols("run")
    assert NEW_TASK_COL not in tcols, "测试前提失败：新列应已删除"
    assert not any(c in rcols for c in NEW_RUN_COLS), "测试前提失败：run 新列应已删除"
    con = sqlite3.connect(DB)
    n_tasks = con.execute("SELECT COUNT(*) FROM task").fetchone()[0]
    con.close()
    print(f"  存量任务数: {n_tasks}")

    from mio_taskhub.db import engine
    from mio_taskhub.migrations import run_migrations

    print("\n=== 第一次迁移 ===")
    run_migrations(engine)
    tcols = _cols("task")
    rcols = _cols("run")
    assert NEW_TASK_COL in tcols, "task.diff_gate_exempt 未创建"
    for c in NEW_RUN_COLS:
        assert c in rcols, f"run.{c} 未创建"
    print("  新列已创建 ✅")
    vals = _task_exempt()
    not_exempt = {k: v for k, v in vals.items() if not v}
    print(f"  存量任务豁免值: {len(vals)} 条，其中未豁免 {len(not_exempt)} 条")
    assert not not_exempt, f"存量任务应全部被置豁免，仍有未豁免：{list(not_exempt)[:5]}"
    print("  存量任务全部豁免 ✅（保持迁移前行为不变）")

    print("\n=== 第二次迁移（幂等） ===")
    run_migrations(engine)
    vals2 = _task_exempt()
    assert vals2 == vals, "重复迁移不应改变数据"
    print("  数据不变、无报错 ✅")

    print("\n=== 迁移后新建任务默认不受门禁约束 ===")
    from sqlmodel import Session
    from mio_taskhub.models import Task as _Task
    with Session(engine) as s:
        s.add(_Task(id="post-migration-new", title="迁移后新建"))
        s.commit()
    new_val = _task_exempt().get("post-migration-new")
    assert new_val == 0, f"迁移后新建任务应默认豁免值=0，实际 {new_val}"
    print("  新建任务 diff_gate_exempt=0 ✅")

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
