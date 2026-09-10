from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from mio_taskhub.db import get_session
from mio_taskhub.models import (Idea, IdeaChange, IdeaStatus, IdeaType,
                                IdeaHistory, ChangeType, OutboxEvent)
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event
from mio_taskhub.api.ideas import _idea_json, _get_next_adr_number

router = APIRouter(prefix="/ideas", tags=["ideas"])


@router.post("/{idea_id}/evolve-to-adr")
def evolve_to_adr(idea_id: str, body: dict, db: Session = Depends(get_session)):
    """将 Idea 演化为 ADR（幂等/并发安全）"""
    i = db.get(Idea, idea_id, with_for_update=True)
    if not i:
        raise HTTPException(404, "idea not found")
    if i.idea_type == IdeaType.ADR:
        raise HTTPException(409, "IDEA_ALREADY_ADR")
    if i.status != IdeaStatus.FORMED:
        raise HTTPException(422, f"can only evolve from 'formed', current status: {i.status.value}")

    old_type = i.idea_type.value
    old_status = i.status.value

    i.idea_type = IdeaType.ADR
    i.adr_status = IdeaStatus.PROPOSED
    i.status = IdeaStatus.PROPOSED
    i.adr_number = _get_next_adr_number(db)
    _fill_madr_fields(i, body)
    i.version += 1
    i.updated_at = _now()

    _record_evolve_history(db, idea_id, i, old_type, old_status, body)
    _record_evolve_outbox(db, idea_id, i)
    event = emit_event(db, type="idea_evolved_to_adr", entity="idea", entity_id=idea_id,
                       payload={"old_type": old_type, "new_type": i.idea_type.value})

    db.add(i)
    db.commit()
    db.refresh(i)
    return _idea_json(i)


def _fill_madr_fields(i, body):
    """填充 MADR 字段。"""
    for field in ("madr_context", "madr_decision", "madr_consequences", "madr_alternatives"):
        if field in body:
            setattr(i, field, body[field])


def _record_evolve_history(db, idea_id, i, old_type, old_status, body):
    db.add(IdeaChange(
        idea_id=idea_id, version=i.version,
        diff={"idea_type": {"old": old_type, "new": i.idea_type.value},
              "adr_status": {"old": old_status, "new": i.adr_status.value},
              "adr_number": {"old": None, "new": i.adr_number}},
        reason=body.get("reason", ""),
        change_type=ChangeType.TYPE_EVOLUTION,
    ))
    db.add(IdeaHistory(
        idea_id=idea_id, kind="status", actor="user",
        content=f"{old_type} -> {i.idea_type.value}",
        reasoning=body.get("reason", ""),
        extra={"from": old_type, "to": i.idea_type.value, "source": "evolve_to_adr"},
    ))


def _record_evolve_outbox(db, idea_id, i):
    db.add(OutboxEvent(
        event_type="evolve-to-adr", aggregate_type="idea", aggregate_id=idea_id,
        payload={"adr_number": i.adr_number, "title": i.title,
                 "adr_status": i.adr_status.value,
                 "madr_context": i.madr_context, "madr_decision": i.madr_decision,
                 "madr_consequences": i.madr_consequences, "madr_alternatives": i.madr_alternatives},
    ))


@router.post("/{idea_id}/adr-action")
def adr_action(idea_id: str, body: dict, db: Session = Depends(get_session)):
    """ADR 状态操作：accept/reject/deprecate/supersede"""
    i = db.get(Idea, idea_id, with_for_update=True)
    if not i:
        raise HTTPException(404, "idea not found")
    if i.idea_type != IdeaType.ADR:
        raise HTTPException(422, "idea is not an ADR")

    action = body.get("action", "").strip()
    if not action:
        raise HTTPException(422, "action is required")

    old_adr_status = i.adr_status
    _apply_adr_action(i, action, body, db)
    i.version += 1
    i.updated_at = _now()

    _record_action_history(db, idea_id, i, action, old_adr_status, body)
    _record_action_outbox(db, idea_id, i, action)
    event = emit_event(db, type="idea_adr_action", entity="idea", entity_id=idea_id,
                       payload={"action": action, "adr_status": i.adr_status.value})

    db.add(i)
    db.commit()
    db.refresh(i)
    return _idea_json(i)


def _apply_adr_action(i, action, body, db):
    """根据 action 校验并执行状态变更。"""
    if action == "accept":
        if i.adr_status != IdeaStatus.PROPOSED:
            raise HTTPException(422, f"can only accept from 'proposed', current: {i.adr_status.value}")
        i.adr_status = IdeaStatus.ACCEPTED
        i.status = IdeaStatus.ACCEPTED
    elif action == "reject":
        if i.adr_status != IdeaStatus.PROPOSED:
            raise HTTPException(422, f"can only reject from 'proposed', current: {i.adr_status.value}")
        i.adr_status = IdeaStatus.REJECTED
        i.status = IdeaStatus.REJECTED
    elif action == "deprecate":
        if i.adr_status != IdeaStatus.ACCEPTED:
            raise HTTPException(422, f"can only deprecate from 'accepted', current: {i.adr_status.value}")
        i.adr_status = IdeaStatus.DEPRECATED
        i.status = IdeaStatus.DEPRECATED
    elif action == "supersede":
        _apply_supersede(i, body, db)
    else:
        raise HTTPException(400, f"invalid action: {action}")


def _apply_supersede(i, body, db):
    """执行 supersede 操作。"""
    if i.adr_status != IdeaStatus.ACCEPTED:
        raise HTTPException(422, f"can only supersede from 'accepted', current: {i.adr_status.value}")
    replacement_id = body.get("replacement_id", "").strip()
    if not replacement_id:
        raise HTTPException(422, "replacement_id is required for supersede")
    replacement = db.get(Idea, replacement_id)
    if not replacement:
        raise HTTPException(404, f"replacement idea not found: {replacement_id}")
    if replacement.idea_type != IdeaType.ADR:
        raise HTTPException(422, "replacement must be an ADR")
    if replacement.adr_status != IdeaStatus.ACCEPTED:
        raise HTTPException(422, "replacement must be in 'accepted' status")
    if replacement.id == i.id:
        raise HTTPException(422, "cannot supersede itself")
    i.adr_status = IdeaStatus.SUPERSEDED
    i.status = IdeaStatus.SUPERSEDED
    i.superseded_by = replacement_id


def _record_action_history(db, idea_id, i, action, old_adr_status, body):
    diff = {"adr_status": {"old": old_adr_status.value, "new": i.adr_status.value}}
    if action == "supersede":
        diff["superseded_by"] = {"old": None, "new": i.superseded_by}
    db.add(IdeaChange(
        idea_id=idea_id, version=i.version, diff=diff,
        reason=body.get("reason", ""),
        change_type=ChangeType.ADR_ACTION,
    ))
    db.add(IdeaHistory(
        idea_id=idea_id, kind="status", actor="user",
        content=f"{old_adr_status.value} -> {i.adr_status.value}",
        reasoning=body.get("reason", ""),
        extra={"action": action, "from": old_adr_status.value, "to": i.adr_status.value},
    ))


def _record_action_outbox(db, idea_id, i, action):
    payload = {
        "adr_number": i.adr_number, "title": i.title,
        "action": action, "adr_status": i.adr_status.value,
    }
    if action == "supersede":
        replacement = db.get(Idea, i.superseded_by)
        payload["superseded_by"] = i.superseded_by
        payload["superseded_by_number"] = replacement.adr_number if replacement else None
    db.add(OutboxEvent(
        event_type=action, aggregate_type="idea", aggregate_id=idea_id, payload=payload,
    ))


@router.get("/{idea_id}/adr-md")
def get_adr_markdown(idea_id: str, db: Session = Depends(get_session)):
    """查看 ADR 原始 Markdown：优先读落盘文件，缺失时即时渲染兜底。"""
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    if i.idea_type != IdeaType.ADR:
        raise HTTPException(422, "idea is not an ADR")

    path = i.adr_file_path or ""
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return {"path": path, "content": f.read(), "source": "file"}
        except OSError:
            pass  # 文件缺失则即时渲染

    from mio_taskhub.git_sync import _render_adr_markdown
    return {"path": "", "content": _render_adr_markdown(i), "source": "inline"}
