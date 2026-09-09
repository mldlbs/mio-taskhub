from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskStage, TaskReview
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event, broadcast_for_event

router = APIRouter(prefix="/tasks", tags=["reviews"])


@router.get("/reviews/queue")
def review_queue(db: Session = Depends(get_session)):
    """返回处于 review 阶段的任务列表 + 审阅统计。"""
    tasks = db.exec(
        select(Task).where(Task.stage == TaskStage.REVIEW).order_by(Task.created_at)
    ).all()
    items = []
    for t in tasks:
        wait_sec = None
        if t.review_started_at:
            started = t.review_started_at
            now = _now()
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            delta = (now - started).total_seconds()
            wait_sec = int(delta)
        items.append({
            "id": t.id, "title": t.title, "priority": t.priority,
            "state": t.state.value, "stage": t.stage.value,
            "target_agent_type": t.target_agent_type,
            "project": t.project, "workspace": t.workspace,
            "review_started_at": t.review_started_at.isoformat() if t.review_started_at else None,
            "wait_seconds": wait_sec,
            "review_result": t.review_result,
            "attempt": t.attempt,
        })
    return {"tasks": items, "count": len(items)}


@router.get("/{task_id}/reviews")
def list_reviews(task_id: str, db: Session = Depends(get_session)):
    """返回任务的审阅历史记录。"""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    reviews = db.exec(
        select(TaskReview).where(TaskReview.task_id == task_id).order_by(TaskReview.created_at)
    ).all()
    return [
        {
            "id": r.id, "task_id": r.task_id, "decision": r.decision,
            "checklist": r.checklist, "summary": r.summary, "comments": r.comments,
            "artifacts": r.artifacts, "reviewer": r.reviewer,
            "review_duration_sec": r.review_duration_sec,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reviews
    ]


@router.post("/{task_id}/reviews")
def submit_review(task_id: str, body: dict, db: Session = Depends(get_session)):
    """提交审阅记录。decision: approve / reject / comment。"""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    decision = body.get("decision", "comment")
    if decision not in ("approve", "reject", "comment"):
        raise HTTPException(422, "decision must be approve, reject, or comment")
    duration_sec = None
    if t.review_started_at:
        started = t.review_started_at
        now = _now()
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        duration_sec = int((now - started).total_seconds())
    review = TaskReview(
        task_id=task_id,
        decision=decision,
        checklist=body.get("checklist"),
        summary=body.get("summary", ""),
        comments=body.get("comments", ""),
        artifacts=body.get("artifacts", []),
        reviewer=body.get("reviewer", ""),
        review_duration_sec=duration_sec,
    )
    db.add(review)
    if decision == "approve":
        summary = body.get("summary", "")
        if summary:
            t.review_result = summary
        db.add(t)
    event = emit_event(db, type="review_submitted", entity="task", entity_id=task_id,
                       payload={"decision": decision, "reviewer": review.reviewer,
                                "summary": summary if decision == "approve" else ""})
    db.commit()
    broadcast_for_event(event)
    return {
        "id": review.id, "decision": decision,
        "review_duration_sec": duration_sec,
    }
