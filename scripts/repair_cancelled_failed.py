# -*- coding: utf-8 -*-
"""P1-1 修复：把「失败被清洗为取消」的历史脏数据还原为 failed。

背景（决策文档 `product-decision-20261020.md` §4 P1-1）：
  `scripts/archive_spinning_tasks.py` 直接写 `task.state = TaskState.CANCELLED`，
  **绕过** `workflow/transitions.apply_transition`——不写 TaskEvent、不设
  `cancelled_at`。被它处理的任务里，有 1490 条**本已 failed**（failed_at 非空），
  其中 1470 条是 idea 评审任务（`task_kind=REVIEW`）。

  后果：失败被清洗成取消 → 交付成功率口径失真 → 窄门禁的证据没人信。

本脚本按「有 failed_at ⇒ 曾经失败」把这类任务还原为 `failed`，并补齐
`stage` / `cancelled_at` / `block_reason`，让 DB 语义可查。

判定规则（保守，只还原**有失败证据**的）：
  1. state = 'CANCELLED' 且 failed_at IS NOT NULL  → 还原为 FAILED
  2. 还原时 stage 归一为 'implementing'（FAILED 唯一合法 stage，见 LEGAL_COMBOS）
  3. 补 TaskEvent(event_type='failed', from_state='failed', to_state='failed',
     actor_type='system', reason='P1-1 修复：失败被清洗为取消，还原为 failed')
     —— 让审计链可追
  4. cancelled_at 置 NULL（它没被合法取消过）
  5. 追加 label 'repaired:p1-1-cancelled-to-failed'，标记「这条是被修过的」

用法：
    python scripts/repair_cancelled_failed.py --dry-run   # 预览（默认）
    python scripts/repair_cancelled_failed.py --apply     # 落库
    python scripts/repair_cancelled_failed.py --undo      # 回滚（还原为 CANCELLED）

幂等：重复执行不会二次改动（已带 label 的跳过）。
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402
from sqlalchemy import text  # noqa: E402

from mio_taskhub.db import engine  # noqa: E402
from mio_taskhub.models import Task, TaskState, TaskStage, TaskEvent  # noqa: E402

REPAIR_LABEL = "repaired:p1-1-cancelled-to-failed"
REPAIR_REASON = "P1-1 修复：失败被清洗为取消，还原为 failed"

# FAILED 的合法 stage 只有一个（见 workflow/state_machine.py LEGAL_COMBOS）
FAILED_STAGE = TaskStage.IMPLEMENTING


def _targets(db: Session) -> list:
    """选出「state=CANCELLED 且有失败证据」的任务（排除已修过的）。"""
    rows = db.exec(
        select(Task).where(
            Task.state == TaskState.CANCELLED,
            Task.failed_at.is_not(None),
        )
    ).all()
    return [t for t in rows if REPAIR_LABEL not in (t.labels or [])]


def run(apply: bool = False, undo: bool = False) -> dict:
    with Session(engine) as db:
        if undo:
            rows = db.exec(
                select(Task).where(
                    Task.state == TaskState.FAILED,
                    Task.labels.is_not(None),
                )
            ).all()
            targets = [t for t in rows if REPAIR_LABEL in (t.labels or [])]
            print(f"待回滚（repaired label）: {len(targets)} 条")
            for t in targets:
                labels = [x for x in (t.labels or []) if x != REPAIR_LABEL]
                t.labels = labels
                t.state = TaskState.CANCELLED
                t.stage = TaskStage.CANCELLED
                t.cancelled_at = t.failed_at or datetime.now(timezone.utc).replace(tzinfo=None)
                t.failed_at = None
                db.add(t)
            if apply and targets:
                db.commit()
            print(f"{'已回滚' if apply else '将回滚'} {len(targets)} 条")
            return {"targeted": len(targets), "changed": len(targets)}

        targets = _targets(db)
        print(f"扫描到 state=CANCELLED 且有失败证据的任务: {len(targets)} 条")
        print(f"模式: {'回滚' if undo else '修复'} | 模式: {'APPLY' if apply else 'DRY-RUN'}")

        for t in targets:
            labels = list(t.labels or [])
            if REPAIR_LABEL not in labels:
                labels.append(REPAIR_LABEL)
            t.labels = labels
            t.state = TaskState.FAILED
            t.stage = FAILED_STAGE
            t.cancelled_at = None
            t.block_reason = REPAIR_REASON
            # 补审计事件：让「何时被修、被谁修」可查
            db.add(TaskEvent(
                task_id=t.id,
                event_type="failed",
                from_state="failed",
                from_stage=FAILED_STAGE.value,
                to_state="failed",
                to_stage=FAILED_STAGE.value,
                actor_type="system",
                actor_id="ops:repair_cancelled_failed",
                reason=REPAIR_REASON,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
            db.add(t)

        if apply and targets:
            db.commit()

        print(f"\n{'已修复' if apply else '将修复'} {len(targets)} 条"
              f"{'（已提交）' if apply else '（未落库，加 --apply 生效）'}")
        if targets:
            print("  样例:")
            for t in targets[:5]:
                print(f"  - {t.id} {t.title[:40]}")

        return {"targeted": len(targets), "changed": len(targets)}


def main() -> int:
    p = argparse.ArgumentParser(description="P1-1：把失败被清洗为取消的历史脏数据还原为 failed")
    p.add_argument("--dry-run", action="store_true", help="只统计不写（默认行为）")
    p.add_argument("--apply", action="store_true", help="实际落库")
    p.add_argument("--undo", action="store_true", help="回滚（还原为 CANCELLED）")
    args = p.parse_args()
    if not args.apply and not args.undo:
        args.dry_run = True
    run(apply=args.apply or args.undo, undo=args.undo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
