"""评审带宽感知：基于历史吞吐动态计算可用槽位，反馈给闸门阈值。"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import Session, select, func

from mio_taskhub.models import Task, TaskState
from mio_taskhub.db import engine


@dataclass
class CapacityInfo:
    """评审容量信息。"""
    throughput: float          # 日均完成评审任务数
    queued: int                # 当前排队中的评审任务数
    running: int               # 正在进行的评审任务数
    available: int             # 可用槽位 = throughput * lookahead_days - queued - running


def compute_review_capacity(
    db: Session,
    now: Optional[datetime] = None,
    lookahead_days: int = 3
) -> CapacityInfo:
    """计算评审带宽（最近 7 天完成量 / 7 = 日均吞吐）。"""
    if now is None:
        now = datetime.now()

    week_ago = now - timedelta(days=7)

    # 日均吞吐：最近 7 天完成的"想法评审"任务
    completed = db.exec(
        select(func.count())
        .select_from(Task)
        .where(
            Task.title.like("%想法评审%"),
            Task.state == TaskState.COMPLETED,
            Task.completed_at.is_not(None),
            Task.completed_at >= week_ago,
        )
    ).one()
    completed = completed[0] if isinstance(completed, (tuple, list)) else completed
    throughput = (completed or 0) / 7.0

    # 当前积压
    queued = db.exec(
        select(func.count())
        .select_from(Task)
        .where(
            Task.title.like("%想法评审%"),
            Task.state == TaskState.QUEUED,
        )
    ).one()
    queued = queued[0] if isinstance(queued, (tuple, list)) else queued

    running = db.exec(
        select(func.count())
        .select_from(Task)
        .where(
            Task.title.like("%想法评审%"),
            Task.state.in_([TaskState.CLAIMED, TaskState.RUNNING]),
        )
    ).one()
    running = running[0] if isinstance(running, (tuple, list)) else running

    # 可用槽位 = 未来 lookahead_days 内预期完成量 - 当前占用
    available = max(0, int(throughput * lookahead_days) - (queued or 0) - (running or 0))

    return CapacityInfo(
        throughput=throughput,
        queued=queued or 0,
        running=running or 0,
        available=available,
    )