from datetime import timezone
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select, func
from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskStage, TaskState, Run, RunState, TaskEvent
from mio_taskhub.workflow.state_machine import (
    is_terminal, State, Stage as M1Stage, LEGAL_COMBOS, is_legal_combo,
)
from mio_taskhub.dependency import task_deps, dependency_satisfied
from mio_taskhub.composite import composite_status, COMPOSITE_LABEL
from mio_taskhub.heartbeat import DEFAULT_TIMEOUT_SECONDS
from mio_taskhub.utils import _now

router = APIRouter(prefix="/board", tags=["board"])


def _stage(v):
    return v.value if not isinstance(v, str) else v


def _to_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _count_by_stage(tasks):
    counts = {s.value: 0 for s in TaskStage}
    for t in tasks:
        counts[_stage(t.stage)] += 1
    return counts


def _ready_queue(db, now):
    rows = db.exec(
        select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
        .order_by(Task.priority.desc(), Task.created_at.asc())
    ).all()
    queue = []
    for t in rows:
        run_at = _to_utc(t.run_at)
        if t.schedule_type == "once" and run_at and run_at > now:
            continue
        queue.append({
            "id": t.id, "title": t.title, "priority": t.priority,
            "target_agent_type": t.target_agent_type, "project": t.project,
            "due_at": _to_utc(t.due_at).isoformat() if t.due_at else None,
            "created_at": t.created_at.isoformat(),
        })
    return queue


def _running_tasks(db, agent):
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
    return running, active


def _check_alerts(db, tasks, active, now):
    alerts = []
    # 心跳超时
    for r in active:
        t = db.get(Task, r.task_id) if hasattr(r, 'task_id') else None
        if not t:
            continue
        timeout_sec = (t.timeout_min * 60) if t.timeout_min else DEFAULT_TIMEOUT_SECONDS
        last = _to_utc(r.last_heartbeat) or _to_utc(r.started_at)
        if last and (now - last).total_seconds() > timeout_sec:
            alerts.append({
                "level": "warning",
                "message": f"任务「{t.title}」心跳超时（{timeout_sec // 60} 分钟未上报），将被重置重领",
            })
    # 超截止时间
    overdue = [
        t for t in tasks
        if t.due_at and t.state not in (TaskState.COMPLETED, TaskState.CANCELLED)
        and (_to_utc(t.due_at) < now)
    ]
    overdue.sort(key=lambda t: t.due_at)
    for t in overdue[:5]:
        alerts.append({"level": "warning", "message": f"任务「{t.title}」已超截止时间"})
    # 依赖阻塞
    for t in tasks:
        deps = task_deps(t)
        if not deps:
            continue
        stage_v = _stage(t.stage)
        if stage_v not in ("brainstorming", "design", "planning"):
            continue
        prereqs = [db.get(Task, d) for d in deps if d]
        blocked = [p for p in prereqs if p is not None and is_terminal(p)
                   and not dependency_satisfied(p)]
        if blocked:
            alerts.append({
                "level": "warning",
                "message": f"任务「{t.title}」（{t.id}）依赖阻塞（前置「{blocked[0].title}」已取消/失败），无法放行",
            })
    alerts.extend(_ready_dependency_alerts(db, tasks))
    return alerts, overdue


def _ready_dependency_alerts(db, tasks):
    """READY 阶段的依赖盲区告警（2026-10-07）。

    既有告警只覆盖 `stage ∈ {brainstorming, design, planning}`——依赖未满足时
    `_release_dependencies` 不放行，逻辑成立。但**已经 READY 的任务不受任何门控**：
    `claim.py:68` 与 `background.py:389` 的候选查询都只判
    `state==QUEUED and stage==READY`，**不检查 `depends_on`**，因此前置未完成的
    任务同样能被领走（隔离库实测：前置 queued 时 claim 返回 200）。

    本函数**只做可见性，不改行为**：把两类问题暴露出来——
      1. 死结：前置已 CANCELLED/FAILED → 该链**永远**不会被 release
      2. 越级：前置仍在进行 → 随时可能被提前领走
    """
    out = []
    for t in tasks:
        if t.state != TaskState.QUEUED or _stage(t.stage) != "ready":
            continue
        deps = task_deps(t)
        if not deps:
            continue
        prereqs = [db.get(Task, d) for d in deps if d]
        dead = [p for p in prereqs if p is not None and is_terminal(p)
                and not dependency_satisfied(p)]
        if dead:
            out.append({
                "level": "warning",
                "message": (f"任务「{t.title}」（{t.id}）已READY 但依赖死结"
                            f"（前置「{dead[0].title}」{dead[0].state.value}）"
                            f"—— 该依赖链永远不会放行，需人工取消本任务"),
            })
            continue
        pending = [p for p in prereqs if p is not None and not dependency_satisfied(p)]
        if pending:
            out.append({
                "level": "info",
                "message": (f"任务「{t.title}」（{t.id}）已 READY 但前置"
                            f"「{pending[0].title}」未完成—— READ 阶段无依赖门控，"
                            f"可能被提前领走"),
            })
    return out


def _recent_done(db):
    done_runs = db.exec(
        select(Run).where(Run.state == RunState.FINISHED, Run.finished_at.isnot(None))
    ).all()
    done_runs.sort(key=lambda r: (_to_utc(r.finished_at).timestamp() if r.finished_at else 0), reverse=True)
    recent = []
    for r in done_runs:
        if len(recent) >= 5:
            break
        t = db.get(Task, r.task_id)
        if t and t.state == TaskState.COMPLETED:
            recent.append({
                "id": t.id, "title": t.title,
                "completed_at": _to_utc(r.finished_at).isoformat() if r.finished_at else None,
            })
    return recent


def _next_steps(ready_queue, overdue, running):
    steps = []
    if ready_queue:
        top = max(ready_queue, key=lambda x: x["priority"])
        steps.append(f"有 {len(ready_queue)} 个待领取任务，最高优先级 {top['priority']}：「{top['title']}」")
    if overdue:
        steps.append(f"有 {len(overdue)} 个任务超过截止时间未完成，建议优先处理")
    if running:
        steps.append(f"有 {len(running)} 个任务执行中，请持续关注心跳")
    if not steps:
        steps.append("当前无待办任务，可创建新任务")
    return steps


def _delivery_quality(db: Session) -> dict:
    """交付质量统计（任务 28f8fceb FR-4）：**只看，不参与任何判定**。

    聚合「agent 自报里到底有没有写测试证据」，让人能看见趋势。
    数据来源是 `Run.result` 文本 —— 即agent 自己写的，不是我们推断的。

    设计约束（重要）：
    - **纯统计**：不产生任何门控，不影响任何流程；
    - 只做比例统计，不给单个任务打分、不做优劣评判；
    - 样本太小时比例不可靠，故带 `sample_size`，由消费方决定是否展示。
    """
    from mio_taskhub.api.task_lessons import _TEST_PATTERNS
    patterns = tuple(p.lower() for p in _TEST_PATTERNS)

    # 只统计「跑完且成功」的 run —— 中断的 run 没资格参与交付质量统计
    rows = db.exec(
        select(Run)
        .where(Run.state == RunState.FINISHED)
        .order_by(Run.finished_at.desc())
        .limit(200)
    ).all()

    total = 0
    with_test = 0
    with_files = 0
    for r in rows:
        text = (r.result or "").strip()
        if not text:
            continue
        total += 1
        low = text.lower()
        if any(p in low for p in patterns):
            with_test += 1
        # 粗判是否列出了改动文件（出现路径形态的词）
        if any(k in text for k in ("修改文件", "新增文件", "改动文件", "文件：")):
            with_files += 1

    def pct(n):
        return round(n * 100.0 / total, 1) if total else 0.0

    return {
        # 样本量：小于 5 时比例基本没有参考价值，消费方宜提示「样本不足」
        "sample_size": total,
        "scanned_runs": len(rows),
        "self_reported_test_evidence": with_test,
        "self_reported_files": with_files,
        "test_evidence_rate": pct(with_test),
        "files_mentioned_rate": pct(with_files),
        "note": ("比例来自 agent 自报文本，仅供观察趋势，不参与任何流程判定；"
                 "sample_size < 5 时比例不稳定。"),
    }


@router.get("/summary")
def board_summary(agent: str = Query(None), db: Session = Depends(get_session)):
    """返回对话友好看板汇总：各阶段计数、待领取、执行中、告警、最近完成与下一步建议。"""
    now = _now()
    tasks = db.exec(select(Task)).all()

    counts = _count_by_stage(tasks)
    ready_queue = _ready_queue(db, now)
    running, active = _running_tasks(db, agent)
    alerts, overdue = _check_alerts(db, tasks, active, now)
    recent_done = _recent_done(db)
    next_steps = _next_steps(ready_queue, overdue, running)

    return {
        "updated_at": now.isoformat(),
        "counts": counts,
        "ready_queue": ready_queue,
        "running": running,
        "alerts": alerts,
        "recent_done": recent_done,
        "next_steps": next_steps,
        # 交付质量观察指标（纯统计，不参与判定；任务 28f8fceb FR-4）
        "delivery_quality": _delivery_quality(db),
    }


@router.get("/overview")
def stats_overview(db: Session = Depends(get_session)):
    """返回 M1 统计概览：composite counts、by_state、by_stage、事件分布。"""
    from mio_taskhub.workflow.transitions import _orm_to_status_state, _orm_to_status_stage

    tasks = db.exec(select(Task)).all()

    # composite counts
    composite_counts = {}
    by_state = {s.value: 0 for s in State}
    by_stage = {s.value: 0 for s in M1Stage}
    by_stage["cancelled"] = 0
    for t in tasks:
        cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
        cur_st = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
        s = _orm_to_status_state(cur_s)
        st = _orm_to_status_stage(cur_st)
        key = f"{s.value},{st.value}"
        composite_counts[key] = composite_counts.get(key, 0) + 1
        by_state[s.value] += 1
        by_stage["cancelled" if cur_st == TaskStage.CANCELLED else st.value] += 1

    # task_events 统计
    all_events = db.exec(select(TaskEvent)).all()
    events_count = len(all_events)
    event_by_type = {}
    for e in all_events:
        event_by_type[e.event_type] = event_by_type.get(e.event_type, 0) + 1

    return {
        "composite_counts": composite_counts,
        "by_state": by_state,
        "by_stage": by_stage,
        "task_events_count": events_count,
        "event_by_type": event_by_type,
        "total_tasks": len(tasks),
        # 交付质量观察指标（纯统计，不参与判定；任务 28f8fceb FR-4）
        "delivery_quality": _delivery_quality(db),
    }


# ── P1-1：终态口径全量统计（failed / cancelled 严格分离）────────────────
# 背景：决策文档 product-decision-20261020.md §4 P1-1 —— 「停止把失败清洗为取消，
# API 暴露全量统计」。此前存在三条互相冲突的口径：
#   ① metrics.py   success_rate 分母只含 COMPLETED+FAILED（cancelled 排除）
#   ② report.py    success_rate 分母含 COMPLETED+FAILED+CANCELLED
#   ③ board/overview  只给 by_state 原始计数，无成功率
# 同一个「成功率」有三个数（0.8364 / 0.0947 / 无），无法审计。
#
# 本端点给**唯一权威口径**：
#   - attempted = COMPLETED + FAILED（真正尝试过执行的）
#   - success_rate = COMPLETED / attempted（cancelled 不进分母——取消是独立终态）
#   - cancel_rate = CANCELLED / terminal_total（取消单独度量）
#   - 额外暴露 cancelled_with_failure_evidence 计数：**有失败证据却被标为取消**的
#     脏数据条数——这是 P1-1 修复前的典型症状，>0 即表示口径仍被污染。
@router.get("/terminal-stats")
def terminal_stats(db: Session = Depends(get_session)):
    """终态全量统计：failed / cancelled 严格分离，成功率口径唯一且可审计。

    与 /metrics、/observability/report 的关系：本端点是**口径定义源**，
    那两处若口径不同属实现漂移，应向本端点对齐。
    """
    from sqlalchemy import text
    stats = db.exec(text("""
        SELECT
            SUM(CASE WHEN state = 'COMPLETED' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN state = 'FAILED'    THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN state = 'CANCELLED' THEN 1 ELSE 0 END) AS cancelled,
            SUM(CASE WHEN state NOT IN ('COMPLETED','FAILED','CANCELLED') THEN 1 ELSE 0 END) AS active,
            COUNT(*) AS total_tasks,
            SUM(CASE WHEN state = 'CANCELLED' AND failed_at IS NOT NULL THEN 1 ELSE 0 END) AS cancelled_with_failure_evidence,
            SUM(CASE WHEN state = 'CANCELLED' AND cancelled_at IS NULL THEN 1 ELSE 0 END) AS cancelled_missing_timestamp
        FROM task
    """)).first()

    completed = stats[0] or 0
    failed = stats[1] or 0
    cancelled = stats[2] or 0
    active = stats[3] or 0
    total_tasks = stats[4] or 0
    washed = stats[5] or 0
    missing_ts = stats[6] or 0

    terminal_total = completed + failed + cancelled
    attempted = completed + failed
    success_rate = round(completed / attempted, 4) if attempted else None
    failure_rate = round(failed / attempted, 4) if attempted else None
    cancel_rate = round(cancelled / terminal_total, 4) if terminal_total else None

    # by_state 全量（零填充所有已知状态，便于外部核对每个状态，不受 DB 空表影响）
    # 注意：DB 存的是 enum **名**（SQLAlchemy 用 Enum.name 落库），即 'COMPLETED'，
    # 而 TaskState.value 是小写 'completed' —— 两者不同，这里按 DB 原生名零填充。
    by_state = {st.name: 0 for st in TaskState}
    for st, c in db.exec(
        text("SELECT state, COUNT(*) FROM task GROUP BY state")
    ).all():
        by_state[st] = c

    return {
        "total_tasks": total_tasks,
        "active": active,
        "terminal_total": terminal_total,
        "by_state": by_state,
        "completed": completed,
        "failed": failed,
        "cancelled": cancelled,
        "attempted": attempted,
        "success_rate": success_rate,
        "failure_rate": failure_rate,
        "cancel_rate": cancel_rate,
        "definitions": {
            "attempted": "COMPLETED + FAILED（真正尝试过执行的任务）",
            "success_rate": "COMPLETED / attempted（cancelled 不进分母）",
            "failure_rate": "FAILED / attempted",
            "cancel_rate": "CANCELLED / terminal_total（取消是独立终态，单独度量）",
        },
        "data_integrity": {
            # >0 = 仍有「有失败证据却标成取消」的脏数据，口径仍被污染
            "cancelled_with_failure_evidence": washed,
            # >0 = 有取消任务缺 cancelled_at（绕过 apply_transition 硬写 state 的痕迹）
            "cancelled_missing_timestamp": missing_ts,
            "clean": washed == 0 and missing_ts == 0,
        },
    }