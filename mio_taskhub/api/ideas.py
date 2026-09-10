import re
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import (Idea, IdeaChange, IdeaStatus, IdeaType, Task, TaskKind, TaskStage,
                                Discussion, DiscussionMessage, TaskState, IdeaHistory, ChangeType,
                                OutboxEvent, OutboxStatus)
from mio_taskhub.utils import _now
from mio_taskhub.dependency import normalize_depends, task_deps
from mio_taskhub.planner import detect_cycle
from mio_taskhub.events import emit_event
from mio_taskhub.idea_prompts import DEFAULT_TEMPLATES, IdeaTemplate, get_template_by_id, get_templates_by_category

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
        # ADR 扩展字段
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
        if versioning == "full" and track_change:
            track_ev = _upsert_change_tracking_task(i, diff, db,
                                                    reason=body.get("change_reason", ""))
            if track_ev:
                db.commit()
        return _idea_json(i)
    # 无 diff：仅更新 updated_at 并 emit（保持既有行为，不递增版本）
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


# recommend 映射：agent 给出期望目标状态，hub 只推进一档
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

    # nothing：只记录评审，不推进
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

    # 非法 recommend
    if recommend not in _RECOMMEND_MAP:
        raise HTTPException(400, f"invalid recommend: {recommend}")

    dst = _RECOMMEND_MAP[recommend]
    if not IdeaStatus.can_advance(i.status, dst):
        raise HTTPException(422, f"cannot advance from {i.status.value} to {dst.value}")

    # 推进状态 + 写 kind=status + 更新元数据（单事务）
    from_status = i.status.value
    i.status = dst
    i.review_count += 1
    i.last_reviewed_at = _now()
    i.updated_at = _now()
    db.add(i)
    # kind=status 轨迹
    db.add(IdeaHistory(
        idea_id=idea_id,
        kind="status",
        actor=actor,
        content=f"评审推进：{from_status} → {dst.value}",
        reasoning=None,
        extra={"from": from_status, "to": dst.value, "source": "review"},
    ))
    # kind=review 轨迹
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
        actor=actor,
        content=f"{from_status} → {dst.value}",
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
            "actor": h.actor,
            "content": h.content,
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
        existing = db.exec(select(Task).where(Task.idea_id == idea_id)).all()
        if existing and not body.get("force"):
            raise HTTPException(409, f"already broken down ({len(existing)} tasks exist); pass force=true to add more")
    items = body.get("tasks", [])
    if not items:
        raise HTTPException(422, "tasks is required")
    _validate_refs(items)
    created = []
    try:
        created = _create_tasks_from_items(db, items, idea_id)
        db.flush()
        _resolve_dependencies(db, items, created)
        _check_cycles(created)
        if i.status != IdeaStatus.BROKEN_DOWN:
            transition_idea_status(i, IdeaStatus.BROKEN_DOWN, db, actor="user", source="breakdown")
        idea_event, task_events = _emit_breakdown_events(db, idea_id, created)
        db.add(i)
        db.commit()
        db.refresh(i)
    except HTTPException:
        db.rollback()
        raise
    return {
        "idea": _idea_json(db.get(Idea, idea_id)),
        "tasks": [{"id": t.id, "title": t.title, "ref": it.get("ref", ""),
                   "depends_on": task_deps(t)}
                  for it, t in zip(items, created)],
    }


def _validate_refs(items):
    """校验 ref 唯一性。"""
    refs = [(it.get("ref") or "").strip() for it in items]
    non_empty = [r for r in refs if r]
    if len(set(non_empty)) != len(non_empty):
        raise HTTPException(422, "duplicate ref")


def _create_tasks_from_items(db, items, idea_id):
    """从 items 创建 Task 对象（未 flush）。"""
    created = []
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
    return created


def _resolve_dependencies(db, items, created):
    """解析 depends_on：ref → real id。"""
    ref2id = {it.get("ref"): t.id for it, t in zip(items, created) if it.get("ref")}
    for it, t in zip(items, created):
        resolved = []
        for dep in normalize_depends(it.get("depends_on")):
            real = ref2id.get(dep, dep)
            if real not in [x.id for x in created] and db.get(Task, real) is None:
                raise HTTPException(422, f"unknown dependency ref: {dep}")
            resolved.append(real)
        t.depends_on = resolved


def _check_cycles(created):
    """环检测。"""
    graph = {t.id: list(t.depends_on or []) for t in created}
    cyc = detect_cycle(graph)
    if cyc:
        raise HTTPException(422, f"cyclic dependency: {' → '.join(cyc)}")


def _emit_breakdown_events(db, idea_id, created):
    """发送 breakdown 事件。"""
    idea_event = emit_event(db, type="idea_broken_down", entity="idea", entity_id=idea_id,
                            payload={"action": "broken_down",
                                     "task_ids": [t.id for t in created]})
    task_events = [emit_event(db, type="task_created", entity="task", entity_id=t.id,
                              payload={"title": t.title, "stage": t.stage.value})
                   for t in created]
    return idea_event, task_events


@router.post("/{idea_id}/suggest-tasks")
def suggest_tasks(idea_id: str, body: dict = None, db: Session = Depends(get_session)):
    """从想法的描述、讨论结论、变更记录中自动提取任务草案。"""
    body = body or {}
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")

    max_tasks = min(body.get("max_tasks", 10), 20)
    context_hint = (body.get("context") or "").strip()

    desc, conclusions, changes = _collect_source_data(db, idea_id)
    blocks = _split_description(desc)
    conclusion_tasks = _extract_conclusion_tasks(conclusions)
    change_tasks = _extract_change_tasks(changes)
    suggestions = _generate_suggestions(blocks, conclusion_tasks, change_tasks, max_tasks)

    if not suggestions:
        return {
            "suggestions": [],
            "source_context": desc[:500] if desc else "",
            "message": "描述过于简短，无法自动拆解。建议先补充描述或开启讨论后再试。",
        }

    _link_dependencies(suggestions)
    source_context = _build_source_context(desc, conclusions, changes, blocks)

    return {
        "suggestions": suggestions,
        "source_context": source_context,
        "message": f"从 {len(blocks)} 个描述段落 + {len(conclusions)} 条讨论结论 + {len(changes)} 条变更记录中提取了 {len(suggestions)} 个任务草案",
    }


def _collect_source_data(db, idea_id):
    """收集描述、讨论结论、变更记录。"""
    i = db.get(Idea, idea_id)
    desc = (i.description or "").strip()
    discussions = db.exec(
        select(Discussion).where(Discussion.idea_id == idea_id)
    ).all()
    conclusions = [d.conclusions.strip() for d in discussions if d.conclusions]
    changes = db.exec(
        select(IdeaChange).where(IdeaChange.idea_id == idea_id).order_by(IdeaChange.created_at)
    ).all()
    return desc, conclusions, changes


def _split_description(text):
    """按空行或列表项拆分描述为独立段落。"""
    blocks = re.split(r'\n\s*\n|\n(?=-\s)', text)
    result = [b.strip() for b in blocks if len(b.strip()) > 8]
    return result or ([text] if text else [])


def _extract_conclusion_tasks(conclusions):
    """从讨论结论中提取补充任务。"""
    tasks = []
    for c in conclusions:
        for seg in re.split(r'[。；;]\s*', c):
            seg = seg.strip()
            if len(seg) > 8:
                tasks.append(seg)
    return tasks


def _extract_change_tasks(changes):
    """从变更记录中提取需求变化。"""
    tasks = []
    for ch in changes:
        if ch.diff:
            for field_name, change_desc in ch.diff.items():
                if isinstance(change_desc, str) and len(change_desc) > 5:
                    tasks.append(f"处理 {field_name} 变更：{change_desc}")
    return tasks


def _generate_suggestions(blocks, conclusion_tasks, change_tasks, max_tasks):
    """从所有来源合并生成草案。"""
    suggestions = []
    ref_counter = 0

    def _add(title, description, source, reasoning):
        nonlocal ref_counter
        if len(suggestions) >= max_tasks:
            return
        ref_counter += 1
        total_len = len(title) + len(description)
        est = 30 if total_len < 80 else (60 if total_len < 200 else 120)
        ac_lines = [
            line.strip().lstrip('- ')
            for line in description.split('\n')
            if any(kw in line for kw in ['需要', '必须', '验证', '确认', '检查', '验收', '应该'])
        ]
        suggestions.append({
            "ref": f"t{ref_counter}",
            "title": title,
            "description": description,
            "depends_on": [],
            "est_duration_min": est,
            "acceptance_criteria": "; ".join(ac_lines) if ac_lines else "",
            "reasoning": reasoning,
            "source": source,
        })

    for block in blocks:
        first_line = re.sub(r'^[#\-*>\s]+', '', block.split('\n')[0]).strip()
        if first_line and len(first_line) > 3:
            _add(first_line[:80], block, "description", "从描述段落提取")

    for ct in conclusion_tasks:
        title = re.sub(r'^[#\-*>\s]+', '', ct.split('\n')[0]).strip()[:80]
        _add(title, ct, "discussion", "从讨论结论提取")

    for ct in change_tasks:
        _add(ct[:80], ct, "change", "从变更记录提取")

    return suggestions


def _link_dependencies(suggestions):
    """线性依赖链：t1 → t2 → t3。"""
    for idx in range(1, len(suggestions)):
        suggestions[idx]["depends_on"] = [suggestions[idx - 1]["ref"]]


def _build_source_context(desc, conclusions, changes, blocks):
    """构建来源摘要。"""
    parts = []
    if desc:
        parts.append(f"描述: {desc[:200]}")
    if conclusions:
        parts.append(f"讨论结论({len(conclusions)}条): {'; '.join(c[:80] for c in conclusions[:3])}")
    if changes:
        parts.append(f"变更记录({len(changes)}条)")
    return "\n".join(parts)


# ==================== Enhanced Ideas Models ====================

class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    category: str
    icon: str
    fields: List[dict]
    tags: List[str]


class IdeaSearchRequest(BaseModel):
    query: Optional[str] = None
    status: Optional[List[str]] = None
    idea_type: Optional[str] = None
    category: Optional[str] = None
    project: Optional[str] = None
    labels: Optional[List[str]] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    min_score: Optional[float] = None
    sort_by: str = "updated_at"
    sort_order: str = "desc"
    page: int = 1
    page_size: int = 20


class IdeaScoreResponse(BaseModel):
    idea_id: str
    title: str
    score: float
    factors: Dict[str, float]
    recommendation: str


class BulkActionRequest(BaseModel):
    idea_ids: List[str]
    action: str  # archive, advance_status, add_labels, remove_labels, set_project
    payload: Dict[str, Any] = {}


class TemplateGenerateRequest(BaseModel):
    template_id: str
    values: Dict[str, str]
    num_ideas: int = 3
    sync_to_hub: bool = False


# ==================== Templates ====================

@router.get("/templates")
def list_templates(category: Optional[str] = None):
    """List available idea templates."""
    templates = get_templates_by_category(category) if category else DEFAULT_TEMPLATES
    return {
        "count": len(templates),
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "category": t.category,
                "icon": t.icon,
                "fields": t.fields,
                "tags": t.tags,
            }
            for t in templates
        ],
    }


@router.get("/templates/{template_id}")
def get_template(template_id: str):
    """Get a specific template by ID."""
    template = get_template_by_id(template_id)
    if not template:
        raise HTTPException(404, f"Template not found: {template_id}")

    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "category": template.category,
        "icon": template.icon,
        "fields": template.fields,
        "tags": template.tags,
    }


@router.post("/templates/generate")
def generate_from_template(request: TemplateGenerateRequest):
    """Generate ideas using a template with provided values."""
    template = get_template_by_id(request.template_id)
    if not template:
        raise HTTPException(404, f"Template not found: {request.template_id}")

    import subprocess, json, os

    NODE = r"C:\Users\admin\.workbuddy\binaries\node\versions\22.22.2\node.exe"
    MCP_SCRIPT = r"D:\node_global\node_modules\mio-agent-runtime\server\mio-intelligence-mcp\index.js"
    DATA_DIR = os.path.join(os.path.expanduser("~"), ".mio-intelligence")

    ENV = {
        **os.environ,
        "MIO_DATA_DIR": DATA_DIR,
        "MIO_CONTEXT": json.dumps({
            "agentId": "opencode",
            "project": "2026-08-22-12-13-49",
            "workspace": r"c:\Users\admin\WorkBuddy\2026-08-22-12-13-49",
            "sessionId": "opencode-session",
        }),
    }

    def call_mcp_tool(tool_name: str, arguments: dict) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        proc = subprocess.run(
            [NODE, MCP_SCRIPT],
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=ENV,
            timeout=15,
        )
        if proc.returncode != 0:
            raise HTTPException(500, f"MCP error: {proc.stderr[:500]}")
        lines = proc.stdout.strip().split("\n")
        resp = json.loads(lines[0])
        text = resp["result"]["content"][0]["text"]
        return json.loads(text, strict=False)

    from mio_taskhub.idea_prompts import render_template_prompt

    context = render_template_prompt(template=DEFAULT_TEMPLATES[0], values=request.values)
    context_parts = []
    for field in get_template_by_id(request.template_id).fields:
        key = field["key"]
        if key in request.values and request.values[key]:
            label = field["label"]
            context_parts.append(f"{label}：{request.values[key]}")
    context = "\n\n".join(context_parts)

    goal = request.values.get("title", "生成想法")

    try:
        result = call_mcp_tool("mio.idea.generate", {
            "goal": goal,
            "context": context,
            "constraints": ["不引入外部依赖", "保持本机单用户", "复用现有 MCP 工具"],
            "numIdeas": request.num_ideas,
        })
    except Exception as e:
        raise HTTPException(500, f"MCP generation failed: {e}")

    ideas = result.get("ideas", [])

    created = 0
    synced_ids = []
    if request.sync_to_hub:
        db = next(get_session())
        try:
            existing = db.exec(select(Idea.title)).all()
            existing_titles = {t[0].strip().lower() for t in existing}

            for idea in ideas:
                title = idea.get("title", "").strip()
                if not title or title.lower() in existing_titles:
                    continue
                strategy = idea.get("provenance", {}).get("strategy", "")
                new_idea = Idea(
                    title=title[:200],
                    description=idea.get("description", ""),
                    status="new",
                    labels=["mio-intelligence", "auto-generated"] + ([f"strategy:{strategy}"] if strategy else []),
                )
                db.add(new_idea)
                db.commit()
                db.refresh(new_idea)
                existing_titles.add(title.lower())
                synced_ids.append(new_idea.id)
                created += 1
        finally:
            db.close()

    return {
        "generated": len(ideas),
        "synced": created,
        "synced_ids": synced_ids,
        "ideas": ideas,
    }


# ==================== Search ====================

@router.post("/search")
def search_ideas(request: IdeaSearchRequest, db: Session = Depends(get_session)):
    """Full-text search for ideas with advanced filters."""
    q = select(Idea)

    if request.query:
        query = request.query.strip()
        if query:
            q = q.where(
                or_(
                    Idea.title.contains(query),
                    Idea.description.contains(query),
                )
            )

    if request.status:
        try:
            statuses = [IdeaStatus(s) for s in request.status]
            q = q.where(Idea.status.in_(statuses))
        except ValueError:
            raise HTTPException(400, "invalid status")

    if request.idea_type:
        try:
            q = q.where(Idea.idea_type == IdeaType(request.idea_type))
        except ValueError:
            raise HTTPException(400, "invalid idea_type")

    if request.project:
        q = q.where(Idea.project == request.project)

    if request.labels:
        for label in request.labels:
            q = q.where(Idea.labels.contains([label]))

    if request.date_from:
        try:
            dt = datetime.fromisoformat(request.date_from)
            q = q.where(Idea.updated_at >= dt)
        except ValueError:
            pass

    if request.date_to:
        try:
            dt = datetime.fromisoformat(request.date_to)
            q = q.where(Idea.updated_at <= dt)
        except ValueError:
            pass

    sort_col = getattr(Idea, request.sort_by, Idea.updated_at)
    if request.sort_order == "desc":
        q = q.order_by(sort_col.desc())
    else:
        q = q.order_by(sort_col.asc())

    page = max(1, request.page)
    page_size = min(max(1, request.page_size), 100)
    offset = (page - 1) * page_size

    total = db.exec(select(func.count()).select_from(q.subquery())).one()
    rows = db.exec(q.offset(offset).limit(page_size)).all()

    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "ideas": [_idea_json(i) for i in rows],
    }


# ==================== Scoring ====================

def _calculate_idea_score(idea: Idea, db: Session) -> Dict[str, Any]:
    """Calculate priority score for an idea (RICE-like)."""
    factors = {}

    reach = 1.0
    if idea.project:
        reach += 0.5
    if idea.labels:
        reach += len(idea.labels) * 0.1
    factors["reach"] = min(reach, 5.0)

    impact = 1.0
    if idea.status in [IdeaStatus.FORMED, IdeaStatus.BROKEN_DOWN]:
        impact += 1.0
    if idea.idea_type == "adr":
        impact += 0.5
    if any(l in (idea.labels or []) for l in ["P0", "critical", "blocker"]):
        impact += 1.0
    factors["impact"] = min(impact, 5.0)

    confidence = 1.0
    if idea.description and len(idea.description) > 100:
        confidence += 0.5
    if idea.review_count > 0:
        confidence += idea.review_count * 0.3
    if idea.discussions:
        msg_count = sum(len(d.messages or []) for d in idea.discussions)
        confidence += min(msg_count * 0.1, 1.0)
    factors["confidence"] = min(confidence, 5.0)

    effort = 3.0
    tasks = db.exec(select(Task).where(Task.idea_id == idea.id)).all()
    if tasks:
        total_est = sum(t.est_duration_min or 30 for t in tasks)
        if total_est <= 60:
            effort = 5.0
        elif total_est <= 180:
            effort = 4.0
        elif total_est <= 480:
            effort = 3.0
        elif total_est <= 1440:
            effort = 2.0
        else:
            effort = 1.0
    else:
        desc_len = len(idea.description or "")
        if desc_len > 500:
            effort = 2.0
        elif desc_len > 200:
            effort = 3.0
        else:
            effort = 4.0
    factors["effort"] = effort

    rice = (factors["reach"] * factors["impact"] * factors["confidence"]) / factors["effort"]

    if rice >= 8:
        recommendation = "high-priority"
    elif rice >= 4:
        recommendation = "medium-priority"
    elif rice >= 2:
        recommendation = "low-priority"
    else:
        recommendation = "backlog"

    return {
        "idea_id": idea.id,
        "title": idea.title,
        "score": round(rice, 2),
        "factors": {k: round(v, 2) for k, v in factors.items()},
        "recommendation": recommendation,
    }


@router.get("/{idea_id}/score", response_model=IdeaScoreResponse)
def get_idea_score(idea_id: str, db: Session = Depends(get_session)):
    """Calculate priority score for an idea."""
    idea = db.get(Idea, idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")

    return _calculate_idea_score(idea, db)


@router.get("/scores")
def get_all_scores(
    status: Optional[str] = None,
    idea_type: Optional[str] = None,
    min_score: float = 0,
    db: Session = Depends(get_session)
):
    """Get scores for all ideas (or filtered)."""
    q = select(Idea)
    if status:
        try:
            q = q.where(Idea.status == IdeaStatus(status))
        except ValueError:
            raise HTTPException(400, "invalid status")
    if idea_type:
        try:
            q = q.where(Idea.idea_type == IdeaType(idea_type))
        except ValueError:
            raise HTTPException(400, "invalid idea_type")

    ideas = db.exec(q).all()
    scores = [_calculate_idea_score(i, db) for i in ideas]
    scores = [s for s in scores if s["score"] >= min_score]
    scores.sort(key=lambda x: x["score"], reverse=True)

    return {
        "count": len(scores),
        "scores": scores,
    }


# ==================== Bulk Operations ====================

@router.post("/bulk")
def bulk_action(request: BulkActionRequest, db: Session = Depends(get_session)):
    """Perform bulk actions on multiple ideas."""
    if not request.idea_ids:
        raise HTTPException(422, "idea_ids is required")

    ideas = db.exec(select(Idea).where(Idea.id.in_(request.idea_ids))).all()
    if not ideas:
        raise HTTPException(404, "no ideas found")

    results = {"success": [], "failed": []}

    for idea in ideas:
        try:
            if request.action == "archive":
                transition_idea_status(idea, IdeaStatus.ARCHIVED, db, actor="bulk", source="bulk_archive")

            elif request.action == "advance_status":
                current = idea.status
                next_map = {
                    IdeaStatus.NEW: IdeaStatus.FERMENTING,
                    IdeaStatus.FERMENTING: IdeaStatus.FORMED,
                    IdeaStatus.FORMED: IdeaStatus.BROKEN_DOWN,
                }
                if current in next_map:
                    transition_idea_status(idea, next_map[current], db, actor="bulk", source="bulk_advance")
                else:
                    raise HTTPException(422, f"cannot advance from {current.value}")

            elif request.action == "add_labels":
                labels = request.payload.get("labels", [])
                for label in labels:
                    if label not in (idea.labels or []):
                        idea.labels = (idea.labels or []) + [label]

            elif request.action == "remove_labels":
                labels = request.payload.get("labels", [])
                idea.labels = [l for l in (idea.labels or []) if l not in labels]

            elif request.action == "set_project":
                project = request.payload.get("project", "")
                idea.project = project

            else:
                raise HTTPException(400, f"unknown action: {request.action}")

            idea.updated_at = _now()
            db.add(idea)
            results["success"].append(idea.id)

        except Exception as e:
            results["failed"].append({"id": idea.id, "error": str(e)})

    db.commit()
    return results


# ==================== Statistics ====================

@router.get("/stats/summary")
def get_ideas_summary(db: Session = Depends(get_session)):
    """Get summary statistics for ideas."""
    total = db.exec(select(func.count(Idea.id))).one()

    by_status = {}
    for status in IdeaStatus:
        count = db.exec(
            select(func.count(Idea.id)).where(Idea.status == status)
        ).one()
        by_status[status.value] = count

    by_type = {}
    for itype in IdeaType:
        count = db.exec(
            select(func.count(Idea.id)).where(Idea.idea_type == itype)
        ).one()
        by_type[itype.value] = count

    by_project = db.exec(
        select(Idea.project, func.count(Idea.id))
        .where(Idea.project != "")
        .group_by(Idea.project)
    ).all()
    by_project = {p: c for p, c in by_project}

    week_ago = datetime.now() - timedelta(days=7)
    recent = db.exec(
        select(func.count(Idea.id)).where(Idea.updated_at >= week_ago)
    ).one()

    all_ideas = db.exec(select(Idea)).all()
    scores = [_calculate_idea_score(i, db) for i in all_ideas]
    avg_score = sum(s["score"] for s in scores) / len(scores) if scores else 0

    return {
        "total": total,
        "by_status": by_status,
        "by_type": by_type,
        "by_project": by_project,
        "recent_week": recent,
        "avg_score": round(avg_score, 2),
    }


# ==================== Related Ideas ====================

@router.get("/{idea_id}/related")
def get_related_ideas(idea_id: str, limit: int = 5, db: Session = Depends(get_session)):
    """Find related ideas based on title similarity, shared labels, project."""
    idea = db.get(Idea, idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")

    related = []

    if idea.project:
        same_project = db.exec(
            select(Idea)
            .where(Idea.project == idea.project, Idea.id != idea_id)
            .limit(limit)
        ).all()
        for r in same_project:
            related.append({"idea": _idea_json(r), "reason": f"同项目: {idea.project}", "score": 0.8})

    if idea.labels:
        for label in idea.labels:
            labeled = db.exec(
                select(Idea)
                .where(Idea.labels.contains([label]), Idea.id != idea_id)
                .limit(limit)
            ).all()
            for r in labeled:
                if not any(x["idea"]["id"] == r.id for x in related):
                    related.append({"idea": _idea_json(r), "reason": f"共同标签: {label}", "score": 0.6})

    title_tokens = set((idea.title or "").lower().split())
    if title_tokens:
        all_others = db.exec(select(Idea).where(Idea.id != idea_id)).all()
        for r in all_others:
            if r.id in [x["idea"]["id"] for x in related]:
                continue
            other_tokens = set((r.title or "").lower().split())
            overlap = len(title_tokens & other_tokens)
            if overlap >= 2:
                related.append({"idea": _idea_json(r), "reason": f"标题关键词重叠 ({overlap}个)", "score": 0.4})

    related.sort(key=lambda x: x["score"], reverse=True)
    return {"related": related[:limit]}


# ==================== Export ====================

@router.get("/export")
def export_ideas(
    format: str = "json",
    status: Optional[str] = None,
    idea_type: Optional[str] = None,
    db: Session = Depends(get_session)
):
    """Export ideas as JSON or Markdown."""
    q = select(Idea)
    if status:
        try:
            q = q.where(Idea.status == IdeaStatus(status))
        except ValueError:
            raise HTTPException(400, "invalid status")
    if idea_type:
        try:
            q = q.where(Idea.idea_type == IdeaType(idea_type))
        except ValueError:
            raise HTTPException(400, "invalid idea_type")

    ideas = db.exec(q.order_by(Idea.updated_at.desc())).all()

    if format == "json":
        return {"ideas": [_idea_json(i) for i in ideas]}

    elif format == "markdown":
        lines = ["# Ideas Export", f"Generated: {datetime.now().isoformat()}", ""]
        for i in ideas:
            lines.append(f"## {i.title}")
            lines.append(f"**ID:** {i.id}  \n**Status:** {i.status.value}  \n**Type:** {i.idea_type.value}  \n**Project:** {i.project or '—'}  \n**Labels:** {', '.join(i.labels) if i.labels else '—'}  \n**Updated:** {i.updated_at.isoformat()}")
            lines.append("")
            lines.append(i.description or "_No description_")
            lines.append("")
            lines.append("---")
            lines.append("")
        return {"content": "\n".join(lines), "format": "markdown"}

    else:
        raise HTTPException(400, "format must be 'json' or 'markdown'")


# ==================== Health Check ====================

@router.get("/health")
def ideas_health():
    return {"status": "ok", "features": ["templates", "search", "scoring", "bulk", "export", "related"]}