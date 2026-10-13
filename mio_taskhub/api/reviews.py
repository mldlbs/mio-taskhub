from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.doc_paths import doc_path_of
from mio_taskhub.models import Task, TaskStage, TaskReview, Run
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event

router = APIRouter(prefix="/tasks", tags=["reviews"])

# 2026-10-10 审计 P0-4：approve 时审查结论的最低长度（未登记 review 文档时生效）。
# 原先 summary 是自由字符串，写 "x" 也会写入 review_result 并满足 done 门控。
MIN_APPROVE_SUMMARY = 8


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
    reviewer = (body.get("reviewer") or "").strip()
    summary = (body.get("summary") or "").strip()

    if decision == "approve":
        # 2026-10-10 审计 P0-4：评审人身份隔离。
        # 原先 reviewer 是自由字符串，执行该任务的 agent 可以自己 approve 自己 ——
        # 「独立验收」名存实亡。现默认拒绝自审；确有需要时显式传 self_review=true 放行。
        if reviewer and not body.get("self_review"):
            last_run = db.exec(
                select(Run).where(Run.task_id == task_id)
                .order_by(Run.started_at.desc())).first()
            if last_run is not None and last_run.agent_name == reviewer:
                raise HTTPException(409, detail={
                    "code": "review.self_approval",
                    "message": (f"评审人 '{reviewer}' 是本任务最近的执行者，"
                                f"不能自我 approve；请由他人评审，"
                                f"或显式传 self_review=true 声明为自审"),
                    "reviewer": reviewer,
                    "executor": last_run.agent_name,
                    "hint": "自审会在 TaskReview 中留痕，请确认这是有意为之",
                })
        # 结论内容下限：未登记 review 文档时，summary 太短不构成可追溯的审查结论
        if len(summary) < MIN_APPROVE_SUMMARY and not doc_path_of(t, "review"):
            raise HTTPException(422, detail={
                "code": "review.summary_too_short",
                "message": (f"approve 的审查结论至少 {MIN_APPROVE_SUMMARY} 个字符，"
                            f"当前 {len(summary)} 个；"
                            f"或先登记 review 文档（doc_paths['review']）再 approve"),
                "min_length": MIN_APPROVE_SUMMARY,
                "current_length": len(summary),
            })

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
        summary=summary,
        comments=body.get("comments", ""),
        artifacts=body.get("artifacts", []),
        reviewer=reviewer,
        review_duration_sec=duration_sec,
    )
    db.add(review)
    if decision == "approve":
        if summary:
            t.review_result = summary
        db.add(t)
    event = emit_event(db, type="review_submitted", entity="task", entity_id=task_id,
                       payload={"decision": decision, "reviewer": review.reviewer,
                                "summary": summary if decision == "approve" else ""})
    db.commit()
    return {
        "id": review.id, "decision": decision,
        "review_duration_sec": duration_sec,
    }
