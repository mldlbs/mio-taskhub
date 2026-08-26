from fastapi import APIRouter, HTTPException

from mio_taskhub import night_runner as nr
from mio_taskhub.night_runner import load_config, save_config

router = APIRouter(prefix="/nightrun", tags=["nightrun"])


@router.get("/config")
def get_config():
    cfg = load_config()
    runner = nr.get_runner()
    return {**cfg, "status": runner.status() if runner else {"running_agents": {}}}


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
