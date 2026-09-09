import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskStage, TaskTemplate, TaskTemplateVersion
from mio_taskhub.utils import _now
from mio_taskhub.dependency import normalize_depends, task_deps
from mio_taskhub.events import emit_event
from mio_taskhub.api.task_helpers import parse_dt, validate_depends, check_cycle

router = APIRouter(prefix="/tasks", tags=["tasks"])


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
    due_at = parse_dt(body.get("due_at"), "due_at")
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
    validate_depends(t, db)
    check_cycle(t, db)
    db.add(t)
    event = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                       payload={"title": t.title, "stage": t.stage.value, "from_template": tpl_id})
    db.commit()
    db.refresh(t)
    return {
        "id": t.id, "title": t.title, "state": t.state.value,
        "priority": t.priority, "created_at": t.created_at.isoformat(),
        "depends_on": task_deps(t),
    }
