import uuid
from datetime import datetime, timezone
from mio_taskhub.api.claim import claim_for, should_fallback
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import (
    Task, TaskState, TaskStage, Run, RunState, Subtask, SubtaskStatus, GitRef, RefType, HistoryEvent,
    Discussion, DiscussionMessage, Agent,
)
from mio_taskhub.utils import _now
from mio_taskhub.doc_paths import DOC_KINDS, doc_path_of, merge_doc_paths, sync_legacy_fields
from mio_taskhub.dependency import normalize_depends, task_deps
from mio_taskhub.events import emit_event
from mio_taskhub.api.task_helpers import parse_dt, parse_enum, check_cycle, validate_depends
from mio_taskhub.planner import detect_cycle

router = APIRouter(prefix="/tasks", tags=["tasks"])

@router.post("", response_model=dict)
def create_task(body: dict, db: Session = Depends(get_session)):
    due_at = parse_dt(body.get("due_at"), "due_at")
    run_at = parse_dt(body.get("run_at"), "run_at")
    stage_val = body.get("stage", "brainstorming")
    try:
        stage = TaskStage(stage_val)
    except ValueError:
        raise HTTPException(400, f"invalid stage: {stage_val}")
    # kind -> path 映射；spec_path / plan_path 仍可传，会并入 doc_paths
    doc_paths = merge_doc_paths({}, body.get("doc_paths"))
    _spec = (body.get("spec_path") or "").strip()
    _plan = (body.get("plan_path") or "").strip()
    if _spec:
        doc_paths["spec"] = _spec
    if _plan:
        doc_paths["plan"] = _plan
    t = Task(
        id=str(uuid.uuid4())[:8],
        title=body.get("title", ""),
        description=body.get("description", ""),
        target_agent_type=body.get("target_agent_type"),
        fallback_after=body.get("fallback_after"),
        priority=body.get("priority", 0),
        schedule_type=body.get("schedule_type", "once"),
        run_at=run_at,
        cron_expr=body.get("cron_expr"),
        est_duration_min=body.get("est_duration_min", 30),
        depends_on=normalize_depends(body.get("depends_on")),
        max_retries=body.get("max_retries", 3),
        acceptance_criteria=body.get("acceptance_criteria", ""),
        due_at=due_at,
        labels=body.get("labels", []),
        project=body.get("project", ""),
        workspace=body.get("workspace", ""),
        files=body.get("files", []),
        deliverables=body.get("deliverables", []),
        spec_path=_spec or None,
        plan_path=_plan or None,
        doc_paths=doc_paths,
        stage=stage,
    )
    validate_depends(t, db)
    check_cycle(t, db)
    db.add(t)
    event = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                       payload={"title": t.title, "stage": t.stage.value})
    db.commit()
    db.refresh(t)
    return {
        "id": t.id, "title": t.title, "state": t.state.value,
        "priority": t.priority, "created_at": t.created_at.isoformat(),
        "depends_on": task_deps(t), "idea_id": t.idea_id,
        "fallback_after": t.fallback_after,
    }

@router.get("", response_model=list)
def list_tasks(state: str = None, agent_type: str = None, stage: str = None,
               cancelled: bool = False, db: Session = Depends(get_session)):
    q = select(Task)
    if not cancelled:
        q = q.where(Task.state != TaskState.CANCELLED)
    if state:
        ts = None
        try:
            ts = TaskState[state.upper()]
        except KeyError:
            try:
                ts = TaskState(state)
            except ValueError:
                ts = None
        if ts is not None:
            q = q.where(Task.state == ts)
    if stage:
        q = q.where(Task.stage == TaskStage(stage))
    if agent_type:
        q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
    rows = db.exec(q).all()
    return [
        {"id": r.id, "title": r.title, "state": r.state.value, "stage": r.stage.value,
         "priority": r.priority, "target_agent_type": r.target_agent_type,
         "fallback_after": r.fallback_after,
         "depends_on": task_deps(r), "idea_id": r.idea_id,
         "est_duration_min": r.est_duration_min,
         "project": r.project, "workspace": r.workspace}
        for r in rows
    ]

def _task_detail(t: Task, db: Session) -> dict:
    subtasks = db.exec(select(Subtask).where(Subtask.task_id == t.id).order_by(Subtask.order)).all()
    gitrefs = db.exec(select(GitRef).where(GitRef.task_id == t.id)).all()
    history = db.exec(select(HistoryEvent).where(HistoryEvent.task_id == t.id).order_by(HistoryEvent.at)).all()
    discussions = db.exec(select(Discussion).where(Discussion.task_id == t.id)).all()
    runs = db.exec(select(Run).where(Run.task_id == t.id).order_by(Run.started_at.desc())).all()
    def _fmt(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    retry_countdown = None
    retry_backoff = None
    if t.state == TaskState.RETRYING and t.retry_at:
        ra = t.retry_at
        if ra.tzinfo is None:
            ra = ra.replace(tzinfo=timezone.utc)
        now = _now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        retry_countdown = max(0, int((ra - now).total_seconds()))
        try:
            retry_backoff = float((2 ** max(1, t.attempt)) * 2.0)
        except Exception:
            retry_backoff = None
    return {
        "id": t.id, "title": t.title, "description": t.description, "state": t.state.value,
        "priority": t.priority, "target_agent_type": t.target_agent_type,
        "schedule_type": t.schedule_type, "run_at": _fmt(t.run_at),
        "cron_expr": t.cron_expr, "est_duration_min": t.est_duration_min,
        "depends_on": task_deps(t), "max_retries": t.max_retries, "attempt": t.attempt,
        "retry_at": _fmt(t.retry_at), "retry_count": getattr(t, "retry_count", 0),
        "retry_countdown": retry_countdown, "retry_backoff_seconds": retry_backoff,
        "created_at": t.created_at.isoformat(),
        "acceptance_criteria": t.acceptance_criteria,
        "due_at": _fmt(t.due_at),
        "idea_id": t.idea_id,
        "labels": t.labels, "project": t.project, "workspace": t.workspace,
        "files": t.files, "deliverables": t.deliverables,
        "stage": t.stage.value if not isinstance(t.stage, str) else t.stage,
        "spec_path": t.spec_path,
        "plan_path": t.plan_path,
        "doc_paths": dict(t.doc_paths or {}),
        "doc_statuses": dict(getattr(t, "doc_statuses", None) or {}),
        "review_result": t.review_result,
        "fallback_after": t.fallback_after,
        "subtasks": [{"id": s.id, "order": s.order, "title": s.title, "status": s.status.value} for s in subtasks],
        "gitrefs": [{"id": g.id, "ref_type": g.ref_type.value, "value": g.value, "note": g.note} for g in gitrefs],
        "history": [{"id": h.id, "type": h.type, "payload": h.payload, "at": h.at.isoformat()} for h in history],
        "discussions": [{"id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
                         "summary": d.summary, "conclusions": d.conclusions,
                         "stage": d.stage, "started_at": d.started_at.isoformat()} for d in discussions],
        "runs": [{
            "id": r.id, "agent_name": r.agent_name,
            "state": r.state.value if not isinstance(r.state, str) else r.state,
            "attempt": r.attempt, "progress": r.progress,
            "started_at": _fmt(r.started_at), "finished_at": _fmt(r.finished_at),
            "exit_code": r.exit_code, "result": r.result,
        } for r in runs],
    }

@router.get("/status", response_model=dict)
def tasks_status_alias(agent: str = None, db: Session = Depends(get_session)):
    from mio_taskhub.api.board import board_summary as _bs
    return _bs(agent=agent, db=db)


@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return _task_detail(t, db)

@router.patch("/{task_id}")
def update_task(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    editable = ["title", "description", "priority", "est_duration_min", "max_retries",
                "acceptance_criteria", "due_at", "labels", "project", "workspace",
                "files", "deliverables",
                "target_agent_type", "fallback_after", "depends_on"]
    for k in editable:
        if k in body:
            v = body[k]
            if k == "due_at":
                v = parse_dt(v, "due_at")
            if k == "depends_on":
                t.depends_on = normalize_depends(v)
                validate_depends(t, db)
                check_cycle(t, db)
            else:
                setattr(t, k, v)
    # 文档路径：doc_paths 为主，spec_path/plan_path 是兼容入口；显式传空 = 清除该类型
    dp_incoming = {}
    if isinstance(body.get("doc_paths"), dict):
        dp_incoming.update(body["doc_paths"])
    for legacy_key, kind in (("spec_path", "spec"), ("plan_path", "plan")):
        if legacy_key in body:
            dp_incoming[kind] = body[legacy_key]
    if dp_incoming:
        t.doc_paths = merge_doc_paths(t.doc_paths, dp_incoming)
        merged = t.doc_paths or {}
        for kind, legacy_key in (("spec", "spec_path"), ("plan", "plan_path")):
            if kind in dp_incoming:
                # 显式传入即以其为准（含清空），不走 doc_path_of 的旧列回退
                setattr(t, legacy_key, merged.get(kind, ""))
    db.add(t)
    event = emit_event(db, type="task_updated", entity="task", entity_id=t.id)
    db.commit()
    db.refresh(t)
    return _task_detail(t, db)

@router.post("/{task_id}/discussions")
def add_discussion(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    conclusions = body.get("conclusions", "")
    status = "closed" if conclusions else "open"
    stage = body.get("stage", "brainstorming")
    d = Discussion(task_id=task_id, topic=body.get("topic", ""), agent=body.get("agent", ""),
                   status=status, summary=body.get("summary", ""), conclusions=conclusions,
                   stage=stage, ended_at=_now() if status == "closed" else None)
    db.add(d); db.commit(); db.refresh(d)
    for m in body.get("messages", []):
        db.add(DiscussionMessage(discussion_id=d.id, author=m.get("author", ""),
                                 role=m.get("role", "user"), content=m.get("content", "")))
    event = emit_event(db, type="discussion_created", entity="discussion", entity_id=d.id,
                       payload={"task_id": task_id})
    db.commit()
    return {"id": d.id, "task_id": d.task_id, "topic": d.topic, "agent": d.agent,
            "status": d.status, "summary": d.summary, "conclusions": d.conclusions,
            "stage": d.stage, "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None}

@router.get("/{task_id}/discussions")
def list_discussions(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    rows = db.exec(select(Discussion).where(Discussion.task_id == task_id).order_by(Discussion.started_at)).all()
    out = []
    for d in rows:
        msgs = db.exec(select(DiscussionMessage).where(DiscussionMessage.discussion_id == d.id).order_by(DiscussionMessage.at)).all()
        out.append({
            "id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
            "summary": d.summary, "conclusions": d.conclusions,
            "stage": d.stage, "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None,
            "messages": [{"author": m.author, "role": m.role, "content": m.content,
                          "at": m.at.isoformat()} for m in msgs],
        })
    return {"task_id": task_id, "discussions": out}

@router.post("/claim")
def claim_task(agent: str = Query(...), agent_type: str = Query(None),
               task_id: str = Query(None), project: str = Query(None), workspace: str = Query(None),
               files: str = Query(None), db: Session = Depends(get_session)):
    existing = db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()
    if existing:
        from mio_taskhub.read_evidence import build_claim_context
        resp = {"id": existing.id, "task_id": existing.task_id, "state": existing.state.value,
                "agent_name": existing.agent_name}
        t_existing = db.get(Task, existing.task_id)
        if t_existing is not None:
            resp.update(build_claim_context(t_existing))
        return resp
    if not agent_type:
        ag = db.get(Agent, agent)
        if ag and ag.agent_type:
            agent_type = ag.agent_type
    run = claim_for(agent, db, agent_type, task_id)
    if run is None:
        db.rollback()
        if task_id:
            t = db.get(Task, task_id)
            if not t:
                return Response(status_code=404, content=f"task {task_id} not found")
            if t.state != TaskState.QUEUED:
                return Response(status_code=409, content=f"task {task_id} 不可领取（当前状态 {t.state.value}）")
        return Response(status_code=204)
    task = db.get(Task, run.task_id)
    if project and not task.project:
        task.project = project
    if workspace and not task.workspace:
        task.workspace = workspace
    if files and not task.files:
        task.files = [f.strip() for f in files.split(",") if f.strip()]
    event = emit_event(db, type="task_claimed", entity="task", entity_id=task.id,
                       run_id=run.id, payload={"agent": agent, "attempt": task.attempt})
    db.add(task)
    db.commit()
    db.refresh(run)
    from mio_taskhub.read_evidence import build_claim_context
    resp = {"id": run.id, "task_id": run.task_id, "state": run.state.value,
            "agent_name": run.agent_name, "attempt": run.attempt}
    resp.update(build_claim_context(task))
    return resp
