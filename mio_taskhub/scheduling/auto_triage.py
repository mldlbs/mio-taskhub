"""自动初筛器：INBOX -> NEW（高分）或 INBOX -> ARCHIVED（低分老龄）。"""
from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import Session, select

from mio_taskhub.models import Idea, IdeaStatus
from mio_taskhub.scheduling.gate import CFG, idea_weight
from mio_taskhub.db import engine


def auto_triage(db: Session, now: Optional[datetime] = None) -> int:
    """每小时跑一次：INBOX -> NEW 或 INBOX -> ARCHIVED。
    
    返回: 本次晋升到 NEW 的数量。
    """
    if now is None:
        now = datetime.now()
    # Ensure naive datetime for comparison with idea.created_at
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)

    candidates = db.exec(
        select(Idea).where(Idea.status == IdeaStatus.INBOX)
    ).all()

    promoted = 0
    for idea in candidates:
        score = idea_weight(idea, now)
        age_days = (now - idea.created_at).days

        # 高分直接晋升 NEW
        if score >= CFG.auto_promote_score:
            idea.status = IdeaStatus.NEW
            idea.updated_at = now
            promoted += 1
        # 低分且老龄：自动归档
        elif age_days > CFG.max_inbox_days and score < CFG.archive_score:
            idea.status = IdeaStatus.ARCHIVED
            idea.updated_at = now
            # 打标记以便审计
            labels = list(idea.labels or [])
            if "auto-triage:archived" not in labels:
                labels.append("auto-triage:archived")
            idea.labels = labels
        # else: 留在 INBOX 等待下一轮或人工处理

    if promoted:
        db.commit()
    return promoted


def manual_triage(db: Session, idea_id: str, action: str, actor: str = "user") -> bool:
    """人工初筛：promote / archive / defer。
    
    返回: 是否成功。
    """
    idea = db.get(Idea, idea_id)
    if not idea or idea.status != IdeaStatus.INBOX:
        return False

    now = datetime.now()
    if action == "promote":
        idea.status = IdeaStatus.NEW
        idea.updated_at = now
    elif action == "archive":
        idea.status = IdeaStatus.ARCHIVED
        idea.updated_at = now
        labels = list(idea.labels or [])
        if "manual-triage:archived" not in labels:
            labels.append("manual-triage:archived")
        idea.labels = labels
    elif action == "defer":
        # 什么都不做，只是留在 INBOX，updated_at 刷新防止被自动归档
        idea.updated_at = now
    else:
        return False

    db.add(idea)
    db.commit()
    return True


def batch_triage(db: Session, idea_ids: list[str], action: str, actor: str = "user") -> int:
    """批量初筛。"""
    success = 0
    for iid in idea_ids:
        if manual_triage(db, iid, action, actor):
            success += 1
    return success