# mio_taskhub/idea_review.py
"""Idea 自动评审调度：按间隔派发 task_kind=REVIEW 任务给 agent 评审。

配置（环境变量）：
- MIO_IDEA_REVIEW_INTERVAL_MIN     默认 1440（距上次评审）
- MIO_IDEA_REVIEW_INITIAL_DELAY_MIN 默认 60（创建后首评冷却）
- MIO_IDEA_REVIEW_ENABLED          默认 1
"""
import os
from datetime import datetime, timezone
from sqlmodel import Session, select
from mio_taskhub.db import engine
from mio_taskhub.models import (Idea, IdeaStatus, Task, TaskKind, TaskStage,
                                TaskState)
from mio_taskhub.scheduler import Scheduler
from mio_taskhub.events import emit_event, broadcast_for_event

INTERVAL_MIN = int(os.environ.get("MIO_IDEA_REVIEW_INTERVAL_MIN", "1440"))
INITIAL_DELAY_MIN = int(os.environ.get("MIO_IDEA_REVIEW_INITIAL_DELAY_MIN", "60"))
ENABLED = os.environ.get("MIO_IDEA_REVIEW_ENABLED", "1") not in ("0", "false", "")

# 评审命中的非终态（去重参照：排队/领取/运行/重试均视为在途）
_INFLIGHT_STATES = [TaskState.QUEUED, TaskState.CLAIMED,
                    TaskState.RUNNING, TaskState.RETRYING]
_REVIEWABLE = [IdeaStatus.NEW, IdeaStatus.FERMENTING, IdeaStatus.FORMED]


def _utc(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _has_inflight_review(db: Session, idea_id: str) -> bool:
    t = db.exec(select(Task).where(
        Task.idea_id == idea_id,
        Task.task_kind == TaskKind.REVIEW,
        Task.state.in_(_INFLIGHT_STATES),
    )).first()
    return t is not None


def _due_idea_ids() -> list:
    """返回需要评审（且无在途评审任务）的 idea id。"""
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        ideas = db.exec(select(Idea).where(Idea.status.in_(_REVIEWABLE))).all()
        due = []
        for i in ideas:
            if _has_inflight_review(db, i.id):
                continue
            created = _utc(i.created_at) or now
            last = _utc(i.last_reviewed_at)
            if last is None:
                if (now - created).total_seconds() >= INITIAL_DELAY_MIN * 60:
                    due.append(i.id)
            else:
                if (now - last).total_seconds() >= INTERVAL_MIN * 60:
                    due.append(i.id)
        return due


def _enqueue(idea_id: str):
    """为 idea 建一个待领取的评审任务（去重：在途则跳过）。"""
    with Session(engine) as db:
        idea = db.get(Idea, idea_id)
        if not idea:
            return
        if _has_inflight_review(db, idea_id):
            return
        t = Task(
            title=f"「{idea.title}」想法评审",
            description=("对想法进行评审：调用 taskhub_review_idea 获取详情+判定清单（描述完整度/"
                         "讨论活跃度/存活时长/重复检测），评估后调用 taskhub_submit_review 提交结论"
                         "（recommend ∈ nothing/ferment/form/archive，附 reasoning）。"),
            target_agent_type="idea-reviewer",
            priority=1,
            est_duration_min=30,
            task_kind=TaskKind.REVIEW,
            idea_id=idea_id,
            stage=TaskStage.READY,
        )
        db.add(t)
        event = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                           payload={"title": t.title, "stage": t.stage.value,
                                    "task_kind": t.task_kind.value, "idea_id": idea_id})
        db.commit()
        broadcast_for_event(event)


class IdeaReviewScanner(Scheduler):
    """复用 Scheduler：get_due_tasks 返回待评审 idea id；on_enqueue 建评审任务。"""

    def __init__(self, interval: float = 60.0):
        super().__init__(interval=interval,
                         get_due_tasks=self._get_due,
                         on_enqueue=self._on_enqueue)

    def _get_due(self):
        if not ENABLED:
            return []
        return [{"id": iid} for iid in _due_idea_ids()]

    def _on_enqueue(self, idea_id: str):
        _enqueue(idea_id)