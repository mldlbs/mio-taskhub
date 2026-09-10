import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import (Idea, IdeaChange, IdeaStatus, IdeaType, Task,
                                IdeaHistory, TaskKind, TaskStage, TaskState)
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event


router = APIRouter(prefix="/ideas", tags=["ideas"])


def _get_next_adr_number(db: Session) -> int:
    """获取下一个 ADR 序号"""
    result = db.exec(
        select(Idea.adr_number)
        .where(Idea.adr_number.is_not(None))
        .order_by(Idea.adr_number.desc())
    ).first()
    if result is None:
        return 1
    return result + 1


def _idea_json(i: Idea) -> dict:
    return {
        "id": i.id, "title": i.title, "description": i.description,
        "status": i.status.value, "project": i.project, "labels": i.labels,
        "version": i.version,
        "last_reviewed_at": i.last_reviewed_at.isoformat() if i.last_reviewed_at else None,
        "review_count": i.review_count,
        "created_at": i.created_at.isoformat(), "updated_at": i.updated_at.isoformat(),
        "idea_type": i.idea_type.value,
        "adr_number": i.adr_number,
        "adr_status": i.adr_status.value if i.adr_status else None,
        "superseded_by": i.superseded_by,
        "madr_context": i.madr_context,
        "madr_decision": i.madr_decision,
        "madr_consequences": i.madr_consequences,
        "madr_alternatives": i.madr_alternatives,
        "adr_file_path": i.adr_file_path,
    }


@router.get("")
def list_ideas(
    status: str = None,
    project: str = "",
    idea_type: str = None,
    adr_status: str = None,
    db: Session = Depends(get_session)
):
    q = select(Idea)
    if status:
        try:
            q = q.where(Idea.status == IdeaStatus(status))
        except ValueError:
            raise HTTPException(400, f"invalid status: {status}")
    if project:
        q = q.where(Idea.project == project)
    if idea_type:
        try:
            q = q.where(Idea.idea_type == IdeaType(idea_type))
        except ValueError:
            raise HTTPException(400, f"invalid idea_type: {idea_type}")
    if adr_status:
        try:
            q = q.where(Idea.adr_status == IdeaStatus(adr_status))
        except ValueError:
            raise HTTPException(400, f"invalid adr_status: {adr_status}")
    rows = db.exec(q.order_by(Idea.updated_at.desc())).all()
    return {"count": len(rows), "ideas": [_idea_json(i) for i in rows]}


@router.post("", response_model=dict)
def create_idea(body: dict, db: Session = Depends(get_session)):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "title is required")
    i = Idea(
        title=title,
        description=body.get("description", ""),
        project=body.get("project", ""),
        labels=body.get("labels", []) or [],
    )
    db.add(i)
    event = emit_event(db, type="idea_created", entity="idea", entity_id=i.id,
                       payload={"title": i.title})
    db.commit()
    db.refresh(i)
    return _idea_json(i)


@router.patch("/{idea_id}")
def update_idea(idea_id: str, body: dict, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    versioning = body.get("versioning", "full")
    if versioning not in ("full", "history_only", "none"):
        raise HTTPException(422, f"invalid versioning, expected one of full/history_only/none: {versioning}")
    track_change = body.get("track_change", True)
    if not isinstance(track_change, bool):
        track_change = str(track_change).lower() not in ("false", "0", "no", "")

    diff = {}
    for f in ("title", "description", "project", "labels"):
        if f in body and body[f] is not None:
            old = getattr(i, f)
            if old != body[f]:
                diff[f] = {"old": old, "new": body[f]}

    if diff:
        for f, d in diff.items():
            setattr(i, f, d["new"])
        if versioning == "full":
            i.version += 1
        if versioning in ("full", "history_only"):
            db.add(IdeaChange(idea_id=idea_id, version=i.version, diff=diff,
                              reason=body.get("change_reason", "")))
        i.updated_at = _now()
        event = emit_event(db, type="idea_updated", entity="idea", entity_id=i.id,
                           payload={"version": i.version})
        db.add(i)
        db.commit()
        db.refresh(i)
        if versioning == "full" and track_change:
            track_ev = _upsert_change_tracking_task(i, diff, db,
                                                    reason=body.get("change_reason", ""))
            if track_ev:
                db.commit()
        return _idea_json(i)
    i.updated_at = _now()
    event = emit_event(db, type="idea_updated", entity="idea", entity_id=i.id,
                       payload={"version": i.version})
    db.add(i)
    db.commit()
    db.refresh(i)
    return _idea_json(i)


def _build_description(i: Idea, diff: dict, reason: str = "") -> str:
    summary = "；".join(f"{f}: {d['old']} → {d['new']}" for f, d in diff.items())
    desc = f"需求已变更到 v{i.version}。请 review 已拆解任务与 spec 是否需同步。\n变更内容：{summary}"
    if reason:
        desc += f"\n变更原因：{reason}"
    return desc


def _upsert_change_tracking_task(i: Idea, diff: dict, db: Session, reason: str = ""):
    related = db.exec(select(Task).where(Task.idea_id == i.id)).first()
    if related is None:
        return None
    existing = db.exec(select(Task).where(
        Task.idea_id == i.id,
        Task.task_kind == TaskKind.CHANGE_TRACKING,
        Task.state.not_in([TaskState.COMPLETED, TaskState.CANCELLED]),
    )).first()
    title = f"[变更] {i.title} v{i.version}"
    description = _build_description(i, diff, reason)
    if existing:
        existing.title = title
        existing.description = description
        ev = emit_event(db, type="task_updated", entity="task", entity_id=existing.id,
                        payload={"title": title})
        return ev
    t = Task(
        id=str(uuid.uuid4())[:8],
        title=title,
        description=description,
        stage=TaskStage.REVIEW,
        idea_id=i.id,
        task_kind=TaskKind.CHANGE_TRACKING,
    )
    db.add(t)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.exec(select(Task).where(
            Task.idea_id == i.id,
            Task.task_kind == TaskKind.CHANGE_TRACKING,
            Task.state.not_in([TaskState.COMPLETED, TaskState.CANCELLED]),
        )).first()
        if existing:
            existing.title = title
            existing.description = description
            ev = emit_event(db, type="task_updated", entity="task", entity_id=existing.id,
                            payload={"title": title})
            return ev
        raise
    ev = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                    payload={"title": title})
    return ev


@router.post("/{idea_id}/status")
def set_idea_status(idea_id: str, body: dict, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    try:
        dst = IdeaStatus(body.get("status", ""))
    except ValueError:
        raise HTTPException(400, f"invalid status, expected one of {[e.value for e in IdeaStatus]}")
    if not IdeaStatus.can_advance(i.status, dst):
        raise HTTPException(422, f"cannot advance from {i.status.value} to {dst.value}")
    transition_idea_status(i, dst, db, actor="user", source="manual")
    db.commit()
    db.refresh(i)
    return _idea_json(i)


_RECOMMEND_MAP = {
    "ferment": IdeaStatus.FERMENTING,
    "form": IdeaStatus.FORMED,
    "archive": IdeaStatus.ARCHIVED,
}


@router.post("/{idea_id}/review")
def submit_review(idea_id: str, body: dict, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    recommend = (body.get("recommend") or "").strip()
    reasoning = body.get("reasoning")
    actor = body.get("actor") or "agent"

    if recommend == "nothing":
        i.review_count += 1
        i.last_reviewed_at = _now()
        i.updated_at = _now()
        db.add(i)
        db.add(IdeaHistory(
            idea_id=idea_id,
            kind="review",
            actor=actor,
            content=f"评审：暂不推进（recommend=nothing）",
            reasoning=reasoning,
            extra={"recommend": "nothing"},
        ))
        event = emit_event(db, type="idea_review", entity="idea", entity_id=idea_id,
                           payload={"recommend": "nothing", "reasoning": reasoning, "actor": actor})
        db.commit()
        db.refresh(i)
        return _idea_json(i)

    if recommend not in _RECOMMEND_MAP:
        raise HTTPException(400, f"invalid recommend: {recommend}")

    dst = _RECOMMEND_MAP[recommend]
    if not IdeaStatus.can_advance(i.status, dst):
        raise HTTPException(422, f"cannot advance from {i.status.value} to {dst.value}")

    from_status = i.status.value
    i.status = dst
    i.review_count += 1
    i.last_reviewed_at = _now()
    i.updated_at = _now()
    db.add(i)
    db.add(IdeaHistory(
        idea_id=idea_id,
        kind="status",
        actor=actor,
        content=f"评审推进：{from_status} → {dst.value}",
        reasoning=None,
        extra={"from": from_status, "to": dst.value, "source": "review"},
    ))
    db.add(IdeaHistory(
        idea_id=idea_id,
        kind="review",
        actor=actor,
        content=f"评审 recommend={recommend}",
        reasoning=reasoning,
        extra={"recommend": recommend, "from": from_status, "to": dst.value},
    ))
    event = emit_event(db, type="idea_review", entity="idea", entity_id=idea_id,
                       payload={"recommend": recommend, "reasoning": reasoning, "actor": actor})
    db.commit()
    db.refresh(i)
    return _idea_json(i)


def transition_idea_status(idea: Idea, dst: IdeaStatus, db: Session, actor: str = "system", source: str = "manual"):
    if not IdeaStatus.can_advance(idea.status, dst):
        raise HTTPException(422, f"cannot advance from {idea.status.value} to {dst.value}")
    from_status = idea.status.value
    idea.status = dst
    idea.updated_at = _now()
    db.add(idea)
    db.add(IdeaHistory(
        idea_id=idea.id,
        kind="status",
        actor=actor,
        content=f"{from_status} → {dst.value}",
        reasoning=None,
        extra={"from": from_status, "to": dst.value, "source": source},
    ))
    event = emit_event(db, type="idea_status", entity="idea", entity_id=idea.id,
                       payload={"status": dst.value, "source": source})