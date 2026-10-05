"""价值加权闸门：替代硬编码计数，基于 novelty/feasibility/impact 加权 + 时间衰减。"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import Session, select, func

from mio_taskhub.models import Idea, IdeaStatus
from mio_taskhub.db import engine


@dataclass
class GateConfig:
    """闸门配置（可通过环境变量/数据库覆盖）。"""
    w_novelty: float = 0.4
    w_feasibility: float = 0.3
    w_impact: float = 0.3
    soft_threshold: float = 500.0      # 降级为 weekly
    hard_threshold: float = 800.0      # 告警 + critical
    decay_half_life_days: int = 14     # 权重半衰期
    auto_promote_score: float = 60.0   # INBOX -> NEW 自动晋升阈值
    archive_score: float = 30.0        # INBOX 低分老龄自动归档阈值
    max_inbox_days: int = 14           # INBOX 最大停留天数


@dataclass
class GateStatus:
    """闸门当前状态快照。"""
    total_weight: float
    inbox_count: int
    new_count: int
    level: str           # "ok" | "degraded" | "critical"
    capacity_available: int = 0
    throughput_per_day: float = 0.0


def _get_config() -> GateConfig:
    """从环境变量读取配置（运行时可热更新）。"""
    import os
    def env_float(key: str, default: float) -> float:
        v = os.environ.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except ValueError:
            return default

    def env_int(key: str, default: int) -> int:
        v = os.environ.get(key)
        if v is None:
            return default
        try:
            return int(v)
        except ValueError:
            return default

    return GateConfig(
        w_novelty=env_float("MIO_GATE_W_NOVELTY", 0.4),
        w_feasibility=env_float("MIO_GATE_W_FEASIBILITY", 0.3),
        w_impact=env_float("MIO_GATE_W_IMPACT", 0.3),
        soft_threshold=env_float("MIO_GATE_SOFT_THRESHOLD", 500.0),
        hard_threshold=env_float("MIO_GATE_HARD_THRESHOLD", 800.0),
        decay_half_life_days=env_int("MIO_GATE_DECAY_HALF_LIFE", 14),
        auto_promote_score=env_float("MIO_GATE_AUTO_PROMOTE", 60.0),
        archive_score=env_float("MIO_GATE_ARCHIVE_SCORE", 30.0),
        max_inbox_days=env_int("MIO_GATE_MAX_INBOX_DAYS", 14),
    )


CFG = _get_config()


def idea_weight(idea: Idea, now: Optional[datetime] = None) -> float:
    """单条想法的加权分，含时间衰减。
    
    分数 = (novelty*w_n + feasibility*w_f + impact*w_i) * decay
    decay = 0.5 ** (age_days / half_life)
    缺失分数按 0 处理（旧数据兼容）。
    """
    if now is None:
        now = datetime.now()
    # idea.created_at is naive (SQLite), ensure now is also naive for subtraction
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    base = (
        (idea.novelty or 0) * CFG.w_novelty +
        (idea.feasibility or 0) * CFG.w_feasibility +
        (idea.impact or 0) * CFG.w_impact
    )
    age_days = (now - idea.created_at).days
    if age_days < 0:
        age_days = 0
    decay = 0.5 ** (age_days / CFG.decay_half_life_days)
    return base * decay


def compute_gate_status(db: Session, now: Optional[datetime] = None) -> GateStatus:
    """计算当前闸门状态（供 cron_engine / API / 面板调用）。"""
    if now is None:
        now = datetime.now()

    # 拿 INBOX + NEW 的所有想法
    ideas = db.exec(
        select(Idea).where(Idea.status.in_([IdeaStatus.INBOX, IdeaStatus.NEW]))
    ).all()

    total_weight = 0.0
    inbox_count = 0
    new_count = 0
    for idea in ideas:
        w = idea_weight(idea, now)
        total_weight += w
        if idea.status == IdeaStatus.INBOX:
            inbox_count += 1
        else:
            new_count += 1

    # 评审带宽感知
    capacity = compute_review_capacity(db, now)

    # 等级判定
    if total_weight >= CFG.hard_threshold:
        level = "critical"
    elif total_weight >= CFG.soft_threshold:
        level = "degraded"
    else:
        level = "ok"

    return GateStatus(
        total_weight=round(total_weight, 2),
        inbox_count=inbox_count,
        new_count=new_count,
        level=level,
        capacity_available=capacity.available,
        throughput_per_day=round(capacity.throughput, 2),
    )


def compute_review_capacity(db: Session, now: Optional[datetime] = None) -> "CapacityInfo":
    """计算评审带宽（复用 capacity.py 逻辑，避免循环导入）。"""
    from mio_taskhub.scheduling.capacity import compute_review_capacity as _compute
    return _compute(db, now)


def should_generate_now(job_id: str, db: Session, now: Optional[datetime] = None) -> tuple[bool, str]:
    """判定是否应在本次执行生成（供 cron_engine 调用）。
    
    返回: (should_generate, reason)
    - ok: 生成
    - degraded: 生成但记录降级
    - critical: 不生成（除非 force），返回原因
    """
    if now is None:
        now = datetime.now()

    # 硬关闭开关
    import os
    if os.environ.get("MIO_CRON_PENDING_GUARD", "1") in ("0", "false", "False", "no"):
        return True, "guard disabled"

    status = compute_gate_status(db, now)

    if status.level == "ok":
        return True, "ok"
    elif status.level == "degraded":
        # degraded 仍生成，但降级频率由 cron 控制（见 cron_engine）
        return True, f"degraded: weight={status.total_weight}/{CFG.soft_threshold}"
    else:
        # critical：不生成，返回原因供执行记录
        return False, (
            f"critical: weight={status.total_weight}/{CFG.hard_threshold}, "
            f"inbox={status.inbox_count}, new={status.new_count}, "
            f"capacity={status.capacity_available}, throughput={status.throughput_per_day}/day"
        )


def force_generate(job_id: str, db: Session, reason: str = "") -> tuple[bool, str]:
    """强制生成（绕过闸门，留痕审计）。
    
    调用者需记录 force_reason、operator 到 job 执行记录。
    """
    import os
    os.environ["MIO_CRON_PENDING_GUARD"] = "0"  # 临时关闭，执行完恢复
    try:
        # 直接触发 webhook（复用现有逻辑）
        from mio_taskhub.scheduling.cron_engine import CronEngine
        engine_instance = CronEngine()
        job = db.get(__import__('mio_taskhub.models', fromlist=['ScheduledJob']).ScheduledJob, job_id)
        if not job:
            return False, "job not found"
        result = engine_instance._fire_webhook(job)
        return True, f"forced: {result}"
    finally:
        os.environ["MIO_CRON_PENDING_GUARD"] = "1"