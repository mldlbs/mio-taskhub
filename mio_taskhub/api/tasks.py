import uuid
from datetime import datetime, timezone
from mio_taskhub.api.claim import claim_for, should_fallback
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import (
    Task, TaskState, TaskStage, Run, RunState, Subtask, SubtaskStatus, GitRef, RefType, HistoryEvent,
    Discussion, DiscussionMessage, Agent,
)
from mio_taskhub.utils import _now
from mio_taskhub.status import normalize_depends, task_deps
from mio_taskhub.events import emit_event
from mio_taskhub.api.task_helpers import parse_dt, parse_enum, check_cycle, validate_depends
from mio_taskhub.planner import detect_cycle

router = APIRouter(prefix="/tasks", tags=["tasks"])

@router.post("", response_model=dict)
def create_task(body: dict, db: Session = Depends(get_session)):
    due_at = parse_dt(body.get("due_at"), "due_at")
    run_at = parse_dt(body.get("run_at"), "run_at")
    stage_val = body.get("stage", "brainstorming")
    try:
        stage = TaskStage(stage_val)
    except ValueError:
        raise HTTPException(400, f"invalid stage: {stage_val}")
    t = Task(
        id=str(uuid.uuid4())[:8],
        title=body.get("title", ""),
        description=body.get("description", ""),
        target_agent_type=body.get("target_agent_type"),
        fallback_after=body.get("fallback_after"),
        priority=body.get("priority", 0),
        schedule_type=body.get("schedule_type", "once"),
        run_at=run_at,
        cron_expr=body.get("cron_expr"),
        est_duration_min=body.get("est_duration_min", 30),
        depends_on=normalize_depends(body.get("depends_on")),
        max_retries=body.get("max_retries", 3),
        acceptance_criteria=body.get("acceptance_criteria", ""),
        due_at=due_at,
        labels=body.get("labels", []),
        project=body.get("project", ""),
        workspace=body.get("workspace", ""),
        files=body.get("files", []),
        deliverables=body.get("deliverables", []),
        spec_path=(body.get("spec_path") or "").strip() or None,
        plan_path=(body.get("plan_path") or "").strip() or None,
        stage=stage,
    )
    validate_depends(t, db)
    check_cycle(t, db)                       # 成环则抛 422（未 commit，自动回滚）
    db.add(t)
    event = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                       payload={"title": t.title, "stage": t.stage.value})
    db.commit()
    db.refresh(t)
    return {
        "id": t.id, "title": t.title, "state": t.state.value,
        "priority": t.priority, "created_at": t.created_at.isoformat(),
        "depends_on": task_deps(t), "idea_id": t.idea_id,
        "fallback_after": t.fallback_after,
    }

@router.get("", response_model=list)
def list_tasks(state: str = None, agent_type: str = None, stage: str = None,
               cancelled: bool = False, db: Session = Depends(get_session)):
    q = select(Task)
    if not cancelled:
        q = q.where(Task.state != TaskState.CANCELLED)
    if state:
        ts = None
        try:
            ts = TaskState[state.upper()]  # 按成员名查找（忽略大小写）
        except KeyError:
            try:
                ts = TaskState(state)      # 回退：按值查找
            except ValueError:
                ts = None
        if ts is not None:
            q = q.where(Task.state == ts)
    if stage:
        q = q.where(Task.stage == TaskStage(stage))
    if agent_type:
        q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
    rows = db.exec(q).all()
    return [
        {"id": r.id, "title": r.title, "state": r.state.value, "stage": r.stage.value,
         "priority": r.priority, "target_agent_type": r.target_agent_type,
         "fallback_after": r.fallback_after,
         "depends_on": task_deps(r), "idea_id": r.idea_id,
         "est_duration_min": r.est_duration_min,
         "project": r.project, "workspace": r.workspace}
        for r in rows
    ]

def _task_detail(t: Task, db: Session) -> dict:
    subtasks = db.exec(select(Subtask).where(Subtask.task_id == t.id).order_by(Subtask.order)).all()
    gitrefs = db.exec(select(GitRef).where(GitRef.task_id == t.id)).all()
    history = db.exec(select(HistoryEvent).where(HistoryEvent.task_id == t.id).order_by(HistoryEvent.at)).all()
    discussions = db.exec(select(Discussion).where(Discussion.task_id == t.id)).all()
    runs = db.exec(select(Run).where(Run.task_id == t.id).order_by(Run.started_at.desc())).all()
    def _fmt(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # stored naive → UTC
        return dt.isoformat()
    # 退避倒计时（秒），retrying 且有 retry_at 时计算
    retry_countdown = None
    retry_backoff = None
    if t.state == TaskState.RETRYING and t.retry_at:
        ra = t.retry_at
        if ra.tzinfo is None:
            ra = ra.replace(tzinfo=timezone.utc)
        now = _now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        retry_countdown = max(0, int((ra - now).total_seconds()))
        # 估算当前退避（2^attempt * base）
        try:
            retry_backoff = float((2 ** max(1, t.attempt)) * 2.0)
        except Exception:
            retry_backoff = None
    return {
        "id": t.id, "title": t.title, "description": t.description, "state": t.state.value,
        "priority": t.priority, "target_agent_type": t.target_agent_type,
        "schedule_type": t.schedule_type, "run_at": _fmt(t.run_at),
        "cron_expr": t.cron_expr, "est_duration_min": t.est_duration_min,
        "depends_on": task_deps(t), "max_retries": t.max_retries, "attempt": t.attempt,
        "retry_at": _fmt(t.retry_at), "retry_count": getattr(t, "retry_count", 0),
        "retry_countdown": retry_countdown, "retry_backoff_seconds": retry_backoff,
        "created_at": t.created_at.isoformat(),
        "acceptance_criteria": t.acceptance_criteria,
        "due_at": _fmt(t.due_at),
        "idea_id": t.idea_id,
        "labels": t.labels, "project": t.project, "workspace": t.workspace,
        "files": t.files, "deliverables": t.deliverables,
        "stage": t.stage.value if not isinstance(t.stage, str) else t.stage,
        "spec_path": t.spec_path,
        "plan_path": t.plan_path,
        "review_result": t.review_result,
        "fallback_after": t.fallback_after,
        "subtasks": [{"id": s.id, "order": s.order, "title": s.title, "status": s.status.value} for s in subtasks],
        "gitrefs": [{"id": g.id, "ref_type": g.ref_type.value, "value": g.value, "note": g.note} for g in gitrefs],
        "history": [{"id": h.id, "type": h.type, "payload": h.payload, "at": h.at.isoformat()} for h in history],
        "discussions": [{"id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
                         "summary": d.summary, "conclusions": d.conclusions,
                         "stage": d.stage, "started_at": d.started_at.isoformat()} for d in discussions],
        "runs": [{
            "id": r.id, "agent_name": r.agent_name,
            "state": r.state.value if not isinstance(r.state, str) else r.state,
            "attempt": r.attempt, "progress": r.progress,
            "started_at": _fmt(r.started_at), "finished_at": _fmt(r.finished_at),
            "exit_code": r.exit_code, "result": r.result,
        } for r in runs],
    }

@router.get("/{task_id}/events")
def get_task_events(task_id: str, limit: int = 50, db: Session = Depends(get_session)):
    """返回任务的 M1 TaskEvent 时间线（状态变更记录）。"""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    events = db.exec(
        select(TaskEvent).where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.created_at.desc())
        .limit(limit)
    ).all()
    def _fmt(dt):
        if dt is None: return None
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return {
        "task_id": task_id,
        "events": [{
            "id": e.id,
            "event_type": e.event_type,
            "from_state": e.from_state,
            "from_stage": e.from_stage,
            "to_state": e.to_state,
            "to_stage": e.to_stage,
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "reason": e.reason,
            "metadata": e.event_metadata,
            "created_at": _fmt(e.created_at),
        } for e in events],
    }

@router.get("/graph")
def get_full_graph(db: Session = Depends(get_session)):
    """返回全量依赖图，用于看板 DAG 预览。"""
    tasks = db.exec(select(Task)).all()
    nodes = [
        {"id": t.id, "title": t.title, "state": t.state.value,
         "stage": t.stage.value if not isinstance(t.stage, str) else t.stage,
         "priority": t.priority, "depends_on": task_deps(t)}
        for t in tasks
    ]
    by_id = {n["id"] for n in nodes}
    edges = []
    missing = []
    for n in nodes:
        for d in n["depends_on"]:
            if d in by_id:
                edges.append({"from": d, "to": n["id"]})
            else:
                missing.append({"from": d, "to": n["id"]})
    graph = {n["id"]: n["depends_on"] for n in nodes}
    cyc = detect_cycle(graph)
    return {"nodes": nodes, "edges": edges, "missing": missing,
            "has_cycle": bool(cyc), "cycle_path": cyc}

@router.get("/{task_id}/graph")
def get_task_graph(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    all_tasks = db.exec(select(Task)).all()
    by_id = {x.id: x for x in all_tasks}
    # 邻接
    succ = {x.id: [] for x in all_tasks}
    for x in all_tasks:
        for d in task_deps(x):
            if d in by_id:
                succ[d].append(x.id)
    # 上游（祖先）
    ancestors = set()
    stack = list(task_deps(t))
    while stack:
        cur = stack.pop()
        if cur in ancestors or cur not in by_id:
            continue
        ancestors.add(cur)
        stack.extend(task_deps(by_id[cur]))
    # 下游（后代）
    descendants = set()
    stack = list(succ.get(t.id, []))
    while stack:
        cur = stack.pop()
        if cur in descendants:
            continue
        descendants.add(cur)
        stack.extend(succ.get(cur, []))
    # missing
    missing = [d for d in task_deps(t) if d not in by_id]
    # sub-graph nodes
    sub_ids = ancestors | {t.id} | descendants
    nodes = []
    for tid in sub_ids:
        if tid not in by_id:
            continue
        x = by_id[tid]
        nodes.append({"id": x.id, "title": x.title, "state": x.state.value,
                      "stage": x.stage.value if not isinstance(x.stage, str) else x.stage,
                      "priority": x.priority, "depends_on": task_deps(x)})
    edges = []
    miss_edges = []
    for n in nodes:
        for d in n["depends_on"]:
            if d in by_id and d in sub_ids:
                edges.append({"from": d, "to": n["id"]})
            elif d not in by_id:
                miss_edges.append({"from": d, "to": n["id"]})
    # 环检测（针对全图）
    graph = {x.id: task_deps(x) for x in all_tasks}
    cyc = detect_cycle(graph)
    return {
        "id": t.id,
        "ancestors": sorted(ancestors),
        "descendants": sorted(descendants),
        "depends_on": task_deps(t),
        "dependents": succ.get(t.id, []),
        "missing": missing,
        "nodes": nodes,
        "edges": edges,
        "missing_edges": miss_edges,
        "has_cycle": bool(cyc),
        "cycle_path": cyc,
    }

@router.get("/status", response_model=dict)
def tasks_status_alias(agent: str = None, db: Session = Depends(get_session)):
    """GET /tasks/status 别名：返回调度队列与超时告警（复用 board_summary）。"""
    from mio_taskhub.api.board import board_summary as _bs
    return _bs(agent=agent, db=db)


@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return _task_detail(t, db)

@router.patch("/{task_id}")
def update_task(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    editable = ["title", "description", "priority", "est_duration_min", "max_retries",
                "acceptance_criteria", "due_at", "labels", "project", "workspace",
                "spec_path", "plan_path", "files", "deliverables",
                "target_agent_type", "fallback_after", "depends_on"]
    for k in editable:
        if k in body:
            v = body[k]
            if k == "due_at":
                v = parse_dt(v, "due_at")
            if k == "depends_on":
                t.depends_on = normalize_depends(v)
                validate_depends(t, db)
                check_cycle(t, db)
            else:
                setattr(t, k, v)
    db.add(t)
    event = emit_event(db, type="task_updated", entity="task", entity_id=t.id)
    db.commit()
    db.refresh(t)
    return _task_detail(t, db)

@router.post("/{task_id}/subtasks")
def add_subtask(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    st = Subtask(task_id=task_id, order=body.get("order", 0),
                 title=body.get("title", ""), status=parse_enum(SubtaskStatus, body.get("status"), "pending"))
    event = emit_event(db, type="task_subtask_added", entity="task", entity_id=task_id,
                       payload={"subtask_id": st.id, "title": st.title})
    db.add(st); db.commit(); db.refresh(st)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}

@router.patch("/{task_id}/subtasks/{sid}")
def update_subtask(task_id: str, sid: str, body: dict, db: Session = Depends(get_session)):
    st = db.get(Subtask, sid)
    if not st or st.task_id != task_id:
        raise HTTPException(404, "subtask not found")
    if "title" in body: st.title = body["title"]
    if "order" in body: st.order = body["order"]
    if "status" in body: st.status = parse_enum(SubtaskStatus, body["status"])
    event = emit_event(db, type="task_subtask_updated", entity="task", entity_id=task_id,
                       payload={"subtask_id": st.id, "status": st.status.value})
    db.add(st); db.commit(); db.refresh(st)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}

@router.post("/{task_id}/gitrefs")
def add_gitref(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    g = GitRef(task_id=task_id, ref_type=parse_enum(RefType, body.get("ref_type"), "branch"),
               value=body.get("value", ""), note=body.get("note", ""))
    event = emit_event(db, type="task_gitref_added", entity="task", entity_id=task_id,
                       payload={"gitref_id": g.id, "ref_type": g.ref_type.value})
    db.add(g); db.commit(); db.refresh(g)
    return {"id": g.id, "task_id": g.task_id, "ref_type": g.ref_type.value,
            "value": g.value, "note": g.note}

@router.post("/{task_id}/history")
def add_history(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    import json as _json
    h = HistoryEvent(task_id=task_id, type=body.get("type", ""),
                     payload=_json.dumps(body.get("payload")) if body.get("payload") is not None else None)
    event = emit_event(db, type="task_history_added", entity="task", entity_id=task_id,
                       payload={"type": h.type})
    db.add(h); db.commit(); db.refresh(h)
    return {"id": h.id, "task_id": h.task_id, "type": h.type,
            "payload": h.payload, "at": h.at.isoformat()}

@router.post("/{task_id}/discussions")
def add_discussion(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    conclusions = body.get("conclusions", "")
    status = "closed" if conclusions else "open"
    stage = body.get("stage", "brainstorming")
    d = Discussion(task_id=task_id, topic=body.get("topic", ""), agent=body.get("agent", ""),
                   status=status, summary=body.get("summary", ""), conclusions=conclusions,
                   stage=stage, ended_at=_now() if status == "closed" else None)
    db.add(d); db.commit(); db.refresh(d)
    for m in body.get("messages", []):
        db.add(DiscussionMessage(discussion_id=d.id, author=m.get("author", ""),
                                 role=m.get("role", "user"), content=m.get("content", "")))
    event = emit_event(db, type="discussion_created", entity="discussion", entity_id=d.id,
                       payload={"task_id": task_id})
    db.commit()
    return {"id": d.id, "task_id": d.task_id, "topic": d.topic, "agent": d.agent,
            "status": d.status, "summary": d.summary, "conclusions": d.conclusions,
            "stage": d.stage, "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None}

@router.get("/{task_id}/discussions")
def list_discussions(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    rows = db.exec(select(Discussion).where(Discussion.task_id == task_id).order_by(Discussion.started_at)).all()
    out = []
    for d in rows:
        msgs = db.exec(select(DiscussionMessage).where(DiscussionMessage.discussion_id == d.id).order_by(DiscussionMessage.at)).all()
        out.append({
            "id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
            "summary": d.summary, "conclusions": d.conclusions,
            "stage": d.stage, "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None,
            "messages": [{"author": m.author, "role": m.role, "content": m.content,
                          "at": m.at.isoformat()} for m in msgs],
        })
    return {"task_id": task_id, "discussions": out}

@router.delete("/{task_id}")
def cancel_task(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404)
    # M1: 走状态机校验 + 写 TaskEvent + 设时间戳
    from mio_taskhub.transitions import apply_transition
    from mio_taskhub.status import State, Stage, ActorType, IllegalTransition
    current_stage = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    try:
        _, m1_event = apply_transition(
            t, State.CANCELLED, Stage(current_stage.value),
            ActorType.USER, "api:cancel_task",
            reason="用户取消",
        )
        db.add(m1_event)
    except IllegalTransition:
        # 终态（completed, done）不允许再 cancel — 显式拒绝
        raise HTTPException(409, f"task {task_id} 不可取消（终态）")
    # 保留旧事件广播（向后兼容）
    event = emit_event(db, type="task_cancelled", entity="task", entity_id=task_id)
    db.add(t)
    db.commit()
    return {"ok": True, "state": "cancelled"}


@router.post("/{task_id}/retry")
def retry_task(task_id: str, body: dict = None, db: Session = Depends(get_session)):
    """手动重试：允许从 failed / retrying 重新进入 queued/ready。

    - failed：超限后人工重试，重置退避并重入队列（若 attempt >= max_retries 则重置 attempt 以给新预算）
    - retrying：跳过退避立即重入队列
    - 其他状态：409 不可重试
    """
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    if t.state not in (TaskState.FAILED, TaskState.RETRYING):
        raise HTTPException(409, f"task {task_id} 不可重试（当前状态 {t.state.value}，仅 failed/retrying 可重试）")
    orig_state = t.state.value
    # 若已达最大重试，手动重试视为给予新机会，重置计数
    if t.attempt >= t.max_retries:
        t.attempt = 0
        t.retry_count = 0
    t.retry_at = None
    # M1: 走状态机 T16 manual_retry → (QUEUED, READY)
    from mio_taskhub.transitions import apply_transition
    from mio_taskhub.status import State, Stage, ActorType, IllegalTransition
    current_stage = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    try:
        _, m1_event = apply_transition(
            t, State.QUEUED, Stage.READY,
            ActorType.USER, "api:retry_task",
            reason=f"manual_retry from {orig_state}",
        )
        db.add(m1_event)
    except IllegalTransition as e:
        raise HTTPException(409, f"retry 非法: {e}")
    event = emit_event(db, type="task_retry_manual", entity="task", entity_id=t.id,
                       payload={"from_state": orig_state, "attempt": t.attempt, "max_retries": t.max_retries})
    db.add(t)
    db.commit()
    db.refresh(t)
    requeued = emit_event(db, type="task_retry_requeued", entity="task", entity_id=t.id,
                          payload={"reason": "manual_retry", "attempt": t.attempt, "from": orig_state})
    db.commit()
    return {"id": t.id, "state": t.state.value, "stage": t.stage.value,
            "attempt": t.attempt, "max_retries": t.max_retries, "retry_at": None}

def _apply_stage_requirements(t: Task, dst: TaskStage, body: dict, strict: bool = True):
    """校验目标阶段的产出物并设置辅助字段。抛 HTTPException(422) 当产出物缺失。

    design 需 spec_path、planning 需 plan_path、done 需 review_result。
    注意：本函数 **不修改** task.state / task.stage —— 状态变更由调用方通过
    apply_transition() 统一处理。
    strict=False 时（拖拽轻量路径）不强制产出物，仅在提供时记录，
    done 缺 review_result 时自动补默认结论。
    """
    if dst == TaskStage.DESIGN:
        if body.get("spec_path"):
            t.spec_path = body["spec_path"]
        elif strict and not t.spec_path:
            raise HTTPException(422, "design stage requires spec_path")
    if dst == TaskStage.PLANNING:
        if body.get("plan_path"):
            t.plan_path = body["plan_path"]
        elif strict and not t.plan_path:
            raise HTTPException(422, "planning stage requires plan_path")
    if dst == TaskStage.DONE:
        review = body.get("review_result") or t.review_result or "（拖拽完成）"
        if strict and not (body.get("review_result") or t.review_result):
            raise HTTPException(422, "done stage requires review_result")
        t.review_result = review

@router.post("/{task_id}/stage")
def advance_stage(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    target = body.get("target_stage")
    if not target:
        raise HTTPException(422, "target_stage is required")
    try:
        dst = TaskStage(target)
    except ValueError:
        raise HTTPException(400, f"invalid target_stage: {target}")
    src = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    if not TaskStage.can_advance(src, dst):
        raise HTTPException(400, f"cannot advance from {src.value} to {dst.value}")
    if dst == TaskStage.DESIGN:
        discussions = db.exec(select(Discussion).where(Discussion.task_id == task_id)).all()
        if not discussions:
            raise HTTPException(422, "design stage requires at least one discussion record")
    _apply_stage_requirements(t, dst, body)
    # M1: 走状态机记录 TaskEvent + 设时间戳（不预设 t.stage，由 apply_transition 写入）
    from mio_taskhub.transitions import apply_transition, _orm_to_status_state, _orm_to_status_stage
    from mio_taskhub.status import State, Stage as M1Stage, ActorType, IllegalTransition as M1Illegal
    m1_events = []
    try:
        cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
        from_st = _orm_to_status_stage(src)
        if dst == TaskStage.DONE:
            # 先确保 state=COMPLETED（T5: queued/claimed,review → completed,review）
            if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
                _, e1 = apply_transition(
                    t, State.COMPLETED, from_st,
                    ActorType.USER, "api:advance_stage",
                    reason="advance→done: review pass",
                )
                if e1: m1_events.append(e1)
            # T6: (completed, review) → (completed, done)
            _, e2 = apply_transition(
                t, State.COMPLETED, M1Stage.DONE,
                ActorType.SYSTEM, "auto:finalize",
                reason="T6 finalize",
            )
            if e2: m1_events.append(e2)
        elif dst == TaskStage.CANCELLED:
            _, e = apply_transition(
                t, State.CANCELLED, from_st,
                ActorType.USER, "api:advance_stage",
                reason="advance→cancelled",
            )
            if e: m1_events.append(e)
        else:
            # T17 (from queued) or T11 (from claimed)：stage-only advance
            actor = ActorType.USER if cur_s == TaskState.QUEUED else ActorType.SYSTEM
            actor_id = "api:advance_stage" if cur_s == TaskState.QUEUED else "auto:advance"
            _, e = apply_transition(
                t, _orm_to_status_state(cur_s),
                M1Stage(dst.value), actor, actor_id,
                reason=f"advance {src.value}→{dst.value}",
            )
            if e: m1_events.append(e)
    except M1Illegal as exc:
        raise HTTPException(400, f"state machine rejected advance: {exc}")
    event = emit_event(db, type="task_stage", entity="task", entity_id=task_id,
                       payload={"target": dst.value})
    db.add(t)
    for ev in m1_events:
        db.add(ev)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "review_result": t.review_result,
            "state": t.state.value}

@router.post("/{task_id}/stage/move")
def move_to_stage(task_id: str, body: dict, db: Session = Depends(get_session)):
    """任意跳转到目标阶段（拖拽用）。不校验相邻性，但保留终态保护与产出物校验。

    与 advance_stage 的区别：不要求相邻推进，且 design 阶段不强制要求讨论记录
    （拖拽是轻量路径，产物可由用户自行补充）。所有移动通过 apply_transition 记录。
    """
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    target = body.get("target_stage")
    if not target:
        raise HTTPException(422, "target_stage is required")
    try:
        dst = TaskStage(target)
    except ValueError:
        raise HTTPException(400, f"invalid target_stage: {target}")
    src = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    if src in (TaskStage.DONE, TaskStage.CANCELLED):
        raise HTTPException(400, f"cannot move terminal stage {src.value}")
    _apply_stage_requirements(t, dst, body, strict=False)
    # 同阶段移动：仅更新元数据，不做状态变更
    if src == dst:
        db.add(t)
        db.commit()
        db.refresh(t)
        return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
                "plan_path": t.plan_path, "review_result": t.review_result,
                "state": t.state.value}
    # M1: 全部走状态机（含 T18 自由拖拽）
    from mio_taskhub.transitions import apply_transition, _orm_to_status_state, _orm_to_status_stage
    from mio_taskhub.status import State as M1State, Stage as M1Stage, ActorType as M1Actor
    m1_events = []
    cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
    cur_m1 = _orm_to_status_state(cur_s)
    to_st = _orm_to_status_stage(dst)
    if dst == TaskStage.DONE:
        # 先确保 state=COMPLETED（T5: queued/claimed,review → completed,review）
        if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
            from_st_done = _orm_to_status_stage(src)
            _, e1 = apply_transition(t, M1State.COMPLETED, from_st_done,
                                     M1Actor.USER, "api:move_to_stage",
                                     reason="move→done")
            if e1: m1_events.append(e1)
        # T6: (completed, review) → (completed, done)
        _, e2 = apply_transition(t, M1State.COMPLETED, M1Stage.DONE,
                                 M1Actor.SYSTEM, "auto:finalize",
                                 reason="move→done: finalize")
        if e2: m1_events.append(e2)
    elif dst == TaskStage.CANCELLED:
        from_st = _orm_to_status_stage(src)
        _, e = apply_transition(t, M1State.CANCELLED, from_st,
                                M1Actor.USER, "api:move_to_stage",
                                reason="move→cancelled")
        if e: m1_events.append(e)
    else:
        # T18: 自由拖拽 stage-only（validate_transition 内 fallback）
        actor = M1Actor.USER if cur_s == TaskState.QUEUED else M1Actor.SYSTEM
        actor_id = "api:move_to_stage" if cur_s == TaskState.QUEUED else "auto:move"
        _, e = apply_transition(t, cur_m1, to_st, actor, actor_id,
                                reason=f"move {src.value}→{dst.value}")
        if e: m1_events.append(e)
    event = emit_event(db, type="task_moved", entity="task", entity_id=t.id,
                       payload={"from": src.value, "to": dst.value})
    db.add(t)
    for ev in m1_events:
        db.add(ev)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "review_result": t.review_result,
            "state": t.state.value}

@router.post("/claim")
def claim_task(agent: str = Query(...), agent_type: str = Query(None),
               task_id: str = Query(None), project: str = Query(None), workspace: str = Query(None),
               files: str = Query(None), db: Session = Depends(get_session)):
    existing = db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()
    if existing:
        return {"id": existing.id, "task_id": existing.task_id, "state": existing.state.value,
                "agent_name": existing.agent_name}
    # 回查 agent_type：手动 claim 不传时，用注册信息兜底，保证类型守卫一致
    if not agent_type:
        ag = db.get(Agent, agent)
        if ag and ag.agent_type:
            agent_type = ag.agent_type
    run = claim_for(agent, db, agent_type, task_id)
    if run is None:
        db.rollback()
        if task_id:
            t = db.get(Task, task_id)
            if not t:
                return Response(status_code=404, content=f"task {task_id} not found")
            if t.state != TaskState.QUEUED:
                return Response(status_code=409, content=f"task {task_id} 不可领取（当前状态 {t.state.value}）")
        return Response(status_code=204)
    task = db.get(Task, run.task_id)  # claim_for 已更新 attempt/stage，勿再 refresh
    if project and not task.project:
        task.project = project
    if workspace and not task.workspace:
        task.workspace = workspace
    if files and not task.files:
        task.files = [f.strip() for f in files.split(",") if f.strip()]
    event = emit_event(db, type="task_claimed", entity="task", entity_id=task.id,
                       run_id=run.id, payload={"agent": agent, "attempt": task.attempt})
    db.add(task)
    db.commit()
    db.refresh(run)
    return {"id": run.id, "task_id": run.task_id, "state": run.state.value,
            "agent_name": run.agent_name, "attempt": run.attempt}

