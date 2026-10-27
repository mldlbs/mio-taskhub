import logging
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from sqlalchemy import update as sa_update
from mio_taskhub.db import get_session
from mio_taskhub.models import Run, RunState, Task, TaskEvent, TaskState, TaskStage, Agent, AgentStatus
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event
from mio_taskhub.workflow.transitions import apply_transition, _orm_to_status_stage
from mio_taskhub.workflow.state_machine import State, Stage, ActorType, IllegalTransition as M1Illegal

logger = logging.getLogger("runs")

router = APIRouter(prefix="/runs", tags=["runs"])


# ── Run ownership guards (P0 d1b54de0) ─────────────────────────────────
# 背景：heartbeat / submit_result 原先只按 run_id 取Run 就写，**不校验调用方
# 是否是该 run 的持有者**——任何拿到 run_id 的调用方都能冒充他人 agent 把 run
# 改成 running（续命）或提交结果（把他人任务标记 completed / 触发重试）。
#
# 设计取舍：`agent` 为**可选**参数。
#   - 传了→ 严格校验所有权，不匹配 403（越权被拒）
#   - 没传 → 维持修复前行为（不破坏存量调用方与既有测试）
# 仓内调用方（packaging/agent_wrapper.py、packaging/idle_worker.py、
# mcp_server.py 的 taskhub_heartbeat / taskhub_submit_result）均已持有 agent
# 变量，应在接入时补传；外部 mio-agent-runtime 无引用（已 grep 确认）。
#
# 未传 agent 时的残余风险：仍可凭 run_id 越权。这是**已知限制**，不是修复完成态；
# 彻底关闭需要 run 级凭证（claim 时下发 token），列入后续任务。

def assert_run_owner(run: Run, agent: Optional[str]) -> None:
    """校验调用方是该 run 的持有者。agent 为空则跳过（兼容旧调用方）。"""
    if not agent:
        return
    if run.agent_name != agent:
        raise HTTPException(403, detail={
            "code": "run.not_owner",
            "message": f"run {run.id} 属于 agent '{run.agent_name}'，当前 agent '{agent}' 无权操作",
            "owner": run.agent_name,
        })


def assert_run_submittable(run: Run, task=None) -> None:
    """提交结果的状态前置校验（所有权之后）。

    注意：这里**只拦「重复的成功提交」**，不拦「迟到的结果」。项目既有能力
    （`_handle_success` 的 late_result_recovery，2026-09-17 实测 12 例）要求：
    reaper 判超时把 run 置finished 后，agent 迟到的提交仍返回 200，由业务层
    依据 `_is_system_timeout_failure` 决定是否纠正——所以不能简单地
    「finished 就拒绝」，否则会打断这条既有链路（tests/test_timeout_misjudge.py
    的三个用例正是它的护栏）。

    被拒的唯一情形：**成功结果已交接**（run 已 finished 且 `exit_code == 0`
    且任务已 completed），再交一次会二次触发任务状态流转。

    判据**不能**用 `progress == 100`：`submit_result` 对成功与失败**都**会把
    progress 置 100，用它做判据会把「失败后迟到的成功」误判成重复提交——
    而那恰恰是 late_result_recovery 必须放行的场景。
    """
    if run.state != RunState.FINISHED:
        return
    already_succeeded = (
        run.exit_code == 0
        and task is not None
        and task.state == TaskState.COMPLETED
    )
    if already_succeeded:
        raise HTTPException(409, detail={
            "code": "run.result_already_submitted",
            "message": f"run {run.id} 的成功结果已提交（任务已完成），不接受重复提交",
            "state": run.state.value,
        })


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
    # 窄门禁（d468e633 FR-1）：run 创建时拍「前」快照。
    # 必须在 run 创建时拍 —— 若等到 submit 才拍，无法区分「本次 run 的改动」
    # 与「run 开始前工作区就有的脏」。
    # 采集失败只记 warning，不阻断 claim（fail-open）。
    try:
        from mio_taskhub.diff_gate import snapshot as _dg_snapshot
        snap = _dg_snapshot(getattr(task, "workspace", "") or "")
        if snap.ok:
            run.diff_summary = {"before": snap.brief()}
        else:
            run.diff_summary = {"before": None, "collect_error": snap.error}
    except Exception:
        pass
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
def heartbeat(run_id: str, body: dict = None, agent: str = None,
              db: Session = Depends(get_session)):
    body = body or {}
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404)
    # P0 d1b54de0：所有权校验。不传 agent 则跳过（兼容旧调用方）。
    assert_run_owner(run, agent)
    # 已结束的 run 不接受心跳：避免与超时回收 / 正常完成竞争，把终态改回 running。
    if run.state == RunState.FINISHED:
        raise HTTPException(409, detail={
            "code": "run.not_active",
            "message": f"run {run.id} 已结束（state=finished），不再接受心跳",
            "state": run.state.value,
        })
    # T0.7 生命周期埋点：heartbeat 是唯一写 RUNNING 的路径 → CLAIMED 即首心跳
    first_heartbeat = run.state == RunState.CLAIMED
    run.state = RunState.RUNNING
    run.last_heartbeat = _now()
    if body.get("progress") is not None:
        run.progress = body["progress"]
    if body.get("checkpoint") is not None:
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
    # CAS：仅当 run 仍未进入 finished 时才推进到 running。
    # 与超时回收（同样以 state 为条件）竞争时，只有一方 rowcount==1 生效，
    # 杜绝「reaper 判超时回收」与「agent 正常开工」同时成功。
    res = db.exec(
        sa_update(Run)
        .where(Run.id == run.id, Run.state != RunState.FINISHED)
        .values(state=RunState.RUNNING, last_heartbeat=_now())
    )
    if res.rowcount != 1:
        # 被并发方（通常是回收线程）抢先终结 → 本次心跳作废，不改状态。
        db.rollback()
        raise HTTPException(409, detail={
            "code": "run.race_lost",
            "message": f"run {run_id} 状态已被并发操作改变，心跳未生效",
        })
    db.add(run)
    if task:
        db.add(task)
    if m1_event is not None:
        db.add(m1_event)
    # P1-OBS 执行链 P4：run 心跳 = agent 存活证据 → 同步刷新 agent 心跳并置 ONLINE。
    # 否则 agent 因未单独上报 agent 心跳被 `_mark_stale_agents` 置 OFFLINE，其 run 走
    # 120s 紧窗口被提前回收（实测 opencode run p50=127s 被回收）。
    if run.agent_name:
        ag = db.get(Agent, run.agent_name)
        if ag is not None:
            ag.status = AgentStatus.ONLINE
            ag.last_heartbeat = _now()
            db.add(ag)
    db.commit()
    db.refresh(run)
    # T0.7 生命周期埋点（fire-and-forget，全吞，绝不影响心跳路径）：
    # 首心跳 → agent_ready；带 pid → 异步探测回填 process_alive（pid 去重门控在模块内）。
    try:
        from mio_taskhub.observability.lifecycle import (
            TYPE_AGENT_READY, probe_process_alive, record as lc_record,
        )
        if first_heartbeat:
            lc_record(TYPE_AGENT_READY, run.id, {
                "task_id": run.task_id, "agent_id": run.agent_name,
                "attempt": run.attempt, "progress": run.progress,
            })
        _pid = body.get("pid")
        if isinstance(_pid, int) and 0 < _pid < 2 ** 22:
            probe_process_alive(run.id, _pid)
    except Exception:  # noqa: BLE001 — 观测失败不得影响心跳
        pass
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


@router.get("/{run_id}/diff-evidence")
def run_diff_evidence(run_id: str, db: Session = Depends(get_session)):
    """查看本次 run 的改动证据与门禁判定（供 Agent 提交前自查）。

    与 `submit_result` 的门禁**同一套判据**（`diff_gate.check_diff_gate`），
    所以这里返回的 `would_block` 就是真正提交时会拦截与否。

    注意：这是**只读**查询，不改 run 任何字段，也不落任何事件。
    """
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    task = db.get(Task, run.task_id)
    from mio_taskhub.diff_gate import check_diff_gate

    verdict = check_diff_gate(db, task, run, {})
    before = (run.diff_summary or {}).get("before")
    after = (run.diff_summary or {}).get("after")
    return {
        "run_id": run.id,
        "task_id": run.task_id,
        "mode": verdict.mode,
        "would_block": verdict.blocked,
        "skipped": verdict.skipped,
        "reason": verdict.reason,
        "files_changed": verdict.files_changed,
        "before": before,
        "after": after,
        "changed": (run.diff_summary or {}).get("changed"),
    }


@router.post("/{run_id}/result")
def submit_result(run_id: str, body: dict, agent: str = None,
                  db: Session = Depends(get_session)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404)
    task = db.get(Task, run.task_id)
    # P0 d1b54de0：先校验所有权（不传 agent 则跳过），再校验状态可提交。
    # 顺序要紧——所有权先判，避免把「别人的 run 状态」泄漏给未授权调用方。
    assert_run_owner(run, agent)
    assert_run_submittable(run, task)
    success = body.get("success", True)
    # Read Evidence 前置门控：成功提交前，本次 run 必须读过 required 文档（版本一致）。
    if success and task is not None:
        from mio_taskhub.read_evidence import check_read_gate
        gate = check_read_gate(db, task, run_id)
        if gate["enforced"] and not gate["passed"]:
            raise HTTPException(422, detail=_read_gate_detail(gate))
    # 窄门禁（d468e633 FR-3）：「声明成功但零改动」→ 422 拒绝。
    # 放在 ReadEvidence 之后 —— 读证门控纯查库、更便宜且更基础，先报读证问题。
    if success and task is not None:
        _apply_diff_gate(db, task, run, body)
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
    # TH-01（FR-5/FR-6）：提交结果异步写回 runtime。放在 commit 之后、构造响应
    # 之前：写回失败既不回滚已完成的任务，也不改变响应结构（幂等由 outbox key 保证）。
    try:
        from mio_taskhub.mio_bridge import enqueue_outcome
        if success:
            outcome = "success"
        elif task is not None and _is_system_timeout_failure(db, task):
            outcome = "aborted"
        else:
            outcome = "failure"
        verification = ""
        ds = getattr(run, "diff_summary", None)
        if isinstance(ds, dict):
            verification = "diff_changed=%s reason=%s" % (
                ds.get("changed"), ds.get("reason"))
        enqueue_outcome(db, task, run, outcome, run.result or "", verification)
    except Exception:  # noqa: BLE001 — 写回是增强路径，绝不影响提交结果
        logger.warning("outcome enqueue failed (submit unaffected)", exc_info=True)
    if task_state == "retrying":
        return {"id": run.id, "state": run.state.value, "result": run.result,
                "task_state": task_state, "retry_at": task.retry_at.isoformat(),
                "backoff_seconds": payload.get("backoff_seconds")}
    return {"id": run.id, "state": run.state.value, "result": run.result, "task_state": task_state}


def _apply_diff_gate(db, task, run, body):
    """窄门禁：判定并处理「声明成功但零改动」（d468e633 FR-3/5/6/7）。

    与 `submit_result` 主流程分离：本函数要么**放行**（返回 None），
    要么**抛 HTTPException(422)** —— 抛之前必须把拦截证据落库并 commit。

    三个必踩的坑（实现时逐条对照，见 spec §六）：
      1. `db.py:52-54` 的 `get_session` 退出时只 close 不 commit
         → **抛 422 前必须先 db.commit()**，否则拦截事件随会话丢弃、人永远看不到。
      2. `events.py:43-48` 的 `MESSAGE_TYPES` 不含 `run`
         → 事件必须 `entity="task"`，否则广播成 `event_update`，前端不弹 toast。
      3. 拦截事件**不要**塞 `TaskEvent`：`TaskDetail.jsx` 对 TaskEvent 走
         `compositeLabel(ev.to_state, ev.to_stage)`，null,null 会渲染成 `null·null`
         → 改走 `HistoryEvent`。
    """
    import json as _json
    from mio_taskhub.diff_gate import (
        MAX_REJECTIONS, check_diff_gate, merge_annotations,
    )
    from mio_taskhub.events import emit_event
    from mio_taskhub.models import HistoryEvent
    from mio_taskhub.utils import _now

    verdict = check_diff_gate(db, task, run, body)

    # 记录判定结论（无论放行与否都留痕，便于事后回答「为何没拦」）
    summary = dict(run.diff_summary or {})
    summary["after"] = {
        "files": verdict.files_changed,
        "insertions": verdict.insertions,
        "deletions": verdict.deletions,
    }
    summary["changed"] = verdict.reason == "has_changes"
    summary["reason"] = verdict.reason
    summary["mode"] = verdict.mode
    run.diff_summary = summary

    if not verdict.blocked:
        # 放行路径：豁免/有改动/warn 档 → 只写标注，不拦、不弹 toast
        # **off 档不留标注** —— 契约写「off → 200 且无标注」：
        # 门禁完全关闭时不该在业务数据里留任何痕迹。
        if verdict.reason != "gate_off" and (
                verdict.skipped or verdict.reason == "empty_diff_warn"):
            msg = verdict.message or ""
            if "不影响放行" not in msg:
                msg = (msg + "；本项为标注，不影响放行") if msg else "本项为标注，不影响放行"
            run.annotations = merge_annotations(run.annotations, [{
                "code": verdict.reason,
                "level": "warn",
                "message": msg,
                "at": _now().isoformat(),
            }])
        db.add(run)
        # 标注必须落库 —— submit_result 主流程在写 run 终态时会一并 commit，
        # 但本函数是**前置**门禁，主流程可能因后续异常回滚。
        # 这里显式 commit，保证「跳过判定的原因」事后查得到（FR-8 可观测）。
        db.commit()
        return None

    # ── 拦截路径 ──────────────────────────────────────────────────────
    run.rejections = (run.rejections or 0) + 1
    run.annotations = merge_annotations(run.annotations, [{
        "code": "run.empty_diff",
        "level": "block",
        "message": verdict.message,
        "at": _now().isoformat(),
    }])
    db.add(run)

    # 可见性 ①：WS 事件（entity 必须是 task，否则前端不弹 toast）
    emit_event(db, type="run.result_rejected", entity="task",
               entity_id=task.id, run_id=run.id,
               payload={"code": "run.empty_diff", "message": verdict.message,
                        "rejections": run.rejections,
                        "max_rejections": MAX_REJECTIONS})
    # 可见性 ②：执行历史（走 HistoryEvent，不走 TaskEvent —— 见坑 ③）
    db.add(HistoryEvent(
        task_id=task.id, type="rejected",
        payload=_json.dumps({"message": verdict.message,
                             "rejections": run.rejections,
                             "code": "run.empty_diff"},
                            ensure_ascii=False),
    ))
    db.commit()          # ★ 必须先落库再抛：get_session 退出不 commit

    if run.rejections > MAX_REJECTIONS:
        # 连续被拒**超过**上限（默认 2，即第 3 次）→ 判本次执行失败，
        # 交既有重试机制。
        # 语义：「被拒 2 次以内可继续重试」—— 前 2 次是 422（请改完重试），
        # 第 3 次才是「本次执行失败」。
        # 此时**不再抛 422** —— 已不是「请重试」，而是「本次执行失败」。
        run.exit_code = 1
        run.state = RunState.FINISHED
        run.finished_at = _now()
        run.result = verdict.message
        db.add(run)
        try:
            m1_events = []
            payload = {"success": False, "state": None}
            _handle_failure(task, run, m1_events, payload)
            for ev in m1_events:
                db.add(ev)
        except Exception:
            pass
        db.commit()
        raise HTTPException(409, detail={
            "code": "run.empty_diff_repeated",
            "message": (f"连续 {run.rejections} 次声明 success 但工作区零改动，"
                        f"本次执行判定失败：{verdict.message}"),
            "hint": ("请在工作区实际完成改动后重新领取；"
                     "若本任务确无代码产出，用 PATCH /tasks/{id} 设置 diff_gate_exempt=true。"),
            "files_changed": 0,
            "rejections": run.rejections,
            "max_rejections": MAX_REJECTIONS,
        })

    raise HTTPException(422, detail={
        "code": "run.empty_diff",
        "message": verdict.message,
        "hint": ("先在工作区实际完成改动（提交或保留改动均可）再 submit_result；"
                 "若本任务确无代码产出，用 PATCH /tasks/{id} 设置 diff_gate_exempt=true。"),
        "files_changed": 0,
        "rejections": run.rejections,
        "max_rejections": MAX_REJECTIONS,
    })


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
    # 状态机拒绝时返回真实状态，不再谎报 completed（_safe_transition 会静默吞掉
    # IllegalTransition；曾导致 requeue 后迟到提交 task 实为 queued 却回 completed）。
    if task.state != TaskState.COMPLETED:
        actual = task.state.value if hasattr(task.state, "value") else str(task.state)
        return actual, {
            "success": True, "state": actual,
            "note": "状态机拒绝转入 completed，返回真实 task 状态",
        }
    payload = {"success": True, "state": "completed"}
    if recovered:
        payload["recovered_from_failed"] = True

    # 经验沉淀（任务 28f8fceb FR-3）：把本次的可复用信息写入 memory_store，
    # 供后续同项目任务 claim 时参考。**纯旁路**：失败只记日志，不影响状态流转。
    try:
        from mio_taskhub.api.task_lessons import write_experience
        write_experience(task, run.result)
    except Exception:
        logger.warning("沉淀任务经验失败（不影响本次提交结果）", exc_info=True)

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
