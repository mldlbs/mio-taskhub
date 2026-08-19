from __future__ import annotations
import enum
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Column, JSON
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

class TaskStage(str, enum.Enum):
    BRAINSTORMING = "brainstorming"
    DESIGN = "design"
    PLANNING = "planning"
    READY = "ready"
    IMPLEMENTING = "implementing"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"

    @classmethod
    def can_advance(cls, src: "TaskStage", dst: "TaskStage") -> bool:
        if dst == cls.CANCELLED:
            return src != cls.CANCELLED and src != cls.DONE
        valid = {
            cls.BRAINSTORMING: {cls.DESIGN},
            cls.DESIGN: {cls.PLANNING},
            cls.PLANNING: {cls.READY},
            cls.READY: {cls.IMPLEMENTING},
            cls.IMPLEMENTING: {cls.REVIEW},
            cls.REVIEW: {cls.DONE},
        }
        return dst in valid.get(src, set())

class TaskKind(str, enum.Enum):
    NORMAL = "normal"
    CHANGE_TRACKING = "change_tracking"
    REVIEW = "idea_review"

class SubtaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"

class RefType(str, enum.Enum):
    BRANCH = "branch"
    COMMIT = "commit"
    PR = "pr"
    TAG = "tag"

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
    depends_on: list = Field(default_factory=list, sa_column=Column(JSON))
    state: TaskState = TaskState.QUEUED
    timeout_min: Optional[int] = None
    max_retries: int = 3
    task_kind: TaskKind = TaskKind.NORMAL
    attempt: int = 0
    acceptance_criteria: str = ""
    due_at: Optional[datetime] = None
    labels: list = Field(default_factory=list, sa_column=Column(JSON))
    project: str = ""
    workspace: str = ""
    files: list = Field(default_factory=list, sa_column=Column(JSON))
    deliverables: list = Field(default_factory=list, sa_column=Column(JSON))
    stage: TaskStage = TaskStage.READY
    spec_path: str = ""
    plan_path: str = ""
    review_result: str = ""
    idea_id: str = Field(default="", index=True)   # 拆解来源 idea
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
    last_heartbeat: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[str] = None
    exit_code: Optional[int] = None

class Subtask(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(index=True)
    order: int = 0
    title: str
    status: SubtaskStatus = SubtaskStatus.PENDING

class GitRef(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(index=True)
    ref_type: RefType = RefType.BRANCH
    value: str
    note: str = ""

class HistoryEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    type: str
    payload: Optional[str] = None
    at: datetime = Field(default_factory=_now)

class Discussion(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(index=True, default="")
    idea_id: str = Field(index=True, default="")
    topic: str
    agent: str = ""
    status: str = "open"
    summary: str = ""
    conclusions: str = ""
    stage: str = "brainstorming"
    started_at: datetime = Field(default_factory=_now)
    ended_at: Optional[datetime] = None

class DiscussionMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    discussion_id: str = Field(index=True)
    author: str
    role: str = "user"
    content: str
    at: datetime = Field(default_factory=_now)

class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)   # 自增 seq
    type: str
    entity: str = Field(default="", index=True)
    entity_id: str = Field(default="", index=True)
    run_id: str = Field(default="", index=True)                 # 兼容旧字段
    payload: Optional[str] = None
    at: datetime = Field(default_factory=_now)

class Plan(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    window_start: str
    window_end: str
    status: str = "draft"
    items: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)

class IdeaStatus(str, enum.Enum):
    NEW = "new"                 # 记录中
    FERMENTING = "fermenting"    # 发酵中
    FORMED = "formed"            # 已成形
    BROKEN_DOWN = "broken_down"  # 已拆解为任务
    ARCHIVED = "archived"
    CANCELLED = "cancelled"

    @classmethod
    def can_advance(cls, src: "IdeaStatus", dst: "IdeaStatus") -> bool:
        if dst == cls.ARCHIVED or dst == cls.CANCELLED:
            return src != cls.ARCHIVED and src != cls.CANCELLED and src != cls.BROKEN_DOWN
        # breakdown 可从任意非终态直接推进
        if dst == cls.BROKEN_DOWN:
            return src not in (cls.ARCHIVED, cls.CANCELLED, cls.BROKEN_DOWN)
        progress = [cls.NEW, cls.FERMENTING, cls.FORMED, cls.BROKEN_DOWN]
        if src not in progress or dst not in progress:
            return False
        # 只允许推进到下一档（相邻）
        return progress.index(dst) == progress.index(src) + 1

class Idea(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    title: str
    description: str = ""
    status: IdeaStatus = IdeaStatus.NEW
    version: int = 1
    project: str = ""
    labels: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_reviewed_at: Optional[datetime] = Field(default=None, index=True)
    review_count: int = 0

class IdeaChange(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)  # 自增，供 before_id 游标分页（同 Event.id 模式）
    idea_id: str = Field(index=True)
    version: int                          # 该条变更发生时 idea 的版本号
    created_at: datetime = Field(default_factory=_now)
    diff: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reason: str = ""

    # diff 结构：{field: {"old": ..., "new": ...}}


class IdeaHistory(SQLModel, table=True):
    """想法完整轨迹：评审/流转/讨论/操作记录。kind ∈ review/status/discussion/operation"""
    id: Optional[int] = Field(default=None, primary_key=True)
    idea_id: str = Field(index=True)
    kind: str                                 # review/status/discussion/operation
    actor: str = ""
    content: str = ""
    reasoning: Optional[str] = None           # 决策摘要（非完整 CoT）
    extra: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 结构化上下文
    at: datetime = Field(default_factory=_now, index=True)
