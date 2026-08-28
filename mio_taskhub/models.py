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

class TaskTemplate(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    title: str = Field(index=True)
    description: str = ""
    author: str = ""
    category: str = ""
    priority: int = 0
    est_duration_min: int = 30
    est_cost_min: int = 60
    target_agent_type: Optional[str] = None
    acceptance_criteria: str = ""
    files_template: list = Field(default_factory=list, sa_column=Column(JSON))
    deliverables_template: list = Field(default_factory=list, sa_column=Column(JSON))
    stages: list = Field(default_factory=list, sa_column=Column(JSON))
    dependencies: list = Field(default_factory=list, sa_column=Column(JSON))
    labels: list = Field(default_factory=list, sa_column=Column(JSON))
    is_public: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    version: int = 1
    tags: list = Field(default_factory=list, sa_column=Column(JSON))

class TaskTemplateVersion(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    template_id: str = Field(index=True)
    version: int = Field(default=1, index=True)
    content: dict = Field(default_factory=dict, sa_column=Column(JSON))
    changes: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_by: str = ""
    created_at: datetime = Field(default_factory=_now)
    description: str = ""

class Task(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    title: str
    description: str = ""
    target_agent_type: Optional[str] = None
    fallback_after: Optional[int] = None  # 从 created_at 起算的秒数，超过后允许非目标 agent 领取
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
    retry_at: Optional[datetime] = Field(default=None, index=True)
    retry_count: int = 0
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

class IdeaType(str, enum.Enum):
    IDEA = "idea"           # 普通想法
    ADR = "adr"             # 架构决策记录

class IdeaStatus(str, enum.Enum):
    NEW = "new"                 # 记录中
    FERMENTING = "fermenting"    # 发酵中
    FORMED = "formed"            # 已成形
    BROKEN_DOWN = "broken_down"  # 已拆解为任务
    ARCHIVED = "archived"
    CANCELLED = "cancelled"
    # ADR 专属状态
    PROPOSED = "proposed"       # ADR 提案
    ACCEPTED = "accepted"       # ADR 已接受
    REJECTED = "rejected"       # ADR 被拒绝
    DEPRECATED = "deprecated"   # ADR 已废弃
    SUPERSEDED = "superseded"   # ADR 被取代

    @classmethod
    def can_advance(cls, src: "IdeaStatus", dst: "IdeaStatus") -> bool:
        if dst == cls.CANCELLED:
            return src not in (cls.ARCHIVED, cls.CANCELLED)
        if dst == cls.ARCHIVED:
            return src not in (cls.ARCHIVED, cls.CANCELLED, cls.BROKEN_DOWN)
        # breakdown 可从任意非终态直接推进
        if dst == cls.BROKEN_DOWN:
            return src not in (cls.ARCHIVED, cls.CANCELLED, cls.BROKEN_DOWN)
        # 回流：拆解任务全部完成后可回到 formed 继续演化（如沉淀为 ADR）
        if src == cls.BROKEN_DOWN and dst == cls.FORMED:
            return True
        # 演化为 ADR：只有 formed 可以演化为 proposed
        if dst == cls.PROPOSED:
            return src == cls.FORMED
        # ADR 状态流转
        if dst == cls.ACCEPTED:
            return src == cls.PROPOSED
        if dst == cls.REJECTED:
            return src == cls.PROPOSED
        if dst == cls.DEPRECATED:
            return src in (cls.PROPOSED, cls.ACCEPTED)
        if dst == cls.SUPERSEDED:
            return src == cls.ACCEPTED
        # 普通 Idea 流转
        progress = [cls.NEW, cls.FERMENTING, cls.FORMED, cls.BROKEN_DOWN]
        if src not in progress or dst not in progress:
            return False
        # 只允许推进到下一档（相邻）
        return progress.index(dst) == progress.index(src) + 1

    @classmethod
    def is_adr_status(cls, status: "IdeaStatus") -> bool:
        """判断是否为 ADR 专属状态"""
        return status in (cls.PROPOSED, cls.ACCEPTED, cls.REJECTED, cls.DEPRECATED, cls.SUPERSEDED)

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
    # ADR 扩展字段
    idea_type: IdeaType = IdeaType.IDEA
    adr_number: Optional[int] = Field(default=None, index=True)  # ADR 序号（自增，如 ADR-001）
    adr_status: Optional[IdeaStatus] = Field(default=None, index=True)
    superseded_by: Optional[str] = Field(default=None, index=True)  # 被哪个 ADR 取代
    madr_context: Optional[str] = None      # MADR: 背景/上下文
    madr_decision: Optional[str] = None     # MADR: 决策内容
    madr_consequences: Optional[str] = None # MADR: 后果（正面/负面）
    madr_alternatives: Optional[list] = Field(default=None, sa_column=Column(JSON))  # MADR: 备选方案
    adr_file_path: Optional[str] = None     # Git 中的 ADR 文件路径

class ChangeType(str, enum.Enum):
    FIELD_CHANGE = "field_change"      # 普通字段变更
    TYPE_EVOLUTION = "type_evolution"  # Idea → ADR 演化
    ADR_ACTION = "adr_action"          # ADR 状态操作（accept/reject/deprecate/supersede）

class IdeaChange(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)  # 自增，供 before_id 游标分页（同 Event.id 模式）
    idea_id: str = Field(index=True)
    version: int                          # 该条变更发生时 idea 的版本号
    created_at: datetime = Field(default_factory=_now)
    diff: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reason: str = ""
    change_type: ChangeType = ChangeType.FIELD_CHANGE

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


class OutboxStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SYNCED = "synced"
    FAILED = "failed"


class OutboxEvent(SQLModel, table=True):
    """领域事件 Outbox：DB 事务写入，异步 Worker 消费投影到 Git 等外部系统"""
    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str                           # evolve-to-adr, accept, reject, deprecate, supersede
    aggregate_type: str = "idea"              # 聚合类型
    aggregate_id: str = Field(index=True)     # 聚合 ID（idea_id）
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 事件载荷
    status: OutboxStatus = OutboxStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    processed_at: Optional[datetime] = None
