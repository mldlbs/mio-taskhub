from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session
from mio_taskhub.db import get_session
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
        db.add(existing)
        db.commit()
        return {"name": name, "status": "online"}
    a = Agent(
        name=name,
        agent_type=body.get("agent_type", ""),
        status=AgentStatus.ONLINE,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(a)
    db.commit()
    return {"name": name, "status": "registered"}
