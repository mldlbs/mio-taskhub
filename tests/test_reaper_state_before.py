# -*- coding: utf-8 -*-
"""P1-OBS P2：reaper_decision 记录真实 run_state_before / run_heartbeated。"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

import mio_taskhub.background as bg
from mio_taskhub.db import engine
from mio_taskhub.models import (
    Agent, AgentStatus, Run, RunState, Task, TaskState, TaskStage, Event,
)


def _mk(run_state, progress):
    with Session(engine) as db:
        if not db.exec(select(Agent).where(Agent.name == "sb-agent")).first():
            db.add(Agent(name="sb-agent", agent_type="cli", status=AgentStatus.OFFLINE))
        task = Task(id=str(uuid.uuid4())[:8], title="sb task", state=TaskState.RUNNING,
                    stage=TaskStage.IMPLEMENTING, attempt=1, max_retries=3)
        db.add(task); db.commit(); db.refresh(task)
        run = Run(id=str(uuid.uuid4())[:8], task_id=task.id, agent_name="sb-agent",
                  state=run_state, attempt=1, progress=progress,
                  started_at=datetime.now(timezone.utc) - timedelta(seconds=400),
                  last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=400))
        db.add(run); db.commit(); db.refresh(run)
        return task.id, run.id


def _payload(rid):
    with Session(engine) as db:
        ev = db.exec(select(Event).where(Event.type == "reaper_decision",
                                         Event.entity_id == rid)).first()
        return json.loads(ev.payload) if ev else None


def test_claimed_run_records_state_before():
    tid, rid = _mk(RunState.CLAIMED, 0)
    bg._on_timeout(rid, tid)
    p = _payload(rid)
    assert p["run_state_before"] == "claimed"
    assert p["run_heartbeated"] is False


def test_running_run_records_state_before():
    tid, rid = _mk(RunState.RUNNING, 0)
    bg._on_timeout(rid, tid)
    p = _payload(rid)
    assert p["run_state_before"] == "running"
    assert p["run_heartbeated"] is True
