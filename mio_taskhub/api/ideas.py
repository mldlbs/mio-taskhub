import threading
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from mio_taskhub import mio_runtime
from mio_taskhub.db import get_session
from mio_taskhub.models import (Idea, IdeaChange, IdeaStatus, IdeaType, Task,
                                IdeaHistory, TaskKind, TaskStage, TaskState)
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event


router = APIRouter(prefix="/ideas", tags=["ideas"])

# 想法落地闭环 P0（FR-1/FR-2）+ P1（FR-11 hypotheses 引用）：结构化字段白名单
IDEA_STR_FIELDS = ("goal", "success_metric", "constraints", "out_of_scope", "mvp_scope")
IDEA_JSON_FIELDS = ("assumptions", "risks", "tags", "hypotheses")
IDEA_NEW_FIELDS = IDEA_STR_FIELDS + IDEA_JSON_FIELDS


def _validate_json_field(field: str, value):
    """JSON 白名单字段统一校验（FR-11：hypotheses 必须是字符串 id 列表）。"""
    if not isinstance(value, list):
        raise HTTPException(422, f"{field} must be a list")
    if field == "hypotheses" and any(not isinstance(x, str) for x in value):
        raise HTTPException(422, "hypotheses must be a list of strings")
    return value


def _collect_new_fields(body: dict) -> dict:
    """从请求体收集 P0/P1 新字段并校验类型（strings → str，JSON 字段必须是 list）。"""
    out = {}
    for f in IDEA_STR_FIELDS:
        if f in body and body[f] is not None:
            out[f] = str(body[f])
    for f in IDEA_JSON_FIELDS:
        if f in body and body[f] is not None:
            out[f] = _validate_json_field(f, body[f])
    return out


def _normalized(field: str, value):
    """NULL（旧数据）按空值参与比较与输出，保证全页面无异常。"""
    if field in IDEA_JSON_FIELDS:
        return value if isinstance(value, list) else []
    return value or ""


_ASSUMPTION_KEY_PREFIX = "assumptions["


def _apply_assumption_entry_diff(i: Idea, body: dict, diff: dict) -> None:
    """支持 `assumptions[hid]` 单条写回（FR-2）：只改一条不整表覆盖，diff 键即原文。

    值为 dict 时按字段合并到该条；为 null 时删除该条；匹配 hid/id。
    """
    for key in list(body.keys()):
        if not key.startswith(_ASSUMPTION_KEY_PREFIX) or not key.endswith("]"):
            continue
        hid = key[len(_ASSUMPTION_KEY_PREFIX):-1]
        if not hid:
            continue
        val = body[key]
        cur = list(i.assumptions) if isinstance(i.assumptions, list) else []
        idx = -1
        old = None
        for n, e in enumerate(cur):
            if isinstance(e, dict) and (e.get("hid") == hid or e.get("id") == hid):
                idx, old = n, e
                break
        if val is None:
            if idx < 0:
                continue
            new_entry = None
            cur.pop(idx)
        elif idx < 0:
            new_entry = dict(val) if isinstance(val, dict) else {"text": val}
            new_entry.setdefault("hid", hid)
            cur.append(new_entry)
        else:
            if isinstance(val, dict):
                new_entry = dict(old)
                new_entry.update(val)
            else:
                new_entry = dict(old)
                new_entry["text"] = val
            cur[idx] = new_entry
        diff[key] = {"old": old, "new": new_entry}
        i.assumptions = cur


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
        "goal": _normalized("goal", i.goal),
        "success_metric": _normalized("success_metric", i.success_metric),
        "constraints": _normalized("constraints", i.constraints),
        "out_of_scope": _normalized("out_of_scope", i.out_of_scope),
        "assumptions": _normalized("assumptions", i.assumptions),
        "risks": _normalized("risks", i.risks),
        "mvp_scope": _normalized("mvp_scope", i.mvp_scope),
        "tags": _normalized("tags", i.tags),
        "hypotheses": _normalized("hypotheses", i.hypotheses),
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
        **_collect_new_fields(body),
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
    for f in ("title", "description", "project", "labels") + IDEA_NEW_FIELDS:
        if f in body and body[f] is not None:
            if f in IDEA_STR_FIELDS and not isinstance(body[f], str):
                body[f] = str(body[f])
            if f in IDEA_JSON_FIELDS:
                _validate_json_field(f, body[f])
            old = getattr(i, f)
            if _normalized(f, old) != _normalized(f, body[f]):
                diff[f] = {"old": old, "new": body[f]}
    # 单条假设写回：diff 键 `assumptions[hid]`（FR-2），与整表替换互不覆盖
    _apply_assumption_entry_diff(i, body, diff)

    if diff:
        for f, d in diff.items():
            if "[" in f:
                continue  # assumptions[hid] 单条写回已在 _apply_assumption_entry_diff 中应用
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


# ---------- 想法落地闭环 P1 包 B（FR-12/FR-15）：假设导入与单条人工回写 ----------

# 进程内串行化「读-改-写」：并发导入/回写均进 diff、无丢更新（FR-15）
_ASSOC_LOCK = threading.Lock()


def _mio_fetch_hypotheses(timeout: float = 10.0) -> dict | None:
    """拉取 Mio 发酵假设库；Mio 不可用/CLI 失败 → None（调用方转 503）。"""
    try:
        cr = mio_runtime.creativity(limit=100, timeout=timeout, with_status=False)
    except Exception:  # noqa: BLE001 —— Mio 跨服务失败一律降级为不可用
        return None
    if not cr.get("available") or not cr.get("ok", True):
        return None
    return cr


@router.post("/{idea_id}/hypotheses/import")
def import_idea_hypotheses(idea_id: str, body: dict, db: Session = Depends(get_session)):
    """FR-12：从 Mio 发酵假设库导入 id 引用（集合合并去重、幂等；未知 id 422、Mio 不可用 503）。"""
    ids = body.get("ids")
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x.strip() for x in ids):
        raise HTTPException(422, "ids must be a list of non-empty strings")
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    uniq: list[str] = []
    for x in ids:
        s = x.strip()
        if s and s not in uniq:
            uniq.append(s)
    if not uniq:
        return {"ok": True, "added": [], "hypotheses": i.hypotheses or [], "idea": _idea_json(i)}
    cr = _mio_fetch_hypotheses()
    if cr is None:
        raise HTTPException(503, "Mio creativity unavailable, try again later")
    known = {h.get("id") for h in (cr.get("items") or []) if isinstance(h, dict)}
    missing = [x for x in uniq if x not in known]
    if missing:
        raise HTTPException(422, f"unknown hypothesis ids: {', '.join(missing)}")
    with _ASSOC_LOCK:
        db.refresh(i)
        cur = list(i.hypotheses) if isinstance(i.hypotheses, list) else []
        added = [x for x in uniq if x not in cur]
        if not added:
            return {"ok": True, "added": [], "hypotheses": cur, "idea": _idea_json(i)}
        new = cur + added
        i.hypotheses = new
        i.version += 1
        db.add(IdeaChange(idea_id=i.id, version=i.version,
                          diff={"hypotheses": {"old": cur, "new": new}},
                          reason=body.get("change_reason", "")))
        i.updated_at = _now()
        emit_event(db, type="idea_updated", entity="idea", entity_id=i.id,
                   payload={"version": i.version, "field": "hypotheses"})
        db.add(i)
        db.commit()
        db.refresh(i)
    return {"ok": True, "added": added, "hypotheses": new, "idea": _idea_json(i)}


@router.patch("/{idea_id}/assumptions/{hid}")
def patch_idea_assumption(idea_id: str, hid: str, body: dict, db: Session = Depends(get_session)):
    """FR-15：单条假设人工回写（独立端点，只改一条，diff 键 assumptions[hid]；hid 不存在 404）。"""
    patch = {}
    for f in ("status", "note", "confirmed_by"):
        if f in body and body[f] is not None:
            if not isinstance(body[f], str):
                raise HTTPException(422, f"{f} must be a string")
            patch[f] = body[f]
    if not patch:
        raise HTTPException(422, "nothing to update (status/note/confirmed_by)")
    with _ASSOC_LOCK:
        i = db.get(Idea, idea_id)
        if not i:
            raise HTTPException(404, "idea not found")
        db.refresh(i)
        cur = list(i.assumptions) if isinstance(i.assumptions, list) else []
        idx, old = -1, None
        for n, e in enumerate(cur):
            if isinstance(e, dict) and (e.get("hid") == hid or e.get("id") == hid):
                idx, old = n, e
                break
        if idx < 0:
            raise HTTPException(404, "assumption not found")
        if all(old.get(k) == v for k, v in patch.items()):
            return _idea_json(i)  # 幂等重放：值全同 → 不 bump 版本、不追加 diff
        new_entry = {**old, **patch}
        cur[idx] = new_entry
        i.assumptions = cur
        i.version += 1
        db.add(IdeaChange(idea_id=i.id, version=i.version,
                          diff={f"assumptions[{hid}]": {"old": old, "new": new_entry}},
                          reason=body.get("change_reason", "")))
        i.updated_at = _now()
        emit_event(db, type="idea_updated", entity="idea", entity_id=i.id,
                   payload={"version": i.version, "field": f"assumptions[{hid}]"})
        db.add(i)
        db.commit()
        db.refresh(i)
    return _idea_json(i)


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