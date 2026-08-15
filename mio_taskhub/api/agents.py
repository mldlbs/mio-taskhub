from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session
from mio_taskhub.db import get_session
from mio_taskhub.models import Agent, AgentStatus
from mio_taskhub.events import emit_event, broadcast_for_event

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
        event = emit_event(db, type="agent_registered", entity="agent", entity_id=name)
        db.add(existing)
        db.commit()
        broadcast_for_event(event)
        return {"name": name, "status": "online"}
    a = Agent(
        name=name,
        agent_type=body.get("agent_type", ""),
        status=AgentStatus.ONLINE,
        last_heartbeat=datetime.now(timezone.utc),
    )
    event = emit_event(db, type="agent_registered", entity="agent", entity_id=name)
    db.add(a)
    db.commit()
    broadcast_for_event(event)
    return {"name": name, "status": "registered"}
