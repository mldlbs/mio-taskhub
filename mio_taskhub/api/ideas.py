import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import (Idea, IdeaChange, IdeaStatus, Task, TaskKind, TaskStage,
                                Discussion, DiscussionMessage, TaskState, IdeaHistory)
from mio_taskhub.utils import _now
from mio_taskhub.status import normalize_depends, task_deps
from mio_taskhub.planner import detect_cycle
from mio_taskhub.events import emit_event, broadcast_for_event

router = APIRouter(prefix="/ideas", tags=["ideas"])


def _idea_json(i: Idea) -> dict:
    return {
        "id": i.id, "title": i.title, "description": i.description,
        "status": i.status.value, "project": i.project, "labels": i.labels,
        "version": i.version,
        "last_reviewed_at": i.last_reviewed_at.isoformat() if i.last_reviewed_at else None,
        "review_count": i.review_count,
        "created_at": i.created_at.isoformat(), "updated_at": i.updated_at.isoformat(),
    }


@router.get("")
def list_ideas(status: str = None, project: str = "", db: Session = Depends(get_session)):
    q = select(Idea)
    if status:
        try:
            q = q.where(Idea.status == IdeaStatus(status))
        except ValueError:
            raise HTTPException(400, f"invalid status: {status}")
    if project:
        q = q.where(Idea.project == project)
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
    broadcast_for_event(event)
    return _idea_json(i)


@router.get("/{idea_id}")
def get_idea(idea_id: str, include_changes: bool = Query(True),
             before_id: int = Query(None), limit: int = Query(20, ge=1, le=100),
             db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    out = _idea_json(i)
    rows = db.exec(select(Discussion).where(Discussion.idea_id == idea_id).order_by(Discussion.started_at)).all()
    ds = []
    for d in rows:
        msgs = db.exec(select(DiscussionMessage).where(DiscussionMessage.discussion_id == d.id).order_by(DiscussionMessage.at)).all()
        ds.append({
            "id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
            "summary": d.summary, "conclusions": d.conclusions, "stage": d.stage,
            "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None,
            "messages": [{"author": m.author, "role": m.role, "content": m.content,
                          "at": m.at.isoformat()} for m in msgs],
        })
    out["discussions"] = ds
    tasks = db.exec(select(Task).where(Task.idea_id == idea_id).order_by(Task.created_at)).all()
    out["tasks"] = [{"id": t.id, "title": t.title, "stage": t.stage.value,
                     "state": t.state.value} for t in tasks]
    if include_changes:
        q = select(IdeaChange).where(IdeaChange.idea_id == idea_id)
        if before_id is not None:
            q = q.where(IdeaChange.id < before_id)
        changes = db.exec(q.order_by(IdeaChange.id.desc()).limit(limit)).all()
        out["changes"] = [{
            "id": c.id, "version": c.version, "created_at": c.created_at.isoformat(),
            "diff": c.diff, "reason": c.reason,
        } for c in changes]
    return out


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
        db.commit()          # 先提交 idea 本体与历史（版本一致性）
        db.refresh(i)
        broadcast_for_event(event)
        if versioning == "full" and track_change:
            track_ev = _upsert_change_tracking_task(i, diff, db,
                                                    reason=body.get("change_reason", ""))
            if track_ev:
                db.commit()
                broadcast_for_event(track_ev)
        return _idea_json(i)
    # 无 diff：仅更新 updated_at 并 emit（保持既有行为，不递增版本）
    i.updated_at = _now()
    event = emit_event(db, type="idea_updated", entity="idea", entity_id=i.id,
                       payload={"version": i.version})
    db.add(i)
    db.commit()
    db.refresh(i)
    broadcast_for_event(event)
    return _idea_json(i)


def _build_description(i: Idea, diff: dict, reason: str = "") -> str:
    summary = "；".join(f"{f}: {d['old']} → {d['new']}" for f, d in diff.items())
    desc = f"需求已变更到 v{i.version}。请 review 已拆解任务与 spec 是否需同步。\n变更内容：{summary}"
    if reason:
        desc += f"\n变更原因：{reason}"
    return desc


def _upsert_change_tracking_task(i: Idea, diff: dict, db: Session, reason: str = ""):
    """在存在关联 task 的前提下，生成/更新唯一一条活跃变更跟踪任务。返回 Event 或 None。"""
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
        # 并发下已有活跃变更任务：回退为更新（不动 version/历史）
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


def transition_idea_status(idea: Idea, dst: IdeaStatus, db: Session, actor: str = "system", source: str = "manual"):
    """唯一状态变更入口：校验 can_advance → 变更状态 + updated_at + kind=status 轨迹 + emit event。
    供 set_idea_status / breakdown_idea / review / archive/cancel 复用。"""
    if not IdeaStatus.can_advance(idea.status, dst):
        raise HTTPException(422, f"cannot advance from {idea.status.value} to {dst.value}")
    from_status = idea.status.value
    idea.status = dst
    idea.updated_at = _now()
    db.add(idea)
    # 写 kind=status 轨迹
    db.add(IdeaHistory(
        idea_id=idea.id,
        kind="status",
        reasoning=None,
        extra={"from": from_status, "to": dst.value, "source": source},
    ))
    event = emit_event(db, type="idea_status", entity="idea", entity_id=idea.id,
                       payload={"status": dst.value, "source": source})
    # 注意：不在此 commit，由调用者统一提交（保证原子性）


@router.get("/{idea_id}/history")
def get_idea_history(idea_id: str, page: int = 1, page_size: int = 50, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50
    # 总数
    from sqlalchemy import func
    total = db.exec(select(func.count(IdeaHistory.id)).where(IdeaHistory.idea_id == idea_id)).one()
    # 分页：新的在前
    items = db.exec(
        select(IdeaHistory)
        .where(IdeaHistory.idea_id == idea_id)
        .order_by(IdeaHistory.at.desc(), IdeaHistory.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "items": [{
            "id": h.id,
            "kind": h.kind,
            "reasoning": h.reasoning,
            "extra": h.extra,
            "at": h.at.isoformat(),
        } for h in items]
    }


@router.post("/{idea_id}/breakdown")
def breakdown_idea(idea_id: str, body: dict, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    if i.status == IdeaStatus.BROKEN_DOWN:
        raise HTTPException(409, "already broken down")
    items = body.get("tasks", [])
    if not items:
        raise HTTPException(422, "tasks is required")
    refs = [(it.get("ref") or "").strip() for it in items]
    non_empty = [r for r in refs if r]
    if len(set(non_empty)) != len(non_empty):
        raise HTTPException(422, "duplicate ref")
    created = []
    try:
        for it in items:
            title = (it.get("title") or "").strip()
            if not title:
                raise HTTPException(422, "task title is required")
            stage_val = it.get("stage", "brainstorming")
            try:
                stage = TaskStage(stage_val)
            except ValueError:
                raise HTTPException(400, f"invalid stage: {stage_val}")
            t = Task(
                title=title,
                description=it.get("description", ""),
                target_agent_type=it.get("target_agent_type"),
                priority=it.get("priority", 0),
                est_duration_min=it.get("est_duration_min", 30),
                max_retries=it.get("max_retries", 3),
                acceptance_criteria=it.get("acceptance_criteria", ""),
                depends_on=normalize_depends(it.get("depends_on")),
                idea_id=idea_id,
                stage=stage,
            )
            db.add(t)
            created.append(t)
        db.flush()  # 获得自增 id 与 ref2id
        ref2id = {it.get("ref"): t.id for it, t in zip(items, created) if it.get("ref")}
        # 解析 depends_on：ref → real id；未知 ref/id → 422
        for it, t in zip(items, created):
            resolved = []
            for dep in normalize_depends(it.get("depends_on")):
                real = ref2id.get(dep, dep)
                if real not in [x.id for x in created] and db.get(Task, real) is None:
                    raise HTTPException(422, f"unknown dependency ref: {dep}")
                resolved.append(real)
            t.depends_on = resolved
        # 环检测
        graph = {t.id: list(t.depends_on or []) for t in created}
        cyc = detect_cycle(graph)
        if cyc:
            raise HTTPException(422, f"cyclic dependency: {' → '.join(cyc)}")
        transition_idea_status(i, IdeaStatus.BROKEN_DOWN, db, actor="user", source="breakdown")
        idea_event = emit_event(db, type="idea_broken_down", entity="idea", entity_id=idea_id,
                                payload={"action": "broken_down",
                                         "task_ids": [t.id for t in created]})
        task_events = [emit_event(db, type="task_created", entity="task", entity_id=t.id,
                                  payload={"title": t.title, "stage": t.stage.value})
                       for t in created]
        db.add(i)
        db.commit()
        db.refresh(i)
    except HTTPException:
        db.rollback()
        raise
    broadcast_for_event(idea_event)
    for ev in task_events:
        broadcast_for_event(ev)
    return {
        "idea": _idea_json(db.get(Idea, idea_id)),
        "tasks": [{"id": t.id, "title": t.title, "ref": it.get("ref", ""),
                   "depends_on": task_deps(t)}
                  for it, t in zip(items, created)],
    }