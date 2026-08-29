from datetime import time, datetime, timezone
import json
import os
from pathlib import Path
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskState
from mio_taskhub.planner import generate_night_plan
from mio_taskhub.status import normalize_depends

router = APIRouter(prefix="/plans", tags=["plans"])

PLANNABLE = (TaskState.QUEUED, TaskState.RETRYING)

PLAN_DIR = Path(os.path.expanduser("~/.mio_taskhub")) / "night_plans"
PLAN_LATEST = PLAN_DIR / "latest.json"


def _save_night_plan(payload: dict) -> dict:
    """落盘：写 latest.json + 带时间戳归档。返回带 generated_at 的载荷。"""
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    saved = {**payload, "generated_at": datetime.now(timezone.utc).isoformat()}
    text = json.dumps(saved, ensure_ascii=False, indent=2)
    PLAN_LATEST.write_text(text, encoding="utf-8")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (PLAN_DIR / f"plan_{ts}.json").write_text(text, encoding="utf-8")
    return saved


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

@router.get("/night/saved")
def get_saved_night_plan():
    """读取最近一次落盘的计划。"""
    if not PLAN_LATEST.exists():
        raise HTTPException(404, "no saved plan")
    try:
        return json.loads(PLAN_LATEST.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "corrupted saved plan")

@router.get("/night")
def night_plan(start: str = Query("22:00"), end: str = Query("07:00"),
               task_ids: str = Query(None), project: str = Query(None),
               persist: bool = Query(True),
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
    payload = {
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
    if persist:
        saved = _save_night_plan(payload)
        payload["generated_at"] = saved["generated_at"]
    return payload
