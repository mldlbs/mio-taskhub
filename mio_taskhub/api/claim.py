import uuid
from typing import Optional
from datetime import timezone
from sqlmodel import Session, select
from sqlalchemy import case, Integer, func
from sqlalchemy import update as sa_update
from mio_taskhub.models import Task, TaskState, TaskStage, Run, RunState, Agent
from mio_taskhub.utils import _now


def should_fallback(task, agent_type):
    """从 created_at 起算，超过 fallback_after 秒后降为通用任务。"""
    if not task.target_agent_type or not agent_type:
        return False
    if task.target_agent_type == agent_type:
        return False
    if task.fallback_after is None:
        return False
    if task.created_at is None:
        return False
    elapsed = (_now() - task.created_at).total_seconds()
    return elapsed >= task.fallback_after

def claim_for(agent: str, db: Session, agent_type: Optional[str] = None, task_id: Optional[str] = None):
    """原子领取：返回该 agent 的 Run 或 None。

    先查 agent 已有 claimed/running run（幂等）；否则：
    - 传 task_id：直接认领该指定任务（按 id 领取），无视阶段，仅校验未认领；
    - 否则按关联度 + 优先级 + FIFO 找 ready 任务。
    用条件更新（WHERE state='queued'）抢占，避免 SQLite 无 FOR UPDATE 下的并发双 run。

    关联度排序（内部使用，不硬排斥）：类型匹配 > 无人认领 > 他人专属。
    即同优先级下更相关的任务排前面，但他人专属任务仍可被领到（排最后）。
    agent_type 兜底：手动 claim 不传时回查注册 agent 的 agent_type 用于排序。
    """
    existing = find_existing_run(db, agent)
    if existing:
        return existing

    if not agent_type:
        agent_type = lookup_agent_type(db, agent) or agent_type

    candidate = pick_candidate_task(db, agent_type, task_id)
    if not candidate:
        return None
    return atomic_claim(db, agent, candidate)


def find_existing_run(db, agent):
    """幂等返回：若 agent 已有 claimed/running run，直接返回。"""
    return db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()


def lookup_agent_type(db, agent):
    """回查已注册 agent 的 agent_type（claim 时类型守卫兜底）。"""
    ag = db.get(Agent, agent)
    return ag.agent_type if ag and ag.agent_type else None


def pick_candidate_task(db, agent_type, task_id):
    """选候选任务：指定 task_id 时按 id 领取；否则按关联度+优先级+FIFO 找 ready 任务。"""
    if task_id:
        task = db.get(Task, task_id)
        if not task or task.state != TaskState.QUEUED:
            return None
        return task
    q = select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
    relevance = build_relevance(agent_type)
    rows = db.exec(q.order_by(relevance, Task.priority.desc(), Task.created_at.asc())).all()
    return first_ready_row(rows)


def build_relevance(agent_type):
    """关联度排序：类型匹配 > 无人认领/fallback已到期 > 他人专属。"""
    if agent_type:
        fallback_ready = (
            Task.fallback_after.is_not(None)
            & Task.created_at.is_not(None)
            & (
                func.cast(func.strftime("%s", "now"), Integer)
                >= (
                    func.cast(func.strftime("%s", Task.created_at), Integer)
                    + Task.fallback_after
                )
            )
        )
        return case(
            (Task.target_agent_type == agent_type, 0),
            (Task.target_agent_type.is_(None), 1),
            (fallback_ready, 1),
            else_=2,
        )
    return case((Task.target_agent_type.is_(None), 0), else_=1)


def first_ready_row(rows):
    """取首个未到 run_at 时间的一次性任务。"""
    now = _now()
    for t in rows:
        if t.schedule_type == "once" and t.run_at:
            run_at = t.run_at if t.run_at.tzinfo else t.run_at.replace(tzinfo=timezone.utc)
            if run_at > now:
                continue
        return t
    return None


def atomic_claim(db, agent, candidate):
    """条件更新抢占：仅当 state 仍为 queued 才算抢到。"""
    res = db.exec(
        sa_update(Task)
        .where(Task.id == candidate.id, Task.state == TaskState.QUEUED)
        .values(state=TaskState.CLAIMED)
    )
    if res.rowcount != 1:
        db.rollback()
        return None
    task = db.get(Task, candidate.id)
    db.refresh(task)
    from mio_taskhub.transitions import record_post_claim
    claim_event = record_post_claim(task, agent)
    task.attempt += 1
    task.stage = TaskStage.IMPLEMENTING
    run = Run(
        id=str(uuid.uuid4())[:8],
        task_id=task.id,
        agent_name=agent,
        state=RunState.CLAIMED,
        attempt=task.attempt,
        started_at=_now(),
        last_heartbeat=_now(),
    )
    db.add(task)
    if claim_event:
        db.add(claim_event)
    db.add(run)
    return run
