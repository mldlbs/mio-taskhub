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
from mio_taskhub.models import Task, TaskState, TaskKind, TaskStage  # noqa: E402

ARCHIVE_LABEL = "archived:spinning"
ARCHIVE_NOTE = "assessment-20260930:P1-1 空转垃圾任务归档"

# 需归档的自动生成任务：标题前缀匹配，或带 cron 生成（label cron:<id>）
SPINNING_TITLE_PREFIXES = ("[定时] 自动生成创意想法",)


def _is_spinning(t) -> bool:
    labels = t.labels or []
    if any((t.title or "").startswith(p) for p in SPINNING_TITLE_PREFIXES):
        return True
    # idea 评审任务（task_kind=REVIEW，标题「…」想法评审）——P11 空转风暴残留：
    # 评审任务失败后被反复重建（已加退避修复），历史 FAILED 残留归档。
    if (getattr(t, "task_kind", None) == TaskKind.REVIEW
            and (t.title or "").endswith("想法评审")
            and t.state in (TaskState.FAILED, TaskState.CANCELLED)):
        return True
    # 由 cron job 生成（label cron:<job_id>）且带 auto 标记
    has_cron = any(str(x).startswith("cron:") for x in labels)
    return has_cron and ("auto" in labels)


def run(apply: bool, undo: bool) -> int:
    with Session(engine) as db:
        # 空转任务：未认领（无 claimed_at）且处于排队态；涵盖 CANCELLED 与 QUEUED 两类
        # （CANCELLED = 已被清理的垃圾；QUEUED 且长期未认领 = 正在堆积的垃圾）。
        # 另含 REVIEW 类（idea 评审）——P11 风暴残留的 FAILED 评审任务（已认领，故单列）。
        tasks = db.exec(
            select(Task).where(
                (Task.claimed_at.is_(None) & Task.state.in_([TaskState.CANCELLED, TaskState.QUEUED]))
                | (Task.task_kind == TaskKind.REVIEW)
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
                # 归档语义：既打标签，又移出可领取队列——转 CANCELLED。
                # claim 候选只认 QUEUED/READY，转 CANCELLED 才真正不再被消费；
                # cancel 是状态变化非删除，--undo 可恢复。
                #
                # P1-1（决策文档 product-decision-20261020.md §4）：本脚本曾**绕过**
                # `workflow.transitions.apply_transition` 直接写 state，导致：
                #   ① 不写 TaskEvent → 审计链断裂，「谁在何时取消的」查不到；
                #   ② 不设 cancelled_at → 取消时间戳缺失；
                #   ③ **把已有失败证据（failed_at 非空）的任务也洗成 CANCELLED**
                #      → 交付成功率口径失真（实测 1490 条，其中 1470 条 idea 评审）。
                # 现在统一走 apply_transition：合法转移会写 TaskEvent + 时间戳。
                if ARCHIVE_LABEL in labels and t.state == TaskState.CANCELLED:
                    # 已归档但 stage 未同步 → 补正（幂等）
                    if t.stage != TaskStage.CANCELLED:
                        if apply:
                            t.stage = TaskStage.CANCELLED
                            db.add(t)
                        changed += 1
                    continue
                if ARCHIVE_LABEL not in labels:
                    labels.append(ARCHIVE_LABEL)
                if apply:
                    t.labels = labels
                    # 走状态机：合法转移 → 写 TaskEvent + cancelled_at + last_transition_at。
                    # 转移被拒（如已是终态）时**不硬写 state**，只留标签 —— 避免再次
                    # 制造「state 与事件不一致」的脏数据。
                    try:
                        from mio_taskhub.workflow.transitions import apply_transition
                        from mio_taskhub.workflow.state_machine import State, Stage, ActorType
                        _cur_stage = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
                        _stage_arg = Stage.BRAINSTORMING if _cur_stage == TaskStage.CANCELLED else Stage(_cur_stage.value)
                        _, m1_event = apply_transition(
                            t, State.CANCELLED, _stage_arg,
                            ActorType.SYSTEM, "ops:archive_spinning_tasks",
                            reason=f"空转任务归档[{ARCHIVE_LABEL}]",
                        )
                        if m1_event is not None:
                            db.add(m1_event)
                    except Exception as e:  # noqa: BLE001
                        # 转移非法（已终态等）→ 不强改 state，只打标签 + 留痕
                        print(f"  ! {t.id} 状态转移被拒（{e}）→ 仅打标签，不改 state")
                        t.block_reason = f"归档标签已打，状态转移被拒：{e}"
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
