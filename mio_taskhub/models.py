from __future__ import annotations
import enum
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _uuid() -> str:
    return str(uuid.uuid4())[:8]

class TaskState(str, enum.Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED_FAILED = "blocked_failed"

    @classmethod
    def can_transition(cls, src: "TaskState", dst: "TaskState") -> bool:
        valid = {
            cls.QUEUED:      {cls.CLAIMED, cls.CANCELLED},
            cls.CLAIMED:     {cls.RUNNING, cls.QUEUED, cls.FAILED},
            cls.RUNNING:     {cls.COMPLETED, cls.FAILED, cls.RETRYING, cls.CLAIMED},
            cls.RETRYING:    {cls.QUEUED, cls.FAILED},
            cls.COMPLETED:   set(),
            cls.FAILED:      {cls.RETRYING},
            cls.CANCELLED:   set(),
            cls.BLOCKED_FAILED: {cls.RETRYING, cls.CANCELLED},
        }
        return dst in valid.get(src, set())

class RunState(str, enum.Enum):
    CLAIMED = "claimed"
    RUNNING = "running"
    RETRYING = "retrying"
    FINISHED = "finished"

class AgentStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    IDLE = "idle"

class Task(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    title: str
    description: str = ""
    target_agent_type: Optional[str] = None
    priority: int = 0
    schedule_type: str = "once"
    run_at: Optional[datetime] = None
    cron_expr: Optional[str] = None
    est_duration_min: int = 30
    depends_on: Optional[str] = None
    state: TaskState = TaskState.QUEUED
    timeout_min: Optional[int] = None
    max_retries: int = 3
    attempt: int = 0
    created_at: datetime = Field(default_factory=_now)

class Agent(SQLModel, table=True):
    name: str = Field(primary_key=True)
    agent_type: str = ""
    status: AgentStatus = AgentStatus.OFFLINE
    last_heartbeat: Optional[datetime] = None
    capabilities: Optional[str] = None
    registered_at: datetime = Field(default_factory=_now)

class Run(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    agent_name: str
    state: RunState = RunState.CLAIMED
    attempt: int = 1
    checkpoint: Optional[str] = None
    progress: int = 0
    started_at: datetime = Field(default_factory=_now)
    finished_at: Optional[datetime] = None
    result: Optional[str] = None
    exit_code: Optional[int] = None

class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    type: str
    payload: Optional[str] = None
    at: datetime = Field(default_factory=_now)

class Plan(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    window_start: str
    window_end: str
    status: str = "draft"
    items: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
