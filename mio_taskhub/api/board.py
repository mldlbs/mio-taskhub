from datetime import timezone
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskStage, TaskState, Run, RunState
from mio_taskhub.status import is_terminal, task_deps
from mio_taskhub.utils import _now

router = APIRouter(prefix="/board", tags=["board"])

DEFAULT_TIMEOUT_SECONDS = 120


def _stage(v):
    return v.value if not isinstance(v, str) else v


def _to_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get("/summary")
def board_summary(agent: str = Query(None), db: Session = Depends(get_session)):
    """返回对话友好看板汇总：各阶段计数、待领取、执行中、告警、最近完成与下一步建议。"""
    now = _now()

    tasks = db.exec(select(Task)).all()
    counts = {s.value: 0 for s in TaskStage}
    for t in tasks:
        counts[_stage(t.stage)] += 1

    # 待领取队列（ready + 已到 run_at）
    rows = db.exec(
        select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
        .order_by(Task.priority.desc(), Task.created_at.asc())
    ).all()
    ready_queue = []
    for t in rows:
        run_at = _to_utc(t.run_at)
        if t.schedule_type == "once" and run_at and run_at > now:
            continue
        ready_queue.append({
            "id": t.id, "title": t.title, "priority": t.priority,
            "target_agent_type": t.target_agent_type, "project": t.project,
            "due_at": _to_utc(t.due_at).isoformat() if t.due_at else None,
            "created_at": t.created_at.isoformat(),
        })

    # 执行中（agent 过滤）
    active = db.exec(
        select(Run).where(Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).all()
    running = []
    for r in active:
        t = db.get(Task, r.task_id)
        if not t:
            continue
        if agent and r.agent_name != agent:
            continue
        running.append({
            "task_id": t.id, "title": t.title, "stage": _stage(t.stage),
            "claimed_by": r.agent_name, "run_id": r.id, "progress": r.progress,
            "heartbeat_at": _to_utc(r.last_heartbeat).isoformat() if r.last_heartbeat else None,
        })

    # 告警：心跳超时（全量，不受 agent 过滤影响）
    alerts = []
    for r in active:
        t = db.get(Task, r.task_id)
        if not t:
            continue
        timeout_sec = (t.timeout_min * 60) if t.timeout_min else DEFAULT_TIMEOUT_SECONDS
        last = _to_utc(r.last_heartbeat) or _to_utc(r.started_at)
        if last and (now - last).total_seconds() > timeout_sec:
            alerts.append({
                "level": "warning",
                "message": f"任务「{t.title}」心跳超时（{timeout_sec // 60} 分钟未上报），将被重置重领",
            })

    # 告警：超截止时间
    overdue = [
        t for t in tasks
        if t.due_at and t.state not in (TaskState.COMPLETED, TaskState.CANCELLED)
        and (_to_utc(t.due_at) < now)
    ]
    overdue.sort(key=lambda t: t.due_at)
    for t in overdue[:5]:
        alerts.append({"level": "warning", "message": f"任务「{t.title}」已超截止时间"})

    # 告警：依赖阻塞（前置已 cancelled/failed，不可能放行）
    for t in tasks:
        deps = task_deps(t)
        if not deps:
            continue
        stage_v = _stage(t.stage)
        if stage_v in ("done", "cancelled", "review", "implementing"):
            continue
        prereqs = [db.get(Task, d) for d in deps if d]
        blocked = [p for p in prereqs if p is not None and is_terminal(p)
                   and p.state.value != "completed" and _stage(p.stage) != "done"]
        if blocked:
            alerts.append({
                "level": "warning",
                "message": f"任务「{t.title}」（{t.id}）依赖阻塞（前置「{blocked[0].title}」已取消/失败），无法放行",
            })

    # 最近完成
    done_runs = db.exec(
        select(Run).where(Run.state == RunState.FINISHED, Run.finished_at.isnot(None))
    ).all()
    done_runs.sort(key=lambda r: (_to_utc(r.finished_at).timestamp() if r.finished_at else 0), reverse=True)
    recent_done = []
    for r in done_runs:
        if len(recent_done) >= 5:
            break
        t = db.get(Task, r.task_id)
        if t and t.state == TaskState.COMPLETED:
            recent_done.append({
                "id": t.id, "title": t.title,
                "completed_at": _to_utc(r.finished_at).isoformat() if r.finished_at else None,
            })

    # 下一步建议
    next_steps = []
    if ready_queue:
        top = max(ready_queue, key=lambda x: x["priority"])
        next_steps.append(f"有 {len(ready_queue)} 个待领取任务，最高优先级 {top['priority']}：「{top['title']}」")
    if overdue:
        next_steps.append(f"有 {len(overdue)} 个任务超过截止时间未完成，建议优先处理")
    if running:
        next_steps.append(f"有 {len(running)} 个任务执行中，请持续关注心跳")
    if not next_steps:
        next_steps.append("当前无待办任务，可创建新任务")

    return {
        "updated_at": now.isoformat(),
        "counts": counts,
        "ready_queue": ready_queue,
        "running": running,
        "alerts": alerts,
        "recent_done": recent_done,
        "next_steps": next_steps,
    }