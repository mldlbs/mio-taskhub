from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.events import emit_event
from mio_taskhub.policy_guard import guard_action
from mio_taskhub.models import Task, TaskState, TaskStage, Discussion, Run, RunState
from mio_taskhub.utils import _now
from mio_taskhub.workflow.transitions import apply_transition, _orm_to_status_state, _orm_to_status_stage
from mio_taskhub.api.task_helpers import parse_enum
from mio_taskhub.doc_paths import DOC_KINDS, doc_path_of, merge_doc_paths, sync_legacy_fields
from mio_taskhub.doc_lifecycle import reached_state
from mio_taskhub.workflow.state_machine import State, State as M1State, Stage as M1Stage, ActorType, IllegalTransition as M1Illegal

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ── 阶段产出物要求（单一事实源） ──────────────────────────────────────────────
# 前端与 agent 可通过 GET /tasks/stages/requirements 获取，避免各处重复硬编码。
#   document_kind      : 该阶段需要的文档 kind（写入 doc_paths）
#   requires_discussion: 进入该阶段前是否必须已有讨论记录
#   accepts_text       : 未提供文档时是否接受纯文本字段（done 的审查结论）
#   error              : 产出物缺失时的 422 文案（保持与历史一致）
# 每个阶段可要求多个文档 kind（document_kinds 列表）。done 用 accepts_text 接受
# 纯文本 review_result；其余阶段所有要求 kind 必须同时存在（strict 下缺失即 422）。
# 2026-09-17 扩展：补齐 brainstorming→requirement、implementing→changelog 两道门槛，
# 关闭「brainstorming / implementing 阶段无产物门槛」的已知缺口（skill §8 gap #1）。
STAGE_ARTIFACT_REQUIREMENTS = {
    "brainstorming": {
        "document_kinds": ["requirement"],
        "requires_discussion": False,
        "accepts_text": False,
        "error": "brainstorming stage requires a requirement document (doc_paths['requirement'])",
    },
    "design": {
        "document_kinds": ["spec"],
        "requires_discussion": True,
        "accepts_text": False,
        "error": "design stage requires spec_path",
    },
    "planning": {
        "document_kinds": ["plan"],
        "requires_discussion": False,
        "accepts_text": False,
        "error": "planning stage requires plan_path",
    },
    "implementing": {
        "document_kinds": ["changelog"],
        "requires_discussion": False,
        "accepts_text": False,
        "error": "implementing stage requires a changelog document (doc_paths['changelog'])",
    },
    "done": {
        "document_kinds": ["review"],
        "requires_discussion": False,
        "accepts_text": True,
        "text_field": "review_result",
        "error": "done stage requires review_result or a review document",
    },
}

# ── 阶段推进的文档生命周期门控 ──────────────────────────────────────────────
# 关闭「draft / 空契约仍能推进到 design」的缺口：进入目标阶段前，要求这些文档
# kind 的生命周期状态至少达到 required_state（线性生命周期，索引 >= 即可）。
#   仅当 doc_statuses[kind] 已显式设置状态时才校验；从未设状态的旧任务 / 未跟踪
#   任务一律放行（向后兼容，不阻断历史数据）。
#   api 此前不在任何阶段的 document_kinds 里 → 这里补进 design 门控，契约未
#   approved 不能进入 planning（设计产物 spec+api 都应先审完再写实现/计划）。
# changelog / review 无生命周期，不在此表；implementing / done 仅靠上游
# （design / planning 已门控 spec+api / plan）间接保证。
# 严格 kind：未显式设状态时，只要任务已进入文档生命周期（doc_statuses 非空）就算缺失并阻断。
# 目的：堵住「没有 requirement 也能批准 spec/api 进 design」的缺口。
# 向后兼容：由 task.doc_gate_exempt 显式豁免（迁移把存量历史任务全部置 True）。
# 2026-10-10 审计 P0-1 修正：此前「doc_statuses 为空即放行」被当成历史任务豁免，但新建任务
# 的 doc_statuses 本来就是空的 —— 等于 spec/api/plan 门控在常态路径上从未生效（实测可无任何
# 文档 approved 直达 ready）。现改为：**凡该 kind 的文档已实际落盘（文件存在），就必须有
# 显式状态且达到 required**；只登记路径未落盘不阻断（允许先声明位置、边做边写）。
# 「是否豁免」只看 doc_gate_exempt 这一个显式字段，不再靠推断。
STRICT_KINDS = {"requirement"}

LIFECYCLE_GATE = {
    "brainstorming": {"requirement": "approved"},
    "design": {"requirement": "approved", "spec": "approved", "api": "approved"},
    "planning": {"plan": "approved"},
}

# 研发流水线顺序。用于**累计门控**：move（拖拽）可任意跳转，若只按「目标阶段」查
# LIFECYCLE_GATE，跳过 design 直接进 planning/ready 就能绕过 design 的文档门槛
# （2026-10-10 审计实测：design 被 422 拦下，但 planning/ready 仍 200 直达）。
# 改为「目标阶段及其之前所有阶段的门槛取并集」，跳阶段不再等于免检。
STAGE_PIPELINE = ("brainstorming", "design", "planning", "ready",
                  "implementing", "review", "done")


def _gates_upto(dst) -> dict:
    """目标阶段及其之前所有阶段的文档门槛合并（跳阶段不得绕过前序门槛）。"""
    val = getattr(dst, "value", dst)
    if val not in STAGE_PIPELINE:
        return {}
    merged: dict = {}
    for st in STAGE_PIPELINE[:STAGE_PIPELINE.index(val) + 1]:
        merged.update(LIFECYCLE_GATE.get(st) or {})
    return merged


def _doc_written(t: Task, kind: str) -> bool:
    """该 kind 的文档是否已**实际落盘**（有内容等着被批准）。

    与 `doc_path_of`（只判断路径是否登记）区分开：
      · 只登记路径、文件还没写 → 不阻断（允许「先声明产出物位置，边做边写」）；
      · 文件已写出 → 必须走完生命周期并 approved，否则阻断。
    2026-10-10 审计 P0-1：真正要堵的是「文档写完了却没批准就进下一阶段」，
    而不是「还没写文档就先进阶段」。
    workspace 缺失 / 路径不可解析时按「未落盘」处理，不阻断。
    """
    try:
        from mio_taskhub.read_evidence import resolve_doc_path
        p = resolve_doc_path(t, kind)
        return bool(p is not None and p.is_file())
    except OSError:
        return False


def _incoming_doc_kinds(body: dict) -> set:
    """本次请求将要登记的文档 kind；这些文档不参与本次生命周期校验。"""
    kinds = set()
    if isinstance((body or {}).get("doc_paths"), dict):
        kinds.update(str(k) for k in body["doc_paths"])
    if (body or {}).get("spec_path"):
        kinds.add("spec")
    if (body or {}).get("plan_path"):
        kinds.add("plan")
    return kinds


def _check_lifecycle_gate(t: Task, dst: TaskStage, force: bool = False,
                          skip_kinds=None):
    """进入目标阶段前校验文档生命周期状态已达门槛。

    豁免：仅 `task.doc_gate_exempt=True`（显式字段，迁移把存量历史任务置 True）时不校验。
    2026-10-10 审计 P0-1：原实现用「doc_statuses 是否为空」推断「历史任务」并放行，但新建
    任务的 doc_statuses 本来就是空的，等于 spec/api/plan 门控在常态路径上从未生效（实测可无任何
    文档 approved 直达 ready）。现改为：
      · 该 kind 已落盘（文件存在）→ 必须有显式状态且达到 required；
      · 该 kind 未落盘 → 放行（本阶段不涉及「已写但未批」的文档）；
      · STRICT_KINDS（requirement）另有一道：只要任务已跟踪文档链，无状态即阻断。
      · 门槛按**累计**方式取「目标阶段之前所有阶段」的并集 —— move 可任意跳转，
        只查目标阶段会让「跳过 design 直进 planning/ready」绕过前序门槛。
    未达门槛且未 force → 抛 HTTPException(422, {message, gate})；
    force 绕过 → 返回被绕过的条目列表（供调用方留痕），否则返回空列表。

    2026-11-03（方案 B）补充：
      · light/standard 对已登记文档要求 approved；
      · `skip_kinds` 为本次请求新登记的 kind，不参与本次校验（否则「进 design 时
        才登记 spec」永远无法满足「spec 必须已 approved」）；
      · 文档路径不可解析（无 workspace/非法路径）时沿用系统惯例 fail-open。
    """
    gate = _gates_upto(dst)
    if not gate:
        return []
    if getattr(t, "doc_gate_exempt", False):
        return []
    tier = str(getattr(t, "gate_tier", "light") or "light").lower()
    tier_reason = str(getattr(t, "gate_tier_reason", "") or "")
    # 2026-11-03：light 与 standard 对「已登记文档」一视同仁，必须 approved；
    # 未登记仍放行（fail-open）。“所有任务必须有 requirement”属方案 C，单独决策。
    if tier_reason and tier in ("light", "standard"):
        from mio_taskhub.read_evidence import resolve_doc_path
        skip = {str(k) for k in (skip_kinds or ())}
        statuses = dict(getattr(t, "doc_statuses", None) or {})
        blocked = []
        for kind, required in gate.items():
            if kind in skip:
                continue
            if not doc_path_of(t, kind):
                continue
            if resolve_doc_path(t, kind) is None:
                # workspace 缺失 / 路径不可解析 → 无法校验，沿用 fail-open
                continue
            entry = statuses.get(kind) or {}
            if not _doc_written(t, kind):
                blocked.append({
                    "kind": kind,
                    "current": entry.get("state"),
                    "required": required,
                    "reason": "registered_but_file_missing",
                })
            elif not reached_state(kind, entry.get("state"), required):
                blocked.append({
                    "kind": kind,
                    "current": entry.get("state"),
                    "required": required,
                    "reason": "registered_but_not_approved",
                })
        if blocked and not force:
            # 人话化（E7/P0-2）：被门控拦住时直接告诉调用方「要做什么」，不用去翻文档猜
            steps = "；".join(
                f"{b['kind']}: {b['current'] or '未设置'} → {b['required']}" for b in blocked)
            raise HTTPException(422, detail={
                "message": f"{tier} tier requires registered documents to be approved. "
                           f"你要做的是：{steps}"
                           f"（POST /api/v1/tasks/{t.id}/doc/{{kind}}/status，质量分需≥80；"
                           "确要跳过带 force=true，会留痕）",
                "gate": blocked,
            })
        return blocked
    statuses = dict(getattr(t, "doc_statuses", None) or {})
    tracked = bool(statuses)                 # 已进入文档生命周期（显式设过任一状态）
    blocked = []
    for kind, required in gate.items():
        entry = statuses.get(kind)
        if not entry:                       # 从未设置状态
            written = _doc_written(t, kind)
            if written or (kind in STRICT_KINDS and tracked):
                blocked.append({
                    "kind": kind,
                    "current": None,
                    "required": required,
                    "has_doc": bool(doc_path_of(t, kind)),
                    "written": written,
                })
            continue
        cur = entry.get("state")
        if reached_state(kind, cur, required):
            continue
        blocked.append({
            "kind": kind,
            "current": cur,
            "required": required,
            "has_doc": bool(doc_path_of(t, kind)),
        })
    if blocked and not force:
        # 人话化（E7/P0-2）：门控拦路时给出可执行的下一步，而不是让调用方撞错后去翻手册
        steps = "；".join(
            f"{b['kind']}: {b['current'] or '未设置'} → {b['required']}" for b in blocked)
        raise HTTPException(422, detail={
            "message": "文档生命周期门控：以下文档未达进入该阶段所需状态。"
                       f"你要做的是：{steps}"
                       f"（POST /api/v1/tasks/{t.id}/doc/{{kind}}/status，推进顺序 draft→review→approved，"
                       "质量分需≥80；确要跳过带 force=true，会留痕）",
            "gate": blocked,
        })
    return blocked


def _stage_requirement(dst) -> dict:
    """取目标阶段的要求（TaskStage 或字符串均可）。"""
    value = getattr(dst, "value", dst)
    return STAGE_ARTIFACT_REQUIREMENTS.get(value, {})


def _requirement_approved(t: Task) -> bool:
    """进设计的讨论门豁免条件：需求文档已推进到「已批准」。

    方向确认这件事本来就有需求文档这道门承担（构思→设计要求 requirement 到 approved），
    再单独要求一条讨论记录属于重复收费（用户 2026-11-05 拍板）。
    requirement 未登记或未批准时，讨论门照常生效。
    """
    entry = (t.doc_statuses or {}).get("requirement")
    state = entry.get("state") if isinstance(entry, dict) else entry
    return str(state or "") == "approved"


def _required_kinds(req: dict):
    """返回该阶段要求的所有文档 kind（list）。兼容旧的单 document_kind 写法。"""
    if req.get("document_kinds"):
        return list(req["document_kinds"])
    if req.get("document_kind"):
        return [req["document_kind"]]
    return []


def _apply_stage_requirements(t: Task, dst: TaskStage, body: dict, strict: bool = True):
    """校验目标阶段的产出物并设置辅助字段。抛 HTTPException(422) 当产出物缺失。

    要求来自 STAGE_ARTIFACT_REQUIREMENTS：design 需 spec、planning 需 plan、
    implementing 需 changelog、brainstorming 进入需 requirement 文档、
    done 需 review 文档或 review_result 文本。
    文档路径统一走 doc_paths（kind -> path），spec_path / plan_path 作为兼容入口，
    写入后与 doc_paths 保持同步。
    注意：本函数 **不修改** task.state / task.stage —— 状态变更由调用方通过
    apply_transition() 统一处理。
    strict=False 时（拖拽轻量路径）不强制产出物，仅在提供时记录，
    done 缺审查产物时自动补默认结论。
    """
    incoming = {}
    if isinstance(body.get("doc_paths"), dict):
        incoming.update(body["doc_paths"])
    for legacy_key, kind in (("spec_path", "spec"), ("plan_path", "plan")):
        if body.get(legacy_key):
            incoming[kind] = body[legacy_key]
    if incoming:
        t.doc_paths = merge_doc_paths(t.doc_paths, incoming)
        for kind in ("spec", "plan"):
            if kind in incoming:
                sync_legacy_fields(t, kind, doc_path_of(t, kind))

    req = _stage_requirement(dst)
    if not req:
        return

    kinds = _required_kinds(req)
    if not kinds:
        return

    # accepts_text：文档或纯文本满足其一即可（done 的审查结论）
    if req.get("accepts_text"):
        field = req.get("text_field", "review_result")
        text = (body.get(field) or "").strip() or (getattr(t, field, "") or "").strip()
        any_doc = any(doc_path_of(t, k) for k in kinds)
        if not text:
            if any_doc:
                # 只给了文档时留一条指向它的提示，避免文本字段为空
                text = f"见审查文档 {doc_path_of(t, kinds[0])}"
            elif getattr(dst, "value", dst) == "done":
                # Phase 1：done 不再要求 review_result；完成证据由 completion_gate 校验。
                return
            else:
                raise HTTPException(422, req["error"])
        setattr(t, field, text)
        return

    # 普通文档门槛：所有要求的 kind 都必须存在（strict 下缺失即 422）
    missing = [k for k in kinds if not doc_path_of(t, k)]
    if missing and strict:
        raise HTTPException(422, req["error"])


def _check_child_stage(t: Task, dst: TaskStage):
    """P2d：子任务不承担 design/planning，只走 ready→implementing→review→done。"""
    if getattr(t, "parent_id", None) and dst in (TaskStage.DESIGN, TaskStage.PLANNING):
        raise HTTPException(422, detail={
            "code": "decompose.child_stage_forbidden",
            "message": "child task cannot enter design/planning",
        })


def _check_done_gate(t: Task, db: Session):
    """Phase 1：done 统一完成校验（warn 只返回问题，block 抛错）。"""
    from mio_taskhub.completion_gate import GateError, check_done
    try:
        return check_done(db, t)
    except GateError as exc:
        raise HTTPException(exc.status, detail=exc.as_detail())


def _transition_to_stage(task, to_state, stage, actor_type, actor_id, reason, m1_events):
    """Apply a single state transition and collect the resulting event."""
    t, event = apply_transition(task, to_state, stage, actor_type, actor_id, reason)
    if event:
        m1_events.append(event)
    return t, event


def _commit_task_and_events(task, db, m1_events):
    """Commit task and all transition events, then refresh the task."""
    db.add(task)
    for ev in m1_events:
        db.add(ev)
    _close_active_runs_if_terminal(db, task)
    db.commit()
    db.refresh(task)


def _close_active_runs_if_terminal(db, task):
    """任务进入终态（completed/failed/cancelled）时关闭其活跃 run（CLAIMED/RUNNING）。

    T4.3/P1-OBS 执行链：`stage/move` 到 done/cancelled 此前**不关 run** → run 悬挂到
    reaper 回收（历史实测 run b5ae9267 悬挂 24h）。此处保证「任务终态 ⇒ 其活跃 run 关闭」，
    使 run 生命周期与 task 一致。
    """
    from mio_taskhub.workflow.state_machine import is_terminal
    if not is_terminal(task):
        return
    runs = db.exec(
        select(Run).where(
            Run.task_id == task.id,
            Run.state.in_([RunState.CLAIMED, RunState.RUNNING]),
        )
    ).all()
    for r in runs:
        r.state = RunState.FINISHED
        r.finished_at = _now()
        if not r.result:
            r.result = f"task {task.state.value} (closed by stage transition)"
        db.add(r)


@router.get("/stages/requirements", response_model=dict)
def stage_requirements():
    """各研发阶段的产出物要求（单一事实源）。

    供 Web UI 提示与 agent 查询「推进到某阶段需要什么文档」。
    """
    return {
        "stages": {
            stage: {
                "document_kinds": _required_kinds(req),
                "document_kind": _required_kinds(req)[0] if _required_kinds(req) else None,
                "requires_discussion": bool(req.get("requires_discussion")),
                "accepts_text": bool(req.get("accepts_text")),
                "text_field": req.get("text_field"),
                "error": req["error"],
                "lifecycle_gate": LIFECYCLE_GATE.get(stage, {}),
            }
            for stage, req in STAGE_ARTIFACT_REQUIREMENTS.items()
        },
        "doc_kinds": list(DOC_KINDS),
    }


# 取消原因枚举（F17/T0.6：task_cancelled 事件 payload 必须非空且可归因）。
# manual=用户/API 主动取消；duplicate=重复任务；obsolete=需求失效；
# superseded=被新任务取代；policy=策略/门控拒绝后放弃；other=其它（配 detail）。
CANCEL_REASONS = ("manual", "duplicate", "obsolete", "superseded", "policy", "other")


@router.delete("/{task_id}")
def cancel_task(task_id: str, confirm: bool = Query(False),
                reason: str = Query("manual"),
                detail: str = Query(""),
                db: Session = Depends(get_session)):
    if reason not in CANCEL_REASONS:
        raise HTTPException(400, f"invalid reason: {reason!r} (expected one of: {', '.join(CANCEL_REASONS)})")
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404)
    policy = guard_action("taskhub:delete-task", confirm=confirm)
    from mio_taskhub.workflow.transitions import apply_transition
    from mio_taskhub.workflow.state_machine import State, Stage, ActorType, IllegalTransition
    current_stage = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    try:
        _, m1_event = apply_transition(
            t, State.CANCELLED, Stage(current_stage.value),
            ActorType.USER, "api:cancel_task",
            reason=f"用户取消[{reason}]",
        )
        db.add(m1_event)
    except IllegalTransition:
        raise HTTPException(409, f"task {task_id} 不可取消（终态）")
    event = emit_event(db, type="task_cancelled", entity="task", entity_id=task_id,
                       payload={"reason": reason,
                                **({"detail": detail} if detail else {})})
    db.add(t)
    db.commit()
    return {"ok": True, "state": "cancelled", "policy": policy,
            "reason": reason, "detail": detail}


@router.post("/{task_id}/retry")
def retry_task(task_id: str, body: dict = None, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    if t.state not in (TaskState.FAILED, TaskState.RETRYING):
        raise HTTPException(409, f"task {task_id} 不可重试（当前状态 {t.state.value}，仅 failed/retrying 可重试）")
    orig_state = t.state.value
    if t.attempt >= t.max_retries:
        t.attempt = 0
        t.retry_count = 0
    t.retry_at = None
    # T4.3/P1-OBS：手动重试 = 全新尝试 → 清空 never_started bounce 累积。
    # 否则受幽灵洪泛/提前回收影响的任务 bounce 已达上限（6+），retry 后首个 never_started
    # 立即再触发 FAILED，retry 失去意义（实测 T0.1 bounce 累积到 14）。
    t.bounce_count = 0
    from mio_taskhub.workflow.transitions import apply_transition
    from mio_taskhub.workflow.state_machine import State, Stage, ActorType, IllegalTransition
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
    _check_child_stage(t, dst)
    if _stage_requirement(dst).get("requires_discussion") and not _requirement_approved(t):
        discussions = db.exec(select(Discussion).where(Discussion.task_id == task_id)).all()
        if not discussions:
            raise HTTPException(422, f"{dst.value} stage requires at least one discussion record "
                                     f"(exempted when the requirement doc is approved). "
                                     f"你要做的是二选一：POST /api/v1/tasks/{task_id}/discussions "
                                     "写一条讨论（topic+conclusions）；或把需求文档推进到「已批准」，"
                                     "方向确认由需求文档承担，讨论要求自动豁免")
    _apply_stage_requirements(t, dst, body)
    forced_gate = _check_lifecycle_gate(t, dst,
                                        force=bool((body or {}).get("force")),
                                        skip_kinds=_incoming_doc_kinds(body))
    if dst == TaskStage.DONE:
        _check_done_gate(t, db)
    m1_events = []
    cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
    # 失败任务的人工阶段推进只有两条合法出路，其余一律给人话而不是状态机天书：
    #   · 到 done：人审否决失败判定，走 T25 单跳（done 的合法前置只有 review）
    #   · 到 cancelled：T14 关闭
    # 中间阶段（implementing→review 等）在 FAILED 下没有边——失败任务不在流程里，
    # 想继续干活必须先 retry / reopen，这也是真实语义。
    if cur_s == TaskState.FAILED and dst not in (TaskStage.DONE, TaskStage.CANCELLED):
        raise HTTPException(
            422,
            f"该任务处于失败状态（stage={src.value}），不能直接推进到 {dst.value}。你要做的是："
            f"① POST /api/v1/tasks/{task_id}/retry 重新排队执行；"
            f"② 或 POST /api/v1/tasks/{task_id}/stage {{\"target_stage\":\"cancelled\"}} 关闭；"
            f"③ 若人审认为产出合格，推进到 done 即人工验收（仅 review 阶段可走）",
        )
    try:
        from_st = _orm_to_status_stage(src)
        if dst == TaskStage.DONE:
            if cur_s == TaskState.FAILED:
                # 人审推翻失败判定：FAILED(REVIEW) → COMPLETED(DONE) 单跳（T25）。
                # 不能复用下面「同阶段 completed + T6 finalize」两跳，因为
                # (FAILED,REVIEW)→(COMPLETED,REVIEW) 的 T22 只授权给 SYSTEM/AGENT。
                _transition_to_stage(t, State.COMPLETED, M1Stage.DONE,
                                     ActorType.USER, "api:advance_stage",
                                     "advance→done: 人工验收推翻失败判定", m1_events)
            else:
                if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
                    _transition_to_stage(t, State.COMPLETED, from_st,
                                         ActorType.USER, "api:advance_stage",
                                         "advance→done: review pass", m1_events)
                _transition_to_stage(t, State.COMPLETED, M1Stage.DONE,
                                      ActorType.SYSTEM, "auto:finalize",
                                      "T6 finalize", m1_events)
        elif dst == TaskStage.CANCELLED:
            _transition_to_stage(t, State.CANCELLED, from_st,
                                  ActorType.USER, "api:advance_stage",
                                  "advance→cancelled", m1_events)
        else:
            actor = ActorType.USER if cur_s == TaskState.QUEUED else ActorType.SYSTEM
            actor_id = "api:advance_stage" if cur_s == TaskState.QUEUED else "auto:advance"
            _transition_to_stage(t, _orm_to_status_state(cur_s), M1Stage(dst.value),
                                  actor, actor_id,
                                  f"advance {src.value}→{dst.value}", m1_events)
    except M1Illegal as exc:
        raise HTTPException(400, f"state machine rejected advance: {exc}")
    if forced_gate:
        emit_event(db, type="task_stage_gate_forced", entity="task", entity_id=task_id,
                   payload={"target": dst.value, "gate": forced_gate})
    emit_event(db, type="task_stage", entity="task", entity_id=task_id,
               payload={"target": dst.value})
    _commit_task_and_events(t, db, m1_events)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "doc_paths": dict(t.doc_paths or {}),
            "review_result": t.review_result,
            "state": t.state.value}


@router.post("/{task_id}/stage/move")
def move_to_stage(task_id: str, body: dict, db: Session = Depends(get_session)):
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
    _check_child_stage(t, dst)
    _apply_stage_requirements(t, dst, body, strict=False)
    if src == dst:
        db.add(t)
        db.commit()
        db.refresh(t)
        return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
                "plan_path": t.plan_path, "review_result": t.review_result,
                "state": t.state.value}
    forced_gate = _check_lifecycle_gate(t, dst,
                                        force=bool((body or {}).get("force")),
                                        skip_kinds=_incoming_doc_kinds(body))
    if dst == TaskStage.DONE:
        _check_done_gate(t, db)
    m1_events = []
    cur_s = t.state if isinstance(t.state, TaskState) else TaskState(t.state)
    cur_m1 = _orm_to_status_state(cur_s)
    to_st = _orm_to_status_stage(dst)
    try:
        if dst == TaskStage.DONE:
            # 设计锁（is_valid_stage_move）：done 只能从 review 经 T5+T6 进入，
            # 任何直跳 (s, src)→(completed, done) 都是未定义转换。修复 2026-09-25：
            # 原实现按 src 阶段直接入 completed，src=ready 时抛 IllegalTransition
            # →500。现改为非 review 源先同状态 stage-only 挪到 review（T18 兜底），
            # queued/claimed 走 T5 入 (completed, review)，最后 T6 收尾。
            if cur_s not in (TaskState.QUEUED, TaskState.CLAIMED, TaskState.COMPLETED):
                raise HTTPException(
                    409, f"不可移动到 done：状态 {cur_s.value} 无完成路径"
                         "（failed/retrying 请先 retry，running 请等待 agent 提交）")
            if src != TaskStage.REVIEW:
                _transition_to_stage(t, cur_m1, M1Stage.REVIEW,
                                     ActorType.USER, "api:move_to_stage",
                                     f"move→done: pass through review ({src.value})",
                                     m1_events)
            if cur_s in (TaskState.CLAIMED, TaskState.QUEUED):
                _transition_to_stage(t, M1State.COMPLETED, M1Stage.REVIEW,
                                     ActorType.USER, "api:move_to_stage",
                                     "move→done: review pass", m1_events)
            _transition_to_stage(t, M1State.COMPLETED, M1Stage.DONE,
                                 ActorType.SYSTEM, "auto:finalize",
                                 "move→done: finalize", m1_events)
        elif dst == TaskStage.CANCELLED:
            from_st = _orm_to_status_stage(src)
            _transition_to_stage(t, M1State.CANCELLED, from_st,
                                 ActorType.USER, "api:move_to_stage",
                                 "move→cancelled", m1_events)
        else:
            actor = ActorType.USER if cur_s == TaskState.QUEUED else ActorType.SYSTEM
            actor_id = "api:move_to_stage" if cur_s == TaskState.QUEUED else "auto:move"
            _transition_to_stage(t, cur_m1, to_st, actor, actor_id,
                                 f"move {src.value}→{dst.value}", m1_events)
    except M1Illegal as exc:
        raise HTTPException(409, f"stage move 被状态机拒绝: {exc}")
    if forced_gate:
        emit_event(db, type="task_stage_gate_forced", entity="task", entity_id=t.id,
                   payload={"target": dst.value, "gate": forced_gate, "move": True})
    emit_event(db, type="task_moved", entity="task", entity_id=t.id,
               payload={"from": src.value, "to": dst.value})
    _commit_task_and_events(t, db, m1_events)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "doc_paths": dict(t.doc_paths or {}),
            "review_result": t.review_result,
            "state": t.state.value}
