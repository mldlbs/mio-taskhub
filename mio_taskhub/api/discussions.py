from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Discussion, DiscussionMessage, Idea, Task, TaskStage, IdeaHistory
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event
from mio_taskhub import role_prompts as rp

router = APIRouter(prefix="/discussions", tags=["discussions"])

_MODES = ("free", "review")
_ITEM_STATUS = ("pending", "doing", "done")


def _msg_json(m: DiscussionMessage) -> dict:
    return {"author": m.author, "role": m.role, "content": m.content, "at": m.at.isoformat()}


def _disc_full(d: Discussion, db: Session) -> dict:
    msgs = db.exec(select(DiscussionMessage).where(DiscussionMessage.discussion_id == d.id).order_by(DiscussionMessage.at)).all()
    return {
        "id": d.id, "task_id": d.task_id, "idea_id": d.idea_id, "topic": d.topic,
        "agent": d.agent, "status": d.status, "summary": d.summary, "conclusions": d.conclusions,
        "stage": d.stage, "started_at": d.started_at.isoformat(),
        "ended_at": d.ended_at.isoformat() if d.ended_at else None,
        # P2（FR-18）：只增键不改既有键
        "mode": d.mode or "free",
        "roles": d.roles if isinstance(d.roles, list) else [],
        "review": d.review if isinstance(d.review, dict) else None,
        "prompt_snapshot": d.prompt_snapshot if isinstance(d.prompt_snapshot, dict) else None,
        "messages": [_msg_json(m) for m in msgs],
    }


def _validate_mode_roles(mode, roles) -> tuple:
    """FR-18：mode 合法性 + review 必填 roles。"""
    mode = (mode or "free") if isinstance(mode, str) else mode
    if mode not in _MODES:
        raise HTTPException(422, f"mode must be one of {'/'.join(_MODES)}")
    if roles is None:
        roles = []
    if not isinstance(roles, list) or any(not isinstance(r, str) or not r.strip() for r in roles):
        raise HTTPException(422, "roles must be a list of non-empty strings")
    roles = [r.strip() for r in roles]
    if mode == "review" and not roles:
        raise HTTPException(422, "roles is required when mode=review")
    return mode, roles


def _validate_review(review) -> dict:
    """FR-21：评审关闭五段结构门控（只验结构与条目数，不假装验质量）。"""
    if not isinstance(review, dict):
        raise HTTPException(422, "review is required when mode=review (missing review payload)")
    errors = []
    risks = review.get("risks")
    if not isinstance(risks, list) or not [r for r in risks if str(r).strip()]:
        errors.append("risks：风险清单至少 1 条")
    div = review.get("divergences")
    if not isinstance(div, str) or not div.strip():
        errors.append("divergences：分歧点缺段（「无分歧」须写达成一致依据）")
    sug = review.get("suggestions")
    if not isinstance(sug, str) or not sug.strip():
        errors.append("suggestions：建议缺段")
    dec = review.get("decisions")
    if not isinstance(dec, list) or len([d for d in dec if str(d).strip()]) < 2:
        errors.append("decisions：决策选项至少 2 个")
    items = review.get("action_items")
    if not isinstance(items, list) or not items:
        errors.append("action_items：行动项至少 1 条")
        items = []
    seen_ids = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            errors.append(f"action_items[{i}]：必须是对象")
            continue
        iid = it.get("id")
        if not isinstance(iid, str) or not iid.strip():
            errors.append(f"action_items[{i}]：缺少 id")
        elif iid in seen_ids:
            errors.append(f"action_items[{i}]：id 重复（{iid}）")
        else:
            seen_ids.add(iid)
        if not isinstance(it.get("owner"), str) or not it.get("owner", "").strip():
            errors.append(f"action_items[{i}]：缺少 owner")
        if not isinstance(it.get("action"), str) or not it.get("action", "").strip():
            errors.append(f"action_items[{i}]：缺少 action")
        due = it.get("due")
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except (TypeError, ValueError):
            errors.append(f"action_items[{i}]：due 必须为 YYYY-MM-DD")
        if it.get("status") not in _ITEM_STATUS:
            errors.append(f"action_items[{i}]：status 必须为 {'/'.join(_ITEM_STATUS)}")
        tid = it.get("task_id")
        if tid is not None and not isinstance(tid, str):
            errors.append(f"action_items[{i}]：task_id 必须为字符串或 null")
    if errors:
        raise HTTPException(422, "review gate: " + "；".join(errors))
    # 归一化落库结构
    normalized = {
        "risks": [str(r) for r in risks],
        "divergences": div.strip(),
        "suggestions": sug.strip(),
        "decisions": [str(d) for d in dec],
        "action_items": [{
            "id": str(it["id"]).strip(),
            "owner": str(it["owner"]).strip(),
            "action": str(it["action"]).strip(),
            "due": it["due"],
            "status": it["status"],
            "task_id": it.get("task_id"),
        } for it in items],
    }
    return normalized


@router.post("", response_model=dict)
def create_discussion(body: dict, db: Session = Depends(get_session)):
    idea_id = body.get("idea_id", "") or ""
    task_id = body.get("task_id", "") or ""
    if not (idea_id or task_id):
        raise HTTPException(422, "idea_id or task_id is required")
    if idea_id and not db.get(Idea, idea_id):
        raise HTTPException(404, "idea not found")
    if task_id and not db.get(Task, task_id):
        raise HTTPException(404, "task not found")
    # FR-18：双模式校验；FR-19：review 创建时快照 roles+prompt 版本
    mode, roles = _validate_mode_roles(body.get("mode", "free"), body.get("roles"))
    conclusions = body.get("conclusions", "")
    status = "closed" if conclusions else "open"
    snapshot = rp.snapshot_for(db, roles) if mode == "review" else None
    review = None
    if mode == "review" and conclusions:
        # 创建即关闭也走同一门控，避免绕过（FR-21 精神）
        review = _validate_review(body.get("review"))
    d = Discussion(
        task_id=task_id, idea_id=idea_id,
        topic=body.get("topic", ""), agent=body.get("agent", ""),
        status=status, summary=body.get("summary", ""), conclusions=conclusions,
        stage=body.get("stage", "brainstorming"),
        mode=mode, roles=roles, review=review, prompt_snapshot=snapshot,
        ended_at=_now() if status == "closed" else None,
    )
    db.add(d); db.commit(); db.refresh(d)
    for m in body.get("messages", []):
        db.add(DiscussionMessage(discussion_id=d.id, author=m.get("author", ""),
                                 role=m.get("role", "user"), content=m.get("content", "")))
    event = emit_event(db, type="discussion_created", entity="discussion",
                       entity_id=d.id, payload={"idea_id": idea_id, "task_id": task_id, "mode": mode})
    db.commit()
    db.refresh(d)
    return _disc_full(d, db)


@router.get("")
def list_discussions(ref_type: str = None, ref_id: str = "", db: Session = Depends(get_session)):
    if ref_type == "idea":
        q = select(Discussion).where(Discussion.idea_id == ref_id)
    elif ref_type == "task":
        q = select(Discussion).where(Discussion.task_id == ref_id)
    else:
        q = select(Discussion)
    rows = db.exec(q.order_by(Discussion.started_at.desc())).all()
    return {"count": len(rows), "discussions": [_disc_full(d, db) for d in rows]}


@router.get("/{discussion_id}")
def get_discussion(discussion_id: str, db: Session = Depends(get_session)):
    d = db.get(Discussion, discussion_id)
    if not d:
        raise HTTPException(404, "discussion not found")
    return _disc_full(d, db)


@router.post("/{discussion_id}/messages")
def add_message(discussion_id: str, body: dict, db: Session = Depends(get_session)):
    d = db.get(Discussion, discussion_id)
    if not d:
        raise HTTPException(404, "discussion not found")
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(422, "content is required")
    m = DiscussionMessage(discussion_id=discussion_id,
                          author=body.get("author", ""),
                          role=body.get("role", "user"),
                          content=content)
    db.add(m)
    event = emit_event(db, type="discussion_message", entity="discussion",
                       entity_id=discussion_id, payload={"role": body.get("role", "user")})
    db.commit()
    db.refresh(m)
    return _msg_json(m)


@router.post("/{discussion_id}/close")
def close_discussion(discussion_id: str, body: dict, db: Session = Depends(get_session)):
    d = db.get(Discussion, discussion_id)
    if not d:
        raise HTTPException(404, "discussion not found")
    review = None
    if (d.mode or "free") == "review":
        # FR-21：评审关闭五段门控，违规 422；达标落库。free 分支完全不动。
        review = _validate_review(body.get("review"))
    d.summary = body.get("summary", d.summary)
    d.conclusions = body.get("conclusions", d.conclusions)
    if review is not None:
        d.review = review
    d.status = "closed"
    d.ended_at = _now()
    # 关联 idea 时写 kind=discussion 轨迹
    if d.idea_id:
        db.add(IdeaHistory(
            idea_id=d.idea_id,
            kind="discussion",
            actor=body.get("actor", "") or "user",
            content=f"讨论《{d.topic}》已关闭",
            reasoning=d.conclusions or None,
            extra={"discussion_id": discussion_id, "conclusions": d.conclusions, "topic": d.topic},
        ))
    event = emit_event(db, type="discussion_closed", entity="discussion",
                       entity_id=discussion_id, payload={"conclusions": d.conclusions})
    db.add(d); db.commit(); db.refresh(d)
    return _disc_full(d, db)


@router.post("/{discussion_id}/convert")
def convert_action_items(discussion_id: str, body: dict, db: Session = Depends(get_session)):
    """FR-22：行动项幂等转任务。缺省转全部未转条目；已转条目跳过；单事务整体回滚。"""
    d = db.get(Discussion, discussion_id)
    if not d:
        raise HTTPException(404, "discussion not found")
    review = d.review if isinstance(d.review, dict) else None
    items = (review or {}).get("action_items") or []
    if not items:
        raise HTTPException(422, "no action_items to convert (close a mode=review discussion first)")
    item_ids = body.get("item_ids")
    if item_ids is not None:
        if not isinstance(item_ids, list) or any(not isinstance(x, str) for x in item_ids):
            raise HTTPException(422, "item_ids must be a list of strings")
        known = {it.get("id") for it in items}
        unknown = [x for x in item_ids if x not in known]
        if unknown:
            raise HTTPException(422, f"unknown action item ids: {', '.join(unknown)}")
        by_id = {it.get("id"): it for it in items}
        selected = [by_id[x] for x in item_ids]
    else:
        selected = list(items)

    results = []
    converted = []
    try:
        for it in selected:
            if it.get("task_id"):
                # 幂等：已转条目返回原任务，不重复创建
                results.append({"item_id": it.get("id"), "task_id": it["task_id"], "created": False})
                continue
            due = None
            try:
                due = datetime.strptime(it["due"], "%Y-%m-%d")
            except (KeyError, TypeError, ValueError):
                pass  # close 门控已保格式；此处兜底不阻塞
            t = Task(
                title=str(it.get("action", "")).strip()[:200] or "行动项",
                description=f"行动项 {it.get('id', '')}｜负责人 {it.get('owner', '')}｜截止 {it.get('due', '')}"
                            f"\n来源讨论：{d.topic}（{d.id}）",
                acceptance_criteria=str(it.get("action", "")).strip(),   # 设计验收：含验收标准
                due_at=due,
                idea_id=d.idea_id or "",
                stage=TaskStage.READY,                                    # 可被领取
            )
            db.add(t)
            db.flush()                                                   # 取 id，失败则整体回滚
            new_items = []
            for orig in items:
                if orig.get("id") == it.get("id"):
                    updated = dict(orig)
                    updated["task_id"] = t.id
                    new_items.append(updated)
                else:
                    new_items.append(orig)
            review = dict(review)
            review["action_items"] = new_items
            items = new_items
            results.append({"item_id": it.get("id"), "task_id": t.id, "created": True})
            converted.append(t.id)
        if converted:
            d.review = review                                             # 重新赋值触发 JSON 列变更检测
            db.add(d)
            emit_event(db, type="action_items_converted", entity="discussion",
                       entity_id=d.id, payload={"task_ids": converted, "count": len(converted)})
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(d)
    return {"discussion_id": d.id, "results": results, "review": d.review}
