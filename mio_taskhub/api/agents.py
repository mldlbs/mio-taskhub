import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session

from mio_taskhub.db import get_session
from mio_taskhub.events import emit_event
from mio_taskhub.models import Agent, AgentStatus

router = APIRouter(prefix="/agents", tags=["agents"])

@router.post("/register")
def register(body: dict, db: Session = Depends(get_session)):
    name = body.get("name")
    if not name:
        return JSONResponse(status_code=400, content={"error": "name is required"})
    existing = db.get(Agent, name)
    if existing:
        existing.status = AgentStatus.ONLINE
        existing.agent_type = body.get("agent_type", existing.agent_type)
        existing.last_heartbeat = datetime.now(timezone.utc)
        emit_event(db, type="agent_registered", entity="agent", entity_id=name)
        db.add(existing)
        db.commit()
        return {"name": name, "status": "online"}
    a = Agent(
        name=name,
        agent_type=body.get("agent_type", ""),
        status=AgentStatus.ONLINE,
        last_heartbeat=datetime.now(timezone.utc),
    )
    emit_event(db, type="agent_registered", entity="agent", entity_id=name)
    db.add(a)
    db.commit()
    return {"name": name, "status": "registered"}

@router.get("")
def list_agents(db: Session = Depends(get_session)):
    """列出全部注册 agent 的在线状态与可领取性（只读）。

    与 GET /agents/{name} 同一契约：can_claim 按部署的复活模式计算。
    在线的排前面，其余按心跳新到旧；巡检脚本、前端 agent 总览、
    claim 前批量自查都从这里取数，不必逐个调 {name} 端点。
    """
    strict = os.environ.get("MIO_CLAIM_REVIVE_OFFLINE_AGENT", "1").lower() in ("0", "false", "off")
    rows = db.query(Agent).all()

    def _row(a: Agent) -> dict:
        st = str(getattr(a.status, "value", a.status))
        online = st == "online"
        can_claim = online or not strict
        return {
            "name": a.name,
            "status": st,
            "agent_type": a.agent_type,
            "last_heartbeat": (a.last_heartbeat.isoformat() if a.last_heartbeat else None),
            "can_claim": can_claim,
        }

    agents = [_row(a) for a in rows]
    agents.sort(key=lambda r: (r["status"] != "online", r["name"]))
    counts = {
        "total": len(agents),
        "online": sum(1 for r in agents if r["status"] == "online"),
        "can_claim": sum(1 for r in agents if r["can_claim"]),
    }
    return {"agents": agents, "counts": counts, "strict_mode": strict}


@router.get("/{name}")
def get_agent(name: str, db: Session = Depends(get_session)):
    """查单个 agent 的在线状态与可领取性。

    起因：运行被心跳超时回收后，agent 会被连带置 offline，之后所有 claim 都被静默拒绝，
    调用方看不出「任务可领但 agent 不可领」。这个端点让拒绝原因可自查。
    """
    a = db.get(Agent, name)
    if a is None:
        return JSONResponse(status_code=404, content={"detail": {
            "code": "agent.not_found",
            "name": name,
            "message": f"agent「{name}」没有注册记录；未注册 agent 的领取按盲领放行（INV-1）。",
        }})
    st = str(getattr(a.status, "value", a.status))
    online = st == "online"
    strict = os.environ.get("MIO_CLAIM_REVIVE_OFFLINE_AGENT", "1").lower() in ("0", "false", "off")
    can_claim = online or not strict   # 默认 claim 自动恢复；严格模式只有 online 能领
    return {
        "name": a.name,
        "status": st,
        "agent_type": a.agent_type,
        "last_heartbeat": (a.last_heartbeat.isoformat() if a.last_heartbeat else None),
        "can_claim": can_claim,
        "strict_mode": strict,
        "hint": None if can_claim else (
            f"此部署开了严格模式（MIO_CLAIM_REVIVE_OFFLINE_AGENT=0），状态 {st} 领取会被拒"
            f"（409 claim.agent_not_online）；需先调 POST /api/v1/agents/heartbeat "
            f"{{\"name\": \"{name}\"}}。"
        ),
    }


@router.post("/heartbeat")
def heartbeat(body: dict, db: Session = Depends(get_session)):
    """agent 心跳（upsert 自动注册）：刷新 last_heartbeat + ONLINE。

    不写事件（防心跳事件风暴）；未注册的 name 自动创建。
    """
    name = body.get("name")
    if not name:
        return JSONResponse(status_code=400, content={"error": "name is required"})
    a = db.get(Agent, name)
    if a is None:
        a = Agent(name=name, agent_type="cli", status=AgentStatus.ONLINE,
                  last_heartbeat=datetime.now(timezone.utc))
    else:
        a.status = AgentStatus.ONLINE
        a.last_heartbeat = datetime.now(timezone.utc)
    db.add(a)
    db.commit()
    db.refresh(a)
    return {"name": a.name, "status": "online",
            "last_heartbeat": a.last_heartbeat.isoformat()}
