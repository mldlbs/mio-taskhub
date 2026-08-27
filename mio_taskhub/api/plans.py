from datetime import time
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskState
from mio_taskhub.planner import generate_night_plan
from mio_taskhub.status import normalize_depends

router = APIRouter(prefix="/plans", tags=["plans"])

PLANNABLE = (TaskState.QUEUED, TaskState.RETRYING)

def _parse_hm(s: str, default: time) -> time:
    if not s:
        return default
    try:
        h, m = s.split(":")
        return time(int(h), int(m))
    except Exception:
        return default

@router.get("/projects")
def list_projects(db: Session = Depends(get_session)):
    q = select(Task.project).where(Task.state.in_(PLANNABLE), Task.project != "")
    rows = db.exec(q).all()
    return sorted(set(rows))

@router.get("/night")
def night_plan(start: str = Query("22:00"), end: str = Query("07:00"),
               task_ids: str = Query(None), project: str = Query(None),
               db: Session = Depends(get_session)):
    q = select(Task).where(Task.state.in_(PLANNABLE))
    if project:
        q = q.where(Task.project == project)
    tasks = db.exec(q).all()
    pool = []
    wanted = {x.strip() for x in task_ids.split(",")} if task_ids else None
    for t in tasks:
        if wanted is not None and t.id not in wanted:
            continue
        pool.append({
            "id": t.id, "title": t.title, "est_duration_min": t.est_duration_min,
            "priority": t.priority, "depends_on": normalize_depends(t.depends_on),
            "target_agent_type": t.target_agent_type,
            "fallback_after": t.fallback_after,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    plan = generate_night_plan(pool, window_start=_parse_hm(start, time(22, 0)),
                               window_end=_parse_hm(end, time(7, 0)))
    pool_by_id = {p["id"]: p for p in pool}
    return {
        "window_start": plan.window_start,
        "window_end": plan.window_end,
        "has_overflow": plan.has_overflow,
        "max_parallel": plan.max_parallel,
        "items": [
            {"task_id": i.task_id, "title": i.title, "est_duration_min": i.est_duration_min,
             "scheduled_start": i.scheduled_start, "scheduled_end": i.scheduled_end,
             "project": next((t.get("project", "") for t in pool if t["id"] == i.task_id), ""),
             "agent_type": (pool_by_id.get(i.task_id) or {}).get("target_agent_type"),
             "fallback_after": (pool_by_id.get(i.task_id) or {}).get("fallback_after"),
             "created_at": (pool_by_id.get(i.task_id) or {}).get("created_at")}
            for i in plan.items
        ],
    }
