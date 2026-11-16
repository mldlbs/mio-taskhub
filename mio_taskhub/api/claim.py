from typing import Optional
import os
from datetime import timezone
from sqlmodel import Session, select
from sqlalchemy import case, Integer, func
from sqlalchemy import update as sa_update
from mio_taskhub.models import Task, TaskState, TaskStage, Agent, AgentStatus
from mio_taskhub.api.runs import find_existing_run, create_run
from mio_taskhub.hierarchy import claimable_task_clause, is_main
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

def claim_for(agent: str, db: Session, agent_type: Optional[str] = None,
              task_id: Optional[str] = None, project_scope: Optional[str] = None):
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

    # 2026-11-16（事故 b139ecec 后用户拍板）：claim 本身就是存活证据——发起领取的进程
    # 此刻是活的，所以 claim 时自动把 agent 置回 online（落 agent_online(claim_revive)
    # 事件留痕），避免「空闲 180s 即离线 → 所有 claim 被静默拒绝」的死结。
    # 代价：防僵尸（T4.3/P1-OBS）后退一步——真僵尸 agent 如果还会循环 claim，也会领到
    # 任务，浪费一轮 attempt；由 max_retries / bounce 上限兜底。
    # 要恢复旧的严格行为（offline 即拒），设 MIO_CLAIM_REVIVE_OFFLINE_AGENT=0。
    # 未注册 agent（盲领，INV-1）仍放行——不查表即不存在记录。
    ag = db.get(Agent, agent)
    if ag is not None and ag.status != AgentStatus.ONLINE:
        if os.environ.get("MIO_CLAIM_REVIVE_OFFLINE_AGENT", "1").lower() in ("0", "false", "off"):
            return None
        from mio_taskhub.db import engine as _engine
        from mio_taskhub.events import emit_event as _emit
        with Session(_engine) as s2:
            a2 = s2.get(Agent, agent)
            if a2 is None:
                return None
            a2.status = AgentStatus.ONLINE
            a2.last_heartbeat = _now()
            ev = _emit(s2, type="agent_online", entity="agent", entity_id=agent,
                       payload={"reason": "claim_revive",
                                "previous_status": str(getattr(ag.status, "value", ag.status))})
            s2.add(a2)
            s2.commit()
            try:
                from mio_taskhub.events import broadcast_for_event
                broadcast_for_event(ev)
            except Exception:  # noqa: BLE001 — 广播失败不影响领取
                pass

    if not agent_type:
        agent_type = lookup_agent_type(db, agent) or agent_type

    candidate = pick_candidate_task(db, agent_type, task_id, project_scope)
    if not candidate:
        return None
    return atomic_claim(db, agent, candidate)


def lookup_agent_type(db, agent):
    """回查已注册 agent 的 agent_type（claim 时类型守卫兜底）。"""
    ag = db.get(Agent, agent)
    return ag.agent_type if ag and ag.agent_type else None


def pick_candidate_task(db, agent_type, task_id, project_scope: Optional[str] = None):
    """选候选任务：指定 task_id 时按 id 领取；否则按关联度>优先级>FIFO 选 ready 任务。

    project_scope（修复 0fe91462）：**领取范围过滤**——非空时只在该项目范围内挑选，
    支持逗号分隔多值（与 idle_worker --project 一致）。注意与 `project`（领取上下文，
    用于回填 Task.project）语义不同，不可混用。
    """
    if task_id:
        task = db.get(Task, task_id)
        if not task or task.state != TaskState.QUEUED:
            return None
        # P2a：主任务不是执行单元，即使点名也不能被普通 worker 领走。
        if is_main(task):
            return None
        if not _claim_ready(db, task):
            return None
        return task
    q = select(Task).where(
        Task.state == TaskState.QUEUED,
        Task.stage == TaskStage.READY,
        claimable_task_clause(),
    )
    if project_scope:
        scopes = [p.strip() for p in project_scope.split(",") if p.strip()]
        if scopes:
            q = q.where(Task.project.in_(scopes))
    relevance = build_relevance(agent_type)
    rows = db.exec(q.order_by(relevance, Task.priority.desc(), Task.created_at.asc())).all()
    return first_ready_row(rows, db)


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


def _claim_ready(db, task) -> bool:
    """Phase 1：block 模式下跳过 readiness 不满足的任务。"""
    try:
        from mio_taskhub.completion_gate import build_readiness, claim_gate_mode
        if claim_gate_mode() != "block":
            return True
        return bool(build_readiness(db, task).get("ready"))
    except Exception:
        return True


def first_ready_row(rows, db=None):
    """取首个未到 run_at 时间的一次性任务，并跳过未就绪任务。"""
    now = _now()
    for t in rows:
        if t.schedule_type == "once" and t.run_at:
            run_at = t.run_at if t.run_at.tzinfo else t.run_at.replace(tzinfo=timezone.utc)
            if run_at > now:
                continue
        if db is not None and not _claim_ready(db, t):
            continue
        return t
    return None


def atomic_claim(db, agent, candidate):
    """条件更新抢占：仅当 state 仍为 queued 才算抢到。"""
    res = db.exec(
        sa_update(Task)
        .where(
            Task.id == candidate.id,
            Task.state == TaskState.QUEUED,
            # P2a：CAS 与候选过滤同源，防止选到后被改成 main 仍被抢走。
            claimable_task_clause(),
        )
        .values(state=TaskState.CLAIMED)
    )
    if res.rowcount != 1:
        db.rollback()
        return None
    task = db.get(Task, candidate.id)
    db.refresh(task)
    from mio_taskhub.workflow.transitions import record_post_claim
    claim_event = record_post_claim(task, agent)
    task.attempt += 1
    task.stage = TaskStage.IMPLEMENTING
    run = create_run(db, agent, task)
    db.add(task)
    if claim_event:
        db.add(claim_event)
    return run
