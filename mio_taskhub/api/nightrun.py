from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskState
from mio_taskhub.scheduling import night_runner as nr
from mio_taskhub.scheduling.night_runner import load_config, save_config

router = APIRouter(prefix="/nightrun", tags=["nightrun"])


@router.get("/config")
def get_config():
    cfg = load_config()
    runner = nr.get_runner()
    return {**cfg, "status": runner.status() if runner else {"running_agents": {}}}


@router.get("/full-config")
def get_full_config():
    """Returns full config (agents list) + status + window info."""
    cfg = load_config()
    runner = nr.get_runner()
    status = runner.status() if runner else {}
    return {
        **cfg,
        "status": status,
        "in_window": status.get("in_window", False),
        "window_display": status.get("window", f'{cfg["window_start"]}-{cfg["window_end"]}'),
    }


@router.get("/cron-tasks")
def get_cron_tasks(db: Session = Depends(get_session)):
    """List all tasks that have a cron_expr or future run_at."""
    tasks = db.exec(
        select(Task).where(
            (Task.cron_expr != None) | (Task.run_at != None)
        ).order_by(Task.priority.desc(), Task.created_at)
    ).all()
    return [
        {
            "id": t.id,
            "title": t.title,
            "cron_expr": t.cron_expr,
            "run_at": t.run_at.isoformat() if t.run_at else None,
            "state": t.state.value,
            "priority": t.priority,
            "target_agent_type": t.target_agent_type,
            "workspace": t.workspace,
            "project": t.project,
        }
        for t in tasks
    ]


@router.put("/config")
def put_config(body: dict):
    try:
        clean = save_config(body)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")
    return clean


@router.post("/spawn-now")
def spawn_now(body: dict = None):
    """手动触发：立即按当前配置 spawn（测试用，不受窗口限制）。"""
    runner = nr.get_runner()
    if not runner:
        raise HTTPException(503, "night runner not started")
    cfg = load_config()
    agents = (body or {}).get("agents") or cfg["agents"]
    if not agents:
        raise HTTPException(422, "no agents configured")
    results = {}
    for a in agents:
        name = a.get("agent") or a.get("agent_type")
        results[name] = runner._spawn(a)
    return {"spawned": results}


@router.post("/stop")
def stop_agents():
    runner = nr.get_runner()
    if not runner:
        raise HTTPException(503, "night runner not started")
    runner.stop_agents()
    return {"stopped": True}
