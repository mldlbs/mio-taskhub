import uuid
import pathlib
import os
import re
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import Session, select
from sqlalchemy import case, Integer
from sqlalchemy import func
from mio_taskhub.db import get_session
from mio_taskhub.models import (
    Task, TaskState, TaskStage, Run, RunState, Subtask, SubtaskStatus, GitRef, RefType, HistoryEvent,
    Discussion, DiscussionMessage, Agent, TaskTemplate, TaskTemplateVersion,
)
from mio_taskhub.utils import _now
from mio_taskhub.status import normalize_depends, task_deps
from mio_taskhub.events import emit_event, broadcast_for_event
from mio_taskhub.planner import detect_cycle

router = APIRouter(prefix="/tasks", tags=["tasks"])

def _parse_dt(value, name: str):
    if not isinstance(value, str):
        return value
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"invalid {name}: {value}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # naive → treat as UTC
    else:
        dt = dt.astimezone(timezone.utc)       # any offset → normalize to UTC
    return dt

def _parse_enum(enum_cls, value, default=None):
    if value is None and default is not None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        raise HTTPException(400, f"invalid value: {value}, expected one of {[e.value for e in enum_cls]}")

def _graph_with(task, db) -> dict:
    """构建 {id: [dep_ids]} 依赖图（含给定 task 的新值）。"""
    graph = {}
    for t in db.exec(select(Task)).all():
        graph[t.id] = task_deps(t)
    graph[task.id] = task_deps(task)
    return graph

def _check_cycle(task, db):
    cyc = detect_cycle(_graph_with(task, db))
    if cyc:
        raise HTTPException(422, f"cyclic dependency: {' → '.join(cyc)}")

def _validate_depends(task, db):
    """校验 depends_on：缺失任务、自依赖。缺失优先于环检测，错误信息清晰。"""
    for dep in task_deps(task):
        if dep == task.id:
            raise HTTPException(422, f"cannot depend on itself: {dep}")
        if db.get(Task, dep) is None:
            raise HTTPException(422, f"dependency not found: {dep}")

@router.post("", response_model=dict)
def create_task(body: dict, db: Session = Depends(get_session)):
    due_at = _parse_dt(body.get("due_at"), "due_at")
    run_at = _parse_dt(body.get("run_at"), "run_at")
    stage_val = body.get("stage", "brainstorming")
    try:
        stage = TaskStage(stage_val)
    except ValueError:
        raise HTTPException(400, f"invalid stage: {stage_val}")
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
        spec_path=(body.get("spec_path") or "").strip() or None,
        plan_path=(body.get("plan_path") or "").strip() or None,
        stage=stage,
    )
    _validate_depends(t, db)
    _check_cycle(t, db)                       # 成环则抛 422（未 commit，自动回滚）
    db.add(t)
    event = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                       payload={"title": t.title, "stage": t.stage.value})
    db.commit()
    db.refresh(t)
    broadcast_for_event(event)
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
            ts = TaskState[state.upper()]  # 按成员名查找（忽略大小写）
        except KeyError:
            try:
                ts = TaskState(state)      # 回退：按值查找
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
            dt = dt.replace(tzinfo=timezone.utc)  # stored naive → UTC
        return dt.isoformat()
    # 退避倒计时（秒），retrying 且有 retry_at 时计算
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
        # 估算当前退避（2^attempt * base）
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

@router.get("/{task_id}/events")
def get_task_events(task_id: str, limit: int = 50, db: Session = Depends(get_session)):
    """返回任务的 M1 TaskEvent 时间线（状态变更记录）。"""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    events = db.exec(
        select(TaskEvent).where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.created_at.desc())
        .limit(limit)
    ).all()
    def _fmt(dt):
        if dt is None: return None
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return {
        "task_id": task_id,
        "events": [{
            "id": e.id,
            "event_type": e.event_type,
            "from_state": e.from_state,
            "from_stage": e.from_stage,
            "to_state": e.to_state,
            "to_stage": e.to_stage,
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "reason": e.reason,
            "metadata": e.event_metadata,
            "created_at": _fmt(e.created_at),
        } for e in events],
    }

@router.get("/graph")
def get_full_graph(db: Session = Depends(get_session)):
    """返回全量依赖图，用于看板 DAG 预览。"""
    tasks = db.exec(select(Task)).all()
    nodes = [
        {"id": t.id, "title": t.title, "state": t.state.value,
         "stage": t.stage.value if not isinstance(t.stage, str) else t.stage,
         "priority": t.priority, "depends_on": task_deps(t)}
        for t in tasks
    ]
    by_id = {n["id"] for n in nodes}
    edges = []
    missing = []
    for n in nodes:
        for d in n["depends_on"]:
            if d in by_id:
                edges.append({"from": d, "to": n["id"]})
            else:
                missing.append({"from": d, "to": n["id"]})
    graph = {n["id"]: n["depends_on"] for n in nodes}
    cyc = detect_cycle(graph)
    return {"nodes": nodes, "edges": edges, "missing": missing,
            "has_cycle": bool(cyc), "cycle_path": cyc}

@router.get("/{task_id}/graph")
def get_task_graph(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    all_tasks = db.exec(select(Task)).all()
    by_id = {x.id: x for x in all_tasks}
    # 邻接
    succ = {x.id: [] for x in all_tasks}
    for x in all_tasks:
        for d in task_deps(x):
            if d in by_id:
                succ[d].append(x.id)
    # 上游（祖先）
    ancestors = set()
    stack = list(task_deps(t))
    while stack:
        cur = stack.pop()
        if cur in ancestors or cur not in by_id:
            continue
        ancestors.add(cur)
        stack.extend(task_deps(by_id[cur]))
    # 下游（后代）
    descendants = set()
    stack = list(succ.get(t.id, []))
    while stack:
        cur = stack.pop()
        if cur in descendants:
            continue
        descendants.add(cur)
        stack.extend(succ.get(cur, []))
    # missing
    missing = [d for d in task_deps(t) if d not in by_id]
    # sub-graph nodes
    sub_ids = ancestors | {t.id} | descendants
    nodes = []
    for tid in sub_ids:
        if tid not in by_id:
            continue
        x = by_id[tid]
        nodes.append({"id": x.id, "title": x.title, "state": x.state.value,
                      "stage": x.stage.value if not isinstance(x.stage, str) else x.stage,
                      "priority": x.priority, "depends_on": task_deps(x)})
    edges = []
    miss_edges = []
    for n in nodes:
        for d in n["depends_on"]:
            if d in by_id and d in sub_ids:
                edges.append({"from": d, "to": n["id"]})
            elif d not in by_id:
                miss_edges.append({"from": d, "to": n["id"]})
    # 环检测（针对全图）
    graph = {x.id: task_deps(x) for x in all_tasks}
    cyc = detect_cycle(graph)
    return {
        "id": t.id,
        "ancestors": sorted(ancestors),
        "descendants": sorted(descendants),
        "depends_on": task_deps(t),
        "dependents": succ.get(t.id, []),
        "missing": missing,
        "nodes": nodes,
        "edges": edges,
        "missing_edges": miss_edges,
        "has_cycle": bool(cyc),
        "cycle_path": cyc,
    }

@router.get("/status", response_model=dict)
def tasks_status_alias(agent: str = None, db: Session = Depends(get_session)):
    """GET /tasks/status 别名：返回调度队列与超时告警（复用 board_summary）。"""
    from mio_taskhub.api.board import board_summary as _bs
    return _bs(agent=agent, db=db)


# ── Task Templates ──────────────────────────────────────────────────────────

def _template_json(t: TaskTemplate) -> dict:
    return {
        "id": t.id, "title": t.title, "description": t.description,
        "author": t.author, "category": t.category,
        "priority": t.priority, "est_duration_min": t.est_duration_min,
        "est_cost_min": t.est_cost_min,
        "target_agent_type": t.target_agent_type,
        "acceptance_criteria": t.acceptance_criteria,
        "files_template": t.files_template,
        "deliverables_template": t.deliverables_template,
        "stages": t.stages, "dependencies": t.dependencies,
        "labels": t.labels, "tags": t.tags,
        "is_public": t.is_public, "version": t.version,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


@router.get("/templates", response_model=list)
def list_templates(category: str = None, author: str = None,
                   db: Session = Depends(get_session)):
    q = select(TaskTemplate)
    if category:
        q = q.where(TaskTemplate.category == category)
    if author:
        q = q.where(TaskTemplate.author == author)
    rows = db.exec(q.order_by(TaskTemplate.updated_at.desc())).all()
    return [_template_json(r) for r in rows]


@router.post("/templates", response_model=dict)
def create_template(body: dict, db: Session = Depends(get_session)):
    t = TaskTemplate(
        id=str(uuid.uuid4())[:8],
        title=body.get("title", ""),
        description=body.get("description", ""),
        author=body.get("author", ""),
        category=body.get("category", ""),
        priority=body.get("priority", 0),
        est_duration_min=body.get("est_duration_min", 30),
        est_cost_min=body.get("est_cost_min", 60),
        target_agent_type=body.get("target_agent_type"),
        acceptance_criteria=body.get("acceptance_criteria", ""),
        files_template=body.get("files_template", []),
        deliverables_template=body.get("deliverables_template", []),
        stages=body.get("stages", []),
        dependencies=body.get("dependencies", []),
        labels=body.get("labels", []),
        tags=body.get("tags", []),
        is_public=body.get("is_public", True),
    )
    db.add(t)
    ver = TaskTemplateVersion(
        id=str(uuid.uuid4())[:8],
        template_id=t.id,
        version=1,
        content=_template_json(t),
        created_by=t.author,
        description="initial",
    )
    db.add(ver)
    db.commit()
    db.refresh(t)
    return _template_json(t)


@router.get("/templates/{tpl_id}")
def get_template(tpl_id: str, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    return _template_json(t)


@router.patch("/templates/{tpl_id}", response_model=dict)
def update_template(tpl_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    for key in ("title", "description", "author", "category", "acceptance_criteria",
                "target_agent_type", "is_public"):
        if key in body:
            setattr(t, key, body[key])
    for key in ("priority", "est_duration_min", "est_cost_min", "version"):
        if key in body:
            setattr(t, key, body[key])
    for key in ("files_template", "deliverables_template", "stages", "dependencies",
                "labels", "tags"):
        if key in body:
            setattr(t, key, body[key])
    t.updated_at = _now()
    t.version += 1
    ver = TaskTemplateVersion(
        id=str(uuid.uuid4())[:8],
        template_id=t.id,
        version=t.version,
        content=_template_json(t),
        changes=body,
        created_by=body.get("_author", ""),
        description=body.get("_change_desc", ""),
    )
    db.add(ver)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _template_json(t)


@router.delete("/templates/{tpl_id}")
def delete_template(tpl_id: str, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    db.delete(t)
    vers = db.exec(select(TaskTemplateVersion).where(TaskTemplateVersion.template_id == tpl_id)).all()
    for v in vers:
        db.delete(v)
    db.commit()
    return {"ok": True}


@router.get("/templates/{tpl_id}/versions")
def list_template_versions(tpl_id: str, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    vers = db.exec(
        select(TaskTemplateVersion)
        .where(TaskTemplateVersion.template_id == tpl_id)
        .order_by(TaskTemplateVersion.version.desc())
    ).all()
    return [
        {
            "id": v.id, "version": v.version, "created_at": v.created_at.isoformat(),
            "created_by": v.created_by, "description": v.description,
            "changes": v.changes,
        }
        for v in vers
    ]


@router.post("/templates/{tpl_id}/restore/{version}", response_model=dict)
def restore_template_version(tpl_id: str, version: int, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    v = db.exec(
        select(TaskTemplateVersion)
        .where(TaskTemplateVersion.template_id == tpl_id, TaskTemplateVersion.version == version)
    ).first()
    if not v:
        raise HTTPException(404, "version not found")
    content = v.content or {}
    # 回滚字段到模板
    for key in ("title", "description", "author", "category", "acceptance_criteria",
                "target_agent_type", "is_public"):
        if key in content:
            setattr(t, key, content[key])
    for key in ("priority", "est_duration_min", "est_cost_min"):
        if key in content:
            setattr(t, key, content[key])
    for key in ("files_template", "deliverables_template", "stages", "dependencies",
                "labels", "tags"):
        if key in content:
            setattr(t, key, content[key])
    t.updated_at = _now()
    t.version += 1
    new_ver = TaskTemplateVersion(
        id=str(uuid.uuid4())[:8],
        template_id=t.id,
        version=t.version,
        content=_template_json(t),
        changes={"restored_from": version},
        created_by=content.get("created_by", ""),
        description=f"restored from v{version}",
    )
    db.add(new_ver)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _template_json(t)


@router.post("/templates/from-task/{task_id}", response_model=dict)
def create_template_from_task(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    tpl = TaskTemplate(
        id=str(uuid.uuid4())[:8],
        title=body.get("title", f"模板：{t.title}"),
        description=body.get("description", t.description),
        author=body.get("author", ""),
        category=body.get("category", ""),
        priority=t.priority,
        est_duration_min=t.est_duration_min,
        target_agent_type=t.target_agent_type,
        acceptance_criteria=t.acceptance_criteria,
        files_template=list(t.files) if t.files else [],
        deliverables_template=list(t.deliverables) if t.deliverables else [],
        labels=list(t.labels) if t.labels else [],
        tags=body.get("tags", []),
        is_public=body.get("is_public", True),
    )
    db.add(tpl)
    ver = TaskTemplateVersion(
        id=str(uuid.uuid4())[:8],
        template_id=tpl.id, version=1,
        content=_template_json(tpl),
        created_by=tpl.author, description="created from task " + task_id,
    )
    db.add(ver)
    db.commit()
    db.refresh(tpl)
    return _template_json(tpl)


@router.post("/from-template/{tpl_id}", response_model=dict)
def create_task_from_template(tpl_id: str, body: dict, db: Session = Depends(get_session)):
    tpl = db.get(TaskTemplate, tpl_id)
    if not tpl:
        raise HTTPException(404, "template not found")
    due_at = _parse_dt(body.get("due_at"), "due_at")
    stage_val = body.get("stage", tpl.stages[0] if tpl.stages else "brainstorming")
    try:
        stage = TaskStage(stage_val)
    except ValueError:
        raise HTTPException(400, f"invalid stage: {stage_val}")
    t = Task(
        id=str(uuid.uuid4())[:8],
        title=body.get("title", tpl.title),
        description=body.get("description", tpl.description),
        target_agent_type=body.get("target_agent_type", tpl.target_agent_type),
        priority=body.get("priority", tpl.priority),
        est_duration_min=body.get("est_duration_min", tpl.est_duration_min),
        depends_on=normalize_depends(body.get("depends_on", tpl.dependencies)),
        max_retries=body.get("max_retries", 3),
        acceptance_criteria=body.get("acceptance_criteria", tpl.acceptance_criteria),
        due_at=due_at,
        labels=body.get("labels", tpl.labels),
        project=body.get("project", ""),
        workspace=body.get("workspace", ""),
        files=body.get("files", tpl.files_template),
        deliverables=body.get("deliverables", tpl.deliverables_template),
        stage=stage,
    )
    _validate_depends(t, db)
    _check_cycle(t, db)
    db.add(t)
    event = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                       payload={"title": t.title, "stage": t.stage.value, "from_template": tpl_id})
    db.commit()
    db.refresh(t)
    broadcast_for_event(event)
    return {
        "id": t.id, "title": t.title, "state": t.state.value,
        "priority": t.priority, "created_at": t.created_at.isoformat(),
        "depends_on": task_deps(t),
    }


@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return _task_detail(t, db)

@router.get('/{task_id}/doc')
def get_task_doc(task_id: str, kind: str = Query(...), db: Session = Depends(get_session)):
    """读取任务 spec/plan 文档内容，供 Web UI 展示。"""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    if kind not in ('spec', 'plan'):
        raise HTTPException(400, 'kind must be spec or plan')
    rel = t.spec_path if kind == 'spec' else t.plan_path
    if not rel:
        raise HTTPException(404, f'task has no {kind}_path')
    p, err = _resolve_doc_path(t, rel, kind)
    if err:
        return {'kind': kind, 'path': rel, 'content': err, 'truncated': False, 'missing': True}
    text, truncated = _read_content(p)
    return {'kind': kind, 'path': rel,
            'content': (text if text is not None else f'文件不存在：{p}'),
            'truncated': truncated, 'missing': text is None}

def _resolve_doc_path(task, rel, kind):
    """把 spec/plan 路径解析为绝对 Path；无法解析时返回 (None, 错误说明)。"""
    p = pathlib.Path(rel)
    if not p.is_absolute():
        base = (task.workspace or '').strip()
        if not base:
            return None, (f'任务未设置 workspace，且 {kind}_path 为相对路径「{rel}」，无法确定基准目录。'
                          f'请改用绝对路径，或在任务中填写 workspace 后重试。')
        p = pathlib.Path(base) / p
    return p.resolve(), None


def _read_content(p):
    """读取文件文本，超长截断并返回是否截断。"""
    if not p.exists():
        return None, False
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return f'读取失败：{p}\n{e}', False
    MAX = 200_000
    truncated = len(text) > MAX
    if truncated:
        text = text[:MAX]
    return text, truncated


def discover_task_docs(workspace: str):
    """扫描 workspace 下的 spec/plan 文档，返回候选列表（source=discovered）。"""
    import os
    if not workspace or not os.path.isdir(workspace):
        return []
    SKIP = {'node_modules', 'dist', 'build', '.venv', '.git', '.workbuddy',
            '.memory-backup', '__pycache__', '.idea', '.vscode'}
    out = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if not f.lower().endswith('.md'):
                continue
            low = f.lower()
            if 'spec' in low:
                kind = 'spec'
            elif 'plan' in low:
                kind = 'plan'
            else:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, workspace).replace(os.sep, '/')
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            out.append({'name': f, 'rel_path': rel, 'kind': kind, 'size': size, 'source': 'discovered'})
    out.sort(key=lambda x: (x['kind'], x['rel_path']))
    return out


def _field_doc_entry(rel, kind, ws):
    import os
    name = os.path.basename(rel)
    p = rel if os.path.isabs(rel) else (os.path.join(ws, rel) if ws else rel)
    try:
        size = os.path.getsize(p)
    except OSError:
        size = 0
    return {'name': name, 'rel_path': rel, 'kind': kind, 'size': size, 'source': 'field'}


@router.get('/{task_id}/documents')
def list_task_documents(task_id: str, db: Session = Depends(get_session)):
    """返回任务自己的 spec/plan + 工作区中与其相关的文档（按编号/关键词匹配，避免倾倒整个仓库）。"""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    ws = (t.workspace or '').strip()
    docs = []
    if t.spec_path:
        docs.append(_field_doc_entry(t.spec_path, 'spec', ws))
    if t.plan_path:
        docs.append(_field_doc_entry(t.plan_path, 'plan', ws))
    field_rel = {p for p in (t.spec_path, t.plan_path) if p}
    keys = _task_keys(t)
    for d in discover_task_docs(ws):
        if d['rel_path'] in field_rel:
            continue
        if not keys or not _is_related(d, keys):
            # keys 为空（如纯中文标题提取不到英文 token）时不关联任何 discovered 文档，
            # 只保留任务自己绑定的 spec/plan，避免"中文标题任务拉全库"
            continue
        d['related'] = True
        docs.append(d)
    docs.sort(key=lambda x: (0 if x['source'] == 'field' else 1, x['kind'], x.get('rel_path', '')))
    return {'workspace': ws, 'documents': docs}


def _slug(name):
    n = name.lower()
    if n.startswith('spec-') or n.startswith('plan-'):
        n = n[5:]
    if n.endswith('.md'):
        n = n[:-3]
    return n


def _tokens(s):
    return {tok for tok in re.split(r'[^a-z0-9]+', s.lower()) if len(tok) >= 2}


def _task_keys(t):
    keys = set()
    for p in (t.spec_path, t.plan_path):
        if not p:
            continue
        keys |= _tokens(_slug(os.path.basename(p)))
    if t.title:
        keys |= _tokens(t.title)
    return keys


def _is_related(d, keys):
    return bool(_tokens(_slug(d['name'])) & keys)


@router.get('/{task_id}/file')
def get_task_file(task_id: str, path: str = Query(...), db: Session = Depends(get_session)):
    """基于任务 workspace 安全读取任意文档内容（防目录穿越）。"""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    ws = (t.workspace or '').strip()
    if not ws:
        raise HTTPException(400, 'task has no workspace')
    base = pathlib.Path(ws).resolve()
    target = (base / path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(400, 'invalid path: escape workspace')
    if not target.is_file():
        raise HTTPException(404, f'file not found: {target}')
    text, truncated = _read_content(target)
    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    return {'path': path, 'content': text, 'truncated': truncated, 'size': size}

@router.patch("/{task_id}")
def update_task(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    editable = ["title", "description", "priority", "est_duration_min", "max_retries",
                "acceptance_criteria", "due_at", "labels", "project", "workspace",
                "spec_path", "plan_path", "files", "deliverables",
                "target_agent_type", "fallback_after", "depends_on"]
    for k in editable:
        if k in body:
            v = body[k]
            if k == "due_at":
                v = _parse_dt(v, "due_at")
            if k == "depends_on":
                t.depends_on = normalize_depends(v)
                _validate_depends(t, db)
                _check_cycle(t, db)
            else:
                setattr(t, k, v)
    db.add(t)
    event = emit_event(db, type="task_updated", entity="task", entity_id=t.id)
    db.commit()
    db.refresh(t)
    broadcast_for_event(event)
    return _task_detail(t, db)

@router.post("/{task_id}/subtasks")
def add_subtask(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    st = Subtask(task_id=task_id, order=body.get("order", 0),
                 title=body.get("title", ""), status=_parse_enum(SubtaskStatus, body.get("status"), "pending"))
    event = emit_event(db, type="task_subtask_added", entity="task", entity_id=task_id,
                       payload={"subtask_id": st.id, "title": st.title})
    db.add(st); db.commit(); db.refresh(st)
    broadcast_for_event(event)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}

@router.patch("/{task_id}/subtasks/{sid}")
def update_subtask(task_id: str, sid: str, body: dict, db: Session = Depends(get_session)):
    st = db.get(Subtask, sid)
    if not st or st.task_id != task_id:
        raise HTTPException(404, "subtask not found")
    if "title" in body: st.title = body["title"]
    if "order" in body: st.order = body["order"]
    if "status" in body: st.status = _parse_enum(SubtaskStatus, body["status"])
    event = emit_event(db, type="task_subtask_updated", entity="task", entity_id=task_id,
                       payload={"subtask_id": st.id, "status": st.status.value})
    db.add(st); db.commit(); db.refresh(st)
    broadcast_for_event(event)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}

@router.post("/{task_id}/gitrefs")
def add_gitref(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    g = GitRef(task_id=task_id, ref_type=_parse_enum(RefType, body.get("ref_type"), "branch"),
               value=body.get("value", ""), note=body.get("note", ""))
    event = emit_event(db, type="task_gitref_added", entity="task", entity_id=task_id,
                       payload={"gitref_id": g.id, "ref_type": g.ref_type.value})
    db.add(g); db.commit(); db.refresh(g)
    broadcast_for_event(event)
    return {"id": g.id, "task_id": g.task_id, "ref_type": g.ref_type.value,
            "value": g.value, "note": g.note}

@router.post("/{task_id}/history")
def add_history(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    import json as _json
    h = HistoryEvent(task_id=task_id, type=body.get("type", ""),
                     payload=_json.dumps(body.get("payload")) if body.get("payload") is not None else None)
    event = emit_event(db, type="task_history_added", entity="task", entity_id=task_id,
                       payload={"type": h.type})
    db.add(h); db.commit(); db.refresh(h)
    broadcast_for_event(event)
    return {"id": h.id, "task_id": h.task_id, "type": h.type,
            "payload": h.payload, "at": h.at.isoformat()}

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
    broadcast_for_event(event)
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

@router.delete("/{task_id}")
def cancel_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404)
    # M1: 走状态机校验 + 写 TaskEvent + 设时间戳
    from mio_taskhub.transitions import apply_transition
    from mio_taskhub.status import State, Stage, ActorType, IllegalTransition
    current_stage = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    try:
        _, m1_event = apply_transition(
            t, State.CANCELLED, Stage(current_stage.value),
            ActorType.USER, "api:cancel_task",
            reason="用户取消",
        )
        db.add(m1_event)
    except IllegalTransition:
        # 终态（completed, done）不允许再 cancel — 显式拒绝
        raise HTTPException(409, f"task {task_id} 不可取消（终态）")
    # 保留旧事件广播（向后兼容）
    event = emit_event(db, type="task_cancelled", entity="task", entity_id=task_id)
    db.add(t)
    db.commit()
    broadcast_for_event(event)
    return {"ok": True, "state": "cancelled"}


@router.post("/{task_id}/retry")
def retry_task(task_id: str, body: dict = None, db: Session = Depends(get_session)):
    """手动重试：允许从 failed / retrying 重新进入 queued/ready。

    - failed：超限后人工重试，重置退避并重入队列（若 attempt >= max_retries 则重置 attempt 以给新预算）
    - retrying：跳过退避立即重入队列
    - 其他状态：409 不可重试
    """
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    if t.state not in (TaskState.FAILED, TaskState.RETRYING):
        raise HTTPException(409, f"task {task_id} 不可重试（当前状态 {t.state.value}，仅 failed/retrying 可重试）")
    orig_state = t.state.value
    # 若已达最大重试，手动重试视为给予新机会，重置计数
    if t.attempt >= t.max_retries:
        t.attempt = 0
        t.retry_count = 0
    t.retry_at = None
    # M1: 走状态机 T16 manual_retry → (QUEUED, READY)
    from mio_taskhub.transitions import apply_transition
    from mio_taskhub.status import State, Stage, ActorType, IllegalTransition
    current_stage = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    try:
        _, m1_event = apply_transition(
            t, State.QUEUED, Stage.READY,
            ActorType.USER, "api:retry_task",
            reason=f"manual_retry from {orig_state}",
        )
        db.add(m1_event)
    except IllegalTransition as e:
        raise HTTPException(409, f"retry 非法: {e}")
    event = emit_event(db, type="task_retry_manual", entity="task", entity_id=t.id,
                       payload={"from_state": orig_state, "attempt": t.attempt, "max_retries": t.max_retries})
    db.add(t)
    db.commit()
    db.refresh(t)
    requeued = emit_event(db, type="task_retry_requeued", entity="task", entity_id=t.id,
                          payload={"reason": "manual_retry", "attempt": t.attempt, "from": orig_state})
    db.commit()
    broadcast_for_event(event)
    broadcast_for_event(requeued)
    return {"id": t.id, "state": t.state.value, "stage": t.stage.value,
            "attempt": t.attempt, "max_retries": t.max_retries, "retry_at": None}

def _apply_stage_requirements(t: Task, dst: TaskStage, body: dict, strict: bool = True):
    """校验目标阶段的产出物并设置辅助字段。抛 HTTPException(422) 当产出物缺失。

    design 需 spec_path、planning 需 plan_path、done 需 review_result。
    注意：本函数 **不修改** task.state / task.stage —— 状态变更由调用方通过
    apply_transition() 统一处理。
    strict=False 时（拖拽轻量路径）不强制产出物，仅在提供时记录，
    done 缺 review_result 时自动补默认结论。
    """
    if dst == TaskStage.DESIGN:
        if body.get("spec_path"):
            t.spec_path = body["spec_path"]
        elif strict and not t.spec_path:
            raise HTTPException(422, "design stage requires spec_path")
    if dst == TaskStage.PLANNING:
        if body.get("plan_path"):
            t.plan_path = body["plan_path"]
        elif strict and not t.plan_path:
            raise HTTPException(422, "planning stage requires plan_path")
    if dst == TaskStage.DONE:
        review = body.get("review_result") or t.review_result or "（拖拽完成）"
        if strict and not (body.get("review_result") or t.review_result):
            raise HTTPException(422, "done stage requires review_result")
        t.review_result = review

@router.post("/{task_id}/stage")
def advance_stage(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    target = body.get("target_stage")
    if not target:
        raise HTTPException(422, "target_stage is required")
    try:
        dst = TaskStage(target)
    except ValueError:
        raise HTTPException(400, f"invalid target_stage: {target}")
    src = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    if not TaskStage.can_advance(src, dst):
        raise HTTPException(400, f"cannot advance from {src.value} to {dst.value}")
    if dst == TaskStage.DESIGN:
        discussions = db.exec(select(Discussion).where(Discussion.task_id == task_id)).all()
        if not discussions:
            raise HTTPException(422, "design stage requires at least one discussion record")
    _apply_stage_requirements(t, dst, body)
    # M1: 走状态机记录 TaskEvent + 设时间戳（不预设 t.stage，由 apply_transition 写入）
    from mio_taskhub.transitions import apply_transition
    from mio_taskhub.status import State, Stage as M1Stage, ActorType, IllegalTransition as M1Illegal
    m1_events = []
    try:
        cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
        from_st = M1Stage(src.value if src != TaskStage.CANCELLED else "brainstorming")
        if dst == TaskStage.DONE:
            # 先确保 state=COMPLETED（T5: queued/claimed,review → completed,review）
            if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
                _, e1 = apply_transition(
                    t, State.COMPLETED, from_st,
                    ActorType.USER, "api:advance_stage",
                    reason="advance→done: review pass",
                )
                if e1: m1_events.append(e1)
            # T6: (completed, review) → (completed, done)
            _, e2 = apply_transition(
                t, State.COMPLETED, M1Stage.DONE,
                ActorType.SYSTEM, "auto:finalize",
                reason="T6 finalize",
            )
            if e2: m1_events.append(e2)
        elif dst == TaskStage.CANCELLED:
            _, e = apply_transition(
                t, State.CANCELLED, from_st,
                ActorType.USER, "api:advance_stage",
                reason="advance→cancelled",
            )
            if e: m1_events.append(e)
        else:
            # T17 (from queued) or T11 (from claimed)：stage-only advance
            actor = ActorType.USER if cur_s == TaskState.QUEUED else ActorType.SYSTEM
            actor_id = "api:advance_stage" if cur_s == TaskState.QUEUED else "auto:advance"
            _, e = apply_transition(
                t, State(cur_s.value if cur_s != TaskState.BLOCKED_FAILED else "queued"),
                M1Stage(dst.value), actor, actor_id,
                reason=f"advance {src.value}→{dst.value}",
            )
            if e: m1_events.append(e)
    except M1Illegal as exc:
        raise HTTPException(400, f"state machine rejected advance: {exc}")
    event = emit_event(db, type="task_stage", entity="task", entity_id=task_id,
                       payload={"target": dst.value})
    db.add(t)
    for ev in m1_events:
        db.add(ev)
    db.commit()
    db.refresh(t)
    broadcast_for_event(event)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "review_result": t.review_result,
            "state": t.state.value}

@router.post("/{task_id}/stage/move")
def move_to_stage(task_id: str, body: dict, db: Session = Depends(get_session)):
    """任意跳转到目标阶段（拖拽用）。不校验相邻性，但保留终态保护与产出物校验。

    与 advance_stage 的区别：不要求相邻推进，且 design 阶段不强制要求讨论记录
    （拖拽是轻量路径，产物可由用户自行补充）。所有移动通过 apply_transition 记录。
    """
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    target = body.get("target_stage")
    if not target:
        raise HTTPException(422, "target_stage is required")
    try:
        dst = TaskStage(target)
    except ValueError:
        raise HTTPException(400, f"invalid target_stage: {target}")
    src = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    if src in (TaskStage.DONE, TaskStage.CANCELLED):
        raise HTTPException(400, f"cannot move terminal stage {src.value}")
    _apply_stage_requirements(t, dst, body, strict=False)
    # 同阶段移动：仅更新元数据，不做状态变更
    if src == dst:
        db.add(t)
        db.commit()
        db.refresh(t)
        return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
                "plan_path": t.plan_path, "review_result": t.review_result,
                "state": t.state.value}
    # M1: 全部走状态机（含 T18 自由拖拽）
    from mio_taskhub.transitions import apply_transition, _orm_to_status_stage
    from mio_taskhub.status import State as M1State, Stage as M1Stage, ActorType as M1Actor
    m1_events = []
    cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
    orm_state_map = {TaskState.BLOCKED_FAILED: M1State.QUEUED}
    cur_m1 = orm_state_map.get(cur_s, M1State(cur_s.value))
    to_st = _orm_to_status_stage(dst) if dst != TaskStage.CANCELLED else M1Stage.BRAINSTORMING
    if dst == TaskStage.DONE:
        # 先确保 state=COMPLETED（T5: queued/claimed,review → completed,review）
        if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
            from_st_done = _orm_to_status_stage(src) if src != TaskStage.CANCELLED else M1Stage.BRAINSTORMING
            _, e1 = apply_transition(t, M1State.COMPLETED, from_st_done,
                                     M1Actor.USER, "api:move_to_stage",
                                     reason="move→done")
            if e1: m1_events.append(e1)
        # T6: (completed, review) → (completed, done)
        _, e2 = apply_transition(t, M1State.COMPLETED, M1Stage.DONE,
                                 M1Actor.SYSTEM, "auto:finalize",
                                 reason="move→done: finalize")
        if e2: m1_events.append(e2)
    elif dst == TaskStage.CANCELLED:
        from_st = _orm_to_status_stage(src) if src != TaskStage.CANCELLED else M1Stage.BRAINSTORMING
        _, e = apply_transition(t, M1State.CANCELLED, from_st,
                                M1Actor.USER, "api:move_to_stage",
                                reason="move→cancelled")
        if e: m1_events.append(e)
    else:
        # T18: 自由拖拽 stage-only（validate_transition 内 fallback）
        actor = M1Actor.USER if cur_s == TaskState.QUEUED else M1Actor.SYSTEM
        actor_id = "api:move_to_stage" if cur_s == TaskState.QUEUED else "auto:move"
        _, e = apply_transition(t, cur_m1, to_st, actor, actor_id,
                                reason=f"move {src.value}→{dst.value}")
        if e: m1_events.append(e)
    event = emit_event(db, type="task_moved", entity="task", entity_id=t.id,
                       payload={"from": src.value, "to": dst.value})
    db.add(t)
    for ev in m1_events:
        db.add(ev)
    db.commit()
    db.refresh(t)
    broadcast_for_event(event)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "review_result": t.review_result,
            "state": t.state.value}

def _should_fallback(task, agent_type):
    """从 created_at 起算，超过 fallback_after 秒后降为通用任务。"""
    if not task.target_agent_type or not agent_type:
        return False
    if task.target_agent_type == agent_type:
        return False
    if task.fallback_after is None:
        return False
    if task.created_at is None:
        return False
    elapsed = (_now() - task.created_at).total_seconds()
    return elapsed >= task.fallback_after

def _claim_for(agent: str, db: Session, agent_type: Optional[str] = None, task_id: Optional[str] = None):
    """原子领取：返回该 agent 的 Run 或 None。

    先查 agent 已有 claimed/running run（幂等）；否则：
    - 传 task_id：直接认领该指定任务（按 id 领取），无视阶段，仅校验未认领；
    - 否则按关联度 + 优先级 + FIFO 找 ready 任务。
    用条件更新（WHERE state='queued'）抢占，避免 SQLite 无 FOR UPDATE 下的并发双 run。

    关联度排序（内部使用，不硬排斥）：类型匹配 > 无人认领 > 他人专属。
    即同优先级下更相关的任务排前面，但他人专属任务仍可被领到（排最后）。
    agent_type 兜底：手动 claim 不传时回查注册 agent 的 agent_type 用于排序。
    """
    existing = _find_existing_run(db, agent)
    if existing:
        return existing

    if not agent_type:
        agent_type = _lookup_agent_type(db, agent) or agent_type

    candidate = _pick_candidate_task(db, agent_type, task_id)
    if not candidate:
        return None
    return _atomic_claim(db, agent, candidate)


def _find_existing_run(db, agent):
    """幂等返回：若 agent 已有 claimed/running run，直接返回。"""
    return db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()


def _lookup_agent_type(db, agent):
    """回查已注册 agent 的 agent_type（claim 时类型守卫兜底）。"""
    ag = db.get(Agent, agent)
    return ag.agent_type if ag and ag.agent_type else None


def _pick_candidate_task(db, agent_type, task_id):
    """选候选任务：指定 task_id 时按 id 领取；否则按关联度+优先级+FIFO 找 ready 任务。"""
    if task_id:
        task = db.get(Task, task_id)
        if not task or task.state != TaskState.QUEUED:
            return None
        return task
    q = select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
    relevance = _build_relevance(agent_type)
    rows = db.exec(q.order_by(relevance, Task.priority.desc(), Task.created_at.asc())).all()
    return _first_ready_row(rows)


def _build_relevance(agent_type):
    """关联度排序：类型匹配 > 无人认领/fallback已到期 > 他人专属。"""
    if agent_type:
        fallback_ready = (
            Task.fallback_after.is_not(None)
            & Task.created_at.is_not(None)
            & (
                func.cast(func.strftime("%s", "now"), Integer)
                >= (
                    func.cast(func.strftime("%s", Task.created_at), Integer)
                    + Task.fallback_after
                )
            )
        )
        return case(
            (Task.target_agent_type == agent_type, 0),
            (Task.target_agent_type.is_(None), 1),
            (fallback_ready, 1),
            else_=2,
        )
    return case((Task.target_agent_type.is_(None), 0), else_=1)


def _first_ready_row(rows):
    """取首个未到 run_at 时间的一次性任务。"""
    now = _now()
    for t in rows:
        if t.schedule_type == "once" and t.run_at:
            run_at = t.run_at if t.run_at.tzinfo else t.run_at.replace(tzinfo=timezone.utc)
            if run_at > now:
                continue
        return t
    return None


def _atomic_claim(db, agent, candidate):
    """条件更新抢占：仅当 state 仍为 queued 才算抢到。"""
    from sqlalchemy import update as sa_update
    res = db.exec(
        sa_update(Task)
        .where(Task.id == candidate.id, Task.state == TaskState.QUEUED)
        .values(state=TaskState.CLAIMED)
    )
    if res.rowcount != 1:
        db.rollback()
        return None
    task = db.get(Task, candidate.id)
    db.refresh(task)
    from mio_taskhub.transitions import record_post_claim
    claim_event = record_post_claim(task, agent)
    task.attempt += 1
    task.stage = TaskStage.IMPLEMENTING
    run = Run(
        id=str(uuid.uuid4())[:8],
        task_id=task.id,
        agent_name=agent,
        state=RunState.CLAIMED,
        attempt=task.attempt,
        started_at=_now(),
        last_heartbeat=_now(),
    )
    db.add(task)
    if claim_event:
        db.add(claim_event)
    db.add(run)
    return run

@router.post("/claim")
def claim_task(agent: str = Query(...), agent_type: str = Query(None),
               task_id: str = Query(None), project: str = Query(None), workspace: str = Query(None),
               files: str = Query(None), db: Session = Depends(get_session)):
    existing = db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()
    if existing:
        return {"id": existing.id, "task_id": existing.task_id, "state": existing.state.value,
                "agent_name": existing.agent_name}
    # 回查 agent_type：手动 claim 不传时，用注册信息兜底，保证类型守卫一致
    if not agent_type:
        ag = db.get(Agent, agent)
        if ag and ag.agent_type:
            agent_type = ag.agent_type
    run = _claim_for(agent, db, agent_type, task_id)
    if run is None:
        db.rollback()
        if task_id:
            t = db.get(Task, task_id)
            if not t:
                return Response(status_code=404, content=f"task {task_id} not found")
            if t.state != TaskState.QUEUED:
                return Response(status_code=409, content=f"task {task_id} 不可领取（当前状态 {t.state.value}）")
        return Response(status_code=204)
    task = db.get(Task, run.task_id)  # _claim_for 已更新 attempt/stage，勿再 refresh
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
    broadcast_for_event(event)
    return {"id": run.id, "task_id": run.task_id, "state": run.state.value,
            "agent_name": run.agent_name, "attempt": run.attempt}
