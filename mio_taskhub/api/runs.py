from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Run, RunState, Task, TaskEvent, TaskState, TaskStage
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event
from mio_taskhub.workflow.transitions import apply_transition, _orm_to_status_stage
from mio_taskhub.workflow.state_machine import State, Stage, ActorType, IllegalTransition as M1Illegal

router = APIRouter(prefix="/runs", tags=["runs"])


# ── Run lifecycle ──────────────────────────────────────────────────────

def find_existing_run(db, agent):
    """Idempotent: return existing claimed/running run for agent, or None."""
    return db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()


def create_run(db, agent, task):
    """Create a new Run for the claimed task. Returns the Run object."""
    import uuid as _uuid
    run = Run(
        id=str(_uuid.uuid4())[:8],
        task_id=task.id,
        agent_name=agent,
        state=RunState.CLAIMED,
        attempt=task.attempt,
        started_at=_now(),
        last_heartbeat=_now(),
    )
    db.add(run)
    return run


# 指数退避基数（秒），可按需调整；失败后等待 2^attempt * BASE 秒后重入队列
BASE_RETRY_SECONDS = 2.0


def _backoff_seconds(attempt: int, base: float = BASE_RETRY_SECONDS) -> float:
    try:
        return float((2 ** max(1, attempt)) * base)
    except Exception:
        return float(base)


def _retry_at_for(task) -> timedelta:
    # task.attempt 为已执行的次数（claim 时 +1），退避基于当前 attempt
    secs = _backoff_seconds(task.attempt)
    return timedelta(seconds=secs)


def _safe_transition(task, to_state, to_stage, actor_type, actor_id, reason="", metadata=None):
    """走 M1 状态机；非法时返回 (None, None) 不抛（保持旧行为兼容）。"""
    try:
        from_stage = _orm_to_status_stage(task.stage)
        _, ev = apply_transition(
            task, to_state, to_stage,
            actor_type, actor_id, reason=reason, metadata=metadata,
        )
        return _, ev
    except M1Illegal:
        return None, None


# 看门狗判死时写入的 reason 前缀（background._on_timeout）。
_SYSTEM_TIMEOUT_REASON_PREFIXES = ("agent_offline:", "heartbeat_timeout:")


def _is_system_timeout_failure(db, task) -> bool:
    """任务当前 FAILED 是否源于看门狗超时判死。

    只有这类失败允许被「迟到但成功」的 run 结果纠正。agent / user 主动报的
    失败必须人工复核，不能被一个迟到的 result 悄悄翻案。
    """
    if task.state != TaskState.FAILED:
        return False
    last = db.exec(
        select(TaskEvent)
        .where(TaskEvent.task_id == task.id)
        .order_by(TaskEvent.created_at.desc())
    ).first()
    if last is None:
        return False
    if (last.to_state or "") != State.FAILED.value:
        return False
    if (last.actor_type or "") != ActorType.SYSTEM.value:
        return False
    return (last.reason or "").startswith(_SYSTEM_TIMEOUT_REASON_PREFIXES)


@router.post("/{run_id}/heartbeat")
def heartbeat(run_id: str, body: dict = None, db: Session = Depends(get_session)):
    body = body or {}
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404)
    run.state = RunState.RUNNING
    run.last_heartbeat = _now()
    if "progress" in body:
        run.progress = body["progress"]
    if "checkpoint" in body:
        run.checkpoint = body["checkpoint"]
    task = db.get(Task, run.task_id)
    m1_event = None
    if task and task.state == TaskState.CLAIMED:
        # M1: T2 (claimed, implementing) → (running, implementing)
        _, m1_event = _safe_transition(
            task, State.RUNNING, Stage.IMPLEMENTING,
            ActorType.AGENT, run.agent_name,
            reason="heartbeat",
        )
    event = emit_event(db, type="heartbeat", entity="run", entity_id=run.id,
                       run_id=run.id, payload={"progress": run.progress})
    db.add(run)
    if task:
        db.add(task)
    if m1_event is not None:
        db.add(m1_event)
    db.commit()
    db.refresh(run)
    return {"id": run.id, "state": run.state.value, "progress": run.progress}


def _read_gate_detail(gate):
    """把 Read Evidence 门控结果压成紧凑的 422 detail（MCP 端截断 200 字符，尽量小）。"""
    return {
        "message": "Read Evidence 门控：本次 run 未满足必读要求，禁止提交成功结果",
        "required_reads": gate["required_reads"],
        "missing": gate["missing"],
        "stale": gate["stale"],
        "hint": ("先 taskhub_read_document(task_id, kind, run_id=...) 读取 missing 的文档、"
                 "重读 stale（文档已被改动）的文档，再 submit。"),
    }


@router.get("/{run_id}/read-evidence")
def read_evidence_status(run_id: str, db: Session = Depends(get_session)):
    """查看本次 run 的 Read Evidence 是否满足 submit 前置条件。"""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404)
    task = db.get(Task, run.task_id)
    if task is None:
        return {"run_id": run_id, "task_id": run.task_id, "required_reads": [],
                "read_ok": [], "missing": [], "stale": [],
                "enforced": False, "passed": True}
    from mio_taskhub.read_evidence import check_read_gate
    return check_read_gate(db, task, run_id)


@router.post("/{run_id}/result")
def submit_result(run_id: str, body: dict, db: Session = Depends(get_session)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404)
    success = body.get("success", True)
    task = db.get(Task, run.task_id)
    # Read Evidence 前置门控：成功提交前，本次 run 必须读过 required 文档（版本一致）。
    if success and task is not None:
        from mio_taskhub.read_evidence import check_read_gate
        gate = check_read_gate(db, task, run_id)
        if gate["enforced"] and not gate["passed"]:
            raise HTTPException(422, detail=_read_gate_detail(gate))
    run.result = body.get("result", "")
    run.exit_code = body.get("exit_code", 0 if success else 1)
    run.finished_at = _now()
    run.state = RunState.FINISHED
    run.progress = 100
    task_state = None
    payload = {"success": success}
    m1_events = []
    if task:
        if success:
            task_state, payload = _handle_success(task, run, m1_events, db)
        else:
            task_state, payload = _handle_failure(task, run, m1_events, payload)
        db.add(task)
    else:
        payload["state"] = None
    event = emit_event(db, type="task_result", entity="task", entity_id=run.task_id,
                       run_id=run.id, payload=payload)
    db.add(run)
    for ev in m1_events:
        db.add(ev)
    extra = _emit_retry_event_if_needed(db, task, task_state, run, payload)
    db.commit()
    db.refresh(run)
    db.refresh(task)
    if task_state == "retrying":
        return {"id": run.id, "state": run.state.value, "result": run.result,
                "task_state": task_state, "retry_at": task.retry_at.isoformat(),
                "backoff_seconds": payload.get("backoff_seconds")}
    return {"id": run.id, "state": run.state.value, "result": run.result, "task_state": task_state}


def _handle_success(task, run, m1_events, db=None):
    """成功路径：claimed→running→completed→review。

    额外兜底：任务若已因看门狗超时被判 FAILED，而 agent 随后正常提交了成功
    结果（2026-09-17 实测 12 例），改判回 COMPLETED —— 否则 FAILED 是终态，
    成功结果无法回写，库里会留下 task=FAILED + run=成功/exit0 的矛盾态。
    """
    if task.state == TaskState.CLAIMED:
        _, e0 = _safe_transition(task, State.RUNNING, Stage.IMPLEMENTING,
                                 ActorType.AGENT, run.agent_name, reason="auto-start before submit")
        if e0: m1_events.append(e0)

    recovered = False
    if task.state == TaskState.FAILED:
        if db is None or not _is_system_timeout_failure(db, task):
            return "failed", {
                "success": True, "state": "failed", "recovered": False,
                "note": "task 已是 FAILED 且非系统超时判死，拒绝迟到的成功结果",
            }
        to_stage = Stage.REVIEW if _orm_to_status_stage(task.stage) == Stage.REVIEW else Stage.IMPLEMENTING
        _, er = _safe_transition(
            task, State.COMPLETED, to_stage,
            ActorType.SYSTEM, "auto:late_result_recovery",
            reason="late_result_recovery: 超时判死后 agent 正常提交成功结果",
        )
        if er: m1_events.append(er)
        recovered = task.state == TaskState.COMPLETED

    if task.state != TaskState.COMPLETED:
        _, e1 = _safe_transition(task, State.COMPLETED, Stage.IMPLEMENTING,
                                 ActorType.AGENT, run.agent_name, reason="submit_result success")
        if e1: m1_events.append(e1)
    task.retry_at = None
    _, e2 = _safe_transition(task, State.COMPLETED, Stage.REVIEW,
                             ActorType.SYSTEM, "auto:send-to-review", reason="T4")
    if e2: m1_events.append(e2)
    payload = {"success": True, "state": "completed"}
    if recovered:
        payload["recovered_from_failed"] = True
    return "completed", payload


def _handle_failure(task, run, m1_events, payload):
    """失败路径：重试或失败，带指数退避。"""
    if task.state == TaskState.CLAIMED:
        _, e0 = _safe_transition(task, State.RUNNING, Stage.IMPLEMENTING,
                                 ActorType.AGENT, run.agent_name, reason="auto-start before fail")
        if e0: m1_events.append(e0)
    if task.attempt < task.max_retries:
        if task.max_retries == 0:
            return _fail_permanently(task, run, m1_events)
        return _fail_with_retry(task, run, m1_events, payload)
    return _fail_permanently(task, run, m1_events)


def _fail_permanently(task, run, m1_events):
    """永久失败（max_retries=0 或已耗尽）。"""
    _, e = _safe_transition(task, State.FAILED, Stage.IMPLEMENTING,
                            ActorType.AGENT, run.agent_name, reason="submit_result fail (max reached)")
    if e: m1_events.append(e)
    task.retry_at = None
    return "failed", {"success": False, "state": "failed", "attempt": task.attempt, "max_retries": task.max_retries}


def _fail_with_retry(task, run, m1_events, payload):
    """失败后重试（指数退避）。"""
    _, e1 = _safe_transition(task, State.FAILED, Stage.IMPLEMENTING,
                             ActorType.AGENT, run.agent_name, reason="submit_result fail")
    if e1: m1_events.append(e1)
    task.retry_count = (task.retry_count or 0) + 1
    backoff = _retry_at_for(task)
    task.retry_at = _now() + backoff
    _, e2 = _safe_transition(task, State.RETRYING, Stage.IMPLEMENTING,
                             ActorType.SYSTEM, "auto:retry", reason="T9")
    if e2: m1_events.append(e2)
    return "retrying", {
        "success": False, "state": "retrying",
        "attempt": task.attempt, "max_retries": task.max_retries,
        "retry_at": task.retry_at.isoformat(), "backoff_seconds": backoff.total_seconds(),
    }


def _emit_retry_event_if_needed(db, task, task_state, run, payload):
    """如果是 retrying 状态，发送额外的 retry_scheduled 事件。"""
    if task_state == "retrying":
        extra = emit_event(db, type="task_retry_scheduled", entity="task", entity_id=task.id,
                           run_id=run.id, payload={
                               "attempt": task.attempt, "max_retries": task.max_retries,
                               "retry_at": task.retry_at.isoformat(),
                               "backoff_seconds": payload.get("backoff_seconds"),
                           })
        db.add(extra)
        return extra
    return None
