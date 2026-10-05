# -*- coding: utf-8 -*-
"""归档历史空转垃圾任务（P1-1，见 docs/taskhub/system-assessment-20260930.md）。

背景：定时任务「[定时] 自动生成创意想法」等自动链路曾每日创建任务、几乎无人认领即被取消
（生产库 142 个 CANCELLED 中 97 个来自该标题）。本脚本把这类「系统自动生成、无人消费」的
已取消任务打上归档标签，供看板过滤；**不物理删除**，可回滚。

用法：
    python scripts/archive_spinning_tasks.py --dry-run   # 预览（默认）
    python scripts/archive_spinning_tasks.py --apply     # 落库
    python scripts/archive_spinning_tasks.py --apply --undo  # 撤销归档

幂等：重复执行不会重复打标签；--undo 可逆。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from mio_taskhub.db import engine  # noqa: E402
from mio_taskhub.models import Task, TaskState  # noqa: E402

ARCHIVE_LABEL = "archived:spinning"
ARCHIVE_NOTE = "assessment-20260930:P1-1 空转垃圾任务归档"

# 需归档的自动生成任务：标题前缀匹配，或由 cron 生成（label cron:<id>）
SPINNING_TITLE_PREFIXES = ("[定时] 自动生成创意想法",)


def _is_spinning(t) -> bool:
    labels = t.labels or []
    if any((t.title or "").startswith(p) for p in SPINNING_TITLE_PREFIXES):
        return True
    # 由 cron job 生成（label cron:<job_id>）且带 auto 标记
    has_cron = any(str(x).startswith("cron:") for x in labels)
    return has_cron and ("auto" in labels)


def run(apply: bool, undo: bool) -> int:
    with Session(engine) as db:
        # 空转任务：未认领（无 claimed_at）且处于排队态；涵盖 CANCELLED 与 QUEUED 两类
        # （CANCELLED = 已被清理的垃圾；QUEUED 且长期未认领 = 正在堆积的垃圾）。
        tasks = db.exec(
            select(Task).where(
                Task.claimed_at.is_(None),
                Task.state.in_([TaskState.CANCELLED, TaskState.QUEUED]),
            )
        ).all()
        targets = [t for t in tasks if _is_spinning(t)]
        already = [t for t in targets if ARCHIVE_LABEL in (t.labels or [])]

        print(f"扫描到未认领任务 {len(tasks)} 条，其中空转任务 {len(targets)} 条，"
              f"已归档 {len(already)} 条")
        print(f"模式：{'撤销归档' if undo else '归档'} | 模式：{'APPLY' if apply else 'DRY-RUN'}")

        changed = 0
        for t in targets:
            labels = list(t.labels or [])
            if undo:
                if ARCHIVE_LABEL not in labels:
                    continue
                labels = [x for x in labels if x != ARCHIVE_LABEL]
                if apply:
                    t.labels = labels
                    # 恢复到队列（若此前被归档取消了）
                    if t.state == TaskState.CANCELLED:
                        t.state = TaskState.QUEUED
                    db.add(t)
            else:
                # 归档语义（修正）：既打标签，又移出可领取队列——转 CANCELLED。
                # claim 候选只认 QUEUED/READY，转 CANCELLED 才真正不再被消费；
                # cancel 是状态变化非删除，--undo 可恢复。
                if ARCHIVE_LABEL in labels and t.state == TaskState.CANCELLED:
                    continue
                if ARCHIVE_LABEL not in labels:
                    labels.append(ARCHIVE_LABEL)
                if apply:
                    t.labels = labels
                    t.state = TaskState.CANCELLED
                    db.add(t)
            changed += 1
            if changed <= 10:
                print(f"  {'-' if undo else '+'} {t.id} {t.title[:40]}")

        if apply and changed:
            db.commit()
        print(f"\n{'已处理' if apply else '将处理'} {changed} 条"
              f"{'（已提交）' if apply else '（未落库，加 --apply 生效）'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="归档历史空转垃圾任务（P1-1）")
    ap.add_argument("--apply", action="store_true", help="实际落库（默认 dry-run）")
    ap.add_argument("--undo", action="store_true", help="撤销归档")
    args = ap.parse_args()
    return run(apply=args.apply, undo=args.undo)


if __name__ == "__main__":
    raise SystemExit(main())
