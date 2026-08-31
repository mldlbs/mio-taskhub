# mio_taskhub/status.py
"""统一的任务状态判据（终态 / 依赖满足），供 DAG、Board、Scheduler、Planner、前端联动使用。"""
from __future__ import annotations
import json
import logging
from typing import Any

logger = logging.getLogger("mio_taskhub.status")

# state 维度上的终态集合（stage=done/cancelled 也视为终态，见 is_terminal）
TERMINAL_STATES = {"completed", "cancelled", "failed", "blocked_failed"}


def _stage_str(v) -> str:
    if v is None:
        return ""
    return v.value if not isinstance(v, str) else v


def is_terminal(task) -> bool:
    """任务不可再被调度/放行（终态）。state 或 stage 任一为终态即 True。"""
    s = task.state.value if hasattr(task.state, "value") else task.state
    st = _stage_str(task.stage)
    return s in TERMINAL_STATES or st in ("done", "cancelled")


def dependency_satisfied(task) -> bool:
    """作为前置依赖时是否算满足：state=completed 或 stage=done。"""
    s = task.state.value if hasattr(task.state, "value") else task.state
    return s == "completed" or _stage_str(task.stage) == "done"


def normalize_depends(value: Any) -> list:
    """把 depends_on 的任意旧值/新值归一化为列表。

    - None / 空白字符串 → []
    - 非 JSON 单值字符串（旧库 VARCHAR 列）→ [value]
    - 合法 JSON 数组字符串 → 解析为列表
    - 非法 JSON → [] + warning（不阻塞）
    - 已是 list → 原样（清掉空白项）
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [x for x in value if x]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("[") or s.startswith("{"):
            try:
                arr = json.loads(s)
                return [x for x in arr if x] if isinstance(arr, list) else []
            except ValueError:
                logger.warning("depends_on 非法 JSON，已置空: %r", value)
                return []
        return [s]
    return []


def task_deps(task) -> list:
    """读取任务依赖列表（兼容旧字符串/None/列表）。"""
    return normalize_depends(getattr(task, "depends_on", None))


# =============================================================================
# M1 状态机（追加于 b4d0712 的依赖/终态工具之上）
# 锁定原则（Phase-2 M1，2026-08-29 拍板）：
#  1. state = 运行时生命周期；stage = 研发阶段。
#  2. state=completed != 任务最终完成。
#  3. 完成终态 = state=completed AND stage=done  → 见 is_fully_done()
#  4. brainstorming / design / planning 不启用 completed（推进是原子动作）。
#  5. 每次合法转换必须记录 actor（actor_type + actor_id）。
#  6. 每次合法转换必须追加 task_events。
#  7. bounce_count 是汇总字段，bounce 原因以 task_events 为准。
#  8. 系统动作必须使用显式 system actor。
#  9. 所有状态组合和转换必须经过合法性校验。
#
# 与上方 is_terminal 的语义区别（共存，不冲突）：
#   is_terminal    = 调度终态（不再被重派：cancelled/failed/done），DAG/Scheduler 用
#   is_fully_done  = 完成终态（Q1 锁定：completed AND done），统计/通知/最终完成时间用
# =============================================================================
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Set, Tuple


class State(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Stage(str, Enum):
    BRAINSTORMING = "brainstorming"
    DESIGN = "design"
    PLANNING = "planning"
    READY = "ready"
    IMPLEMENTING = "implementing"
    REVIEW = "review"
    DONE = "done"


class ActorType(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


# ---------- Legal (state, stage) ----------
LEGAL_COMBOS: Set[Tuple[State, Stage]] = {
    (State.QUEUED, Stage.BRAINSTORMING),
    (State.QUEUED, Stage.DESIGN),
    (State.QUEUED, Stage.PLANNING),
    (State.QUEUED, Stage.READY),
    (State.QUEUED, Stage.IMPLEMENTING),
    (State.QUEUED, Stage.REVIEW),
    (State.CLAIMED, Stage.BRAINSTORMING),
    (State.CLAIMED, Stage.DESIGN),
    (State.CLAIMED, Stage.PLANNING),
    (State.CLAIMED, Stage.READY),
    (State.CLAIMED, Stage.IMPLEMENTING),
    (State.CLAIMED, Stage.REVIEW),
    (State.CLAIMED, Stage.DONE),
    (State.RUNNING, Stage.IMPLEMENTING),
    (State.RETRYING, Stage.IMPLEMENTING),
    (State.COMPLETED, Stage.IMPLEMENTING),
    (State.COMPLETED, Stage.REVIEW),
    (State.COMPLETED, Stage.DONE),
    (State.FAILED, Stage.BRAINSTORMING),
    (State.FAILED, Stage.DESIGN),
    (State.FAILED, Stage.PLANNING),
    (State.FAILED, Stage.IMPLEMENTING),
    (State.FAILED, Stage.REVIEW),
    (State.CANCELLED, Stage.BRAINSTORMING),
    (State.CANCELLED, Stage.DESIGN),
    (State.CANCELLED, Stage.PLANNING),
    (State.CANCELLED, Stage.READY),
    (State.CANCELLED, Stage.IMPLEMENTING),
    (State.CANCELLED, Stage.REVIEW),
}


def is_legal_combo(state: State, stage: Stage) -> bool:
    return (state, stage) in LEGAL_COMBOS


def is_fully_done(state: State, stage: Stage) -> bool:
    """Q1 锁定：完成终态 = state=completed AND stage=done。统计/通知/最终完成时间用。"""
    return state == State.COMPLETED and stage == Stage.DONE


def initial_state() -> Tuple[State, Stage]:
    return (State.QUEUED, Stage.BRAINSTORMING)


def is_valid_stage_move(state: State, from_stage: Stage, to_stage: Stage) -> bool:
    """自由拖拽：非终态的 stage 跳转合法（状态不变），但有特殊限制。

    move_to_stage 专用。不改变 state。
    终态 (completed,done) 不可移动。cancelled 是 state 不是 stage，不在此校验。
    done 只能从 review 进入（T5+T6），不能跳跃也不能通过 T18 stage-only 到达。
    """
    if state == State.CANCELLED:
        return False
    if state == State.COMPLETED and from_stage == Stage.DONE:
        return False
    # done 的所有限制：只能从 review 经 T5+T6 到达
    if to_stage == Stage.DONE:
        return False
    # 只要 from combo 合法，任意 stage 跳转均可（T18 兜底）
    return (state, from_stage) in LEGAL_COMBOS


# ---------- Transitions ----------
@dataclass(frozen=True)
class Transition:
    id: str
    from_state: State
    from_stage: Stage
    to_state: State
    to_stage: Stage
    allowed_actors: Set[ActorType]
    requires_reason: bool = False
    terminal: bool = False
    event_type: str = "state_changed"
    increments_bounce: bool = False
    description: str = ""


_EXPLICIT: list = [
    Transition("T1", State.QUEUED, Stage.BRAINSTORMING, State.CLAIMED, Stage.BRAINSTORMING,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, event_type="claimed"),
    Transition("T1", State.QUEUED, Stage.DESIGN, State.CLAIMED, Stage.DESIGN,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, event_type="claimed"),
    Transition("T1", State.QUEUED, Stage.PLANNING, State.CLAIMED, Stage.PLANNING,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, event_type="claimed"),
    Transition("T1", State.QUEUED, Stage.READY, State.CLAIMED, Stage.READY,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, event_type="claimed"),
    Transition("T1", State.QUEUED, Stage.IMPLEMENTING, State.CLAIMED, Stage.IMPLEMENTING,
               {ActorType.AGENT, ActorType.SYSTEM}, event_type="claimed"),
    Transition("T2", State.CLAIMED, Stage.IMPLEMENTING, State.RUNNING, Stage.IMPLEMENTING,
               {ActorType.AGENT, ActorType.SYSTEM}, event_type="started"),
    Transition("T3", State.RUNNING, Stage.IMPLEMENTING, State.COMPLETED, Stage.IMPLEMENTING,
               {ActorType.AGENT}, event_type="completed"),
    Transition("T4", State.COMPLETED, Stage.IMPLEMENTING, State.COMPLETED, Stage.REVIEW,
               {ActorType.SYSTEM, ActorType.USER}, event_type="stage_changed"),
    Transition("T5", State.CLAIMED, Stage.REVIEW, State.COMPLETED, Stage.REVIEW,
               {ActorType.USER, ActorType.SYSTEM}, event_type="completed"),
    Transition("T5", State.QUEUED, Stage.REVIEW, State.COMPLETED, Stage.REVIEW,
               {ActorType.USER, ActorType.SYSTEM}, event_type="completed"),
    Transition("T6", State.COMPLETED, Stage.REVIEW, State.COMPLETED, Stage.DONE,
               {ActorType.SYSTEM, ActorType.USER}, event_type="stage_changed", terminal=True),
    Transition("T7", State.CLAIMED, Stage.REVIEW, State.FAILED, Stage.REVIEW,
               {ActorType.USER}, requires_reason=True, event_type="failed"),
    Transition("T8", State.FAILED, Stage.REVIEW, State.QUEUED, Stage.IMPLEMENTING,
               {ActorType.SYSTEM}, event_type="bounced", increments_bounce=True),
    Transition("T9", State.FAILED, Stage.IMPLEMENTING, State.RETRYING, Stage.IMPLEMENTING,
               {ActorType.SYSTEM, ActorType.AGENT, ActorType.USER}, event_type="retried"),
    Transition("T10", State.RETRYING, Stage.IMPLEMENTING, State.RUNNING, Stage.IMPLEMENTING,
               {ActorType.AGENT, ActorType.SYSTEM}, event_type="state_changed"),
    Transition("T11", State.CLAIMED, Stage.BRAINSTORMING, State.CLAIMED, Stage.DESIGN,
               {ActorType.USER, ActorType.SYSTEM}, event_type="stage_changed"),
    Transition("T11", State.CLAIMED, Stage.DESIGN, State.CLAIMED, Stage.PLANNING,
               {ActorType.USER, ActorType.SYSTEM}, event_type="stage_changed"),
    Transition("T11", State.CLAIMED, Stage.PLANNING, State.CLAIMED, Stage.READY,
               {ActorType.USER, ActorType.SYSTEM}, event_type="stage_changed"),
    Transition("T12", State.CLAIMED, Stage.READY, State.CLAIMED, Stage.IMPLEMENTING,
               {ActorType.AGENT, ActorType.SYSTEM}, event_type="stage_changed"),
    # T17 manual_advance：人工推进阶段（不改变 state），仅 from queued
    Transition("T17", State.QUEUED, Stage.BRAINSTORMING, State.QUEUED, Stage.DESIGN,
               {ActorType.USER, ActorType.SYSTEM}, event_type="stage_changed"),
    Transition("T17", State.QUEUED, Stage.DESIGN, State.QUEUED, Stage.PLANNING,
               {ActorType.USER, ActorType.SYSTEM}, event_type="stage_changed"),
    Transition("T17", State.QUEUED, Stage.PLANNING, State.QUEUED, Stage.READY,
               {ActorType.USER, ActorType.SYSTEM}, event_type="stage_changed"),
    Transition("T13", State.CLAIMED, Stage.BRAINSTORMING, State.FAILED, Stage.BRAINSTORMING,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, requires_reason=True, event_type="failed"),
    Transition("T13", State.CLAIMED, Stage.DESIGN, State.FAILED, Stage.DESIGN,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, requires_reason=True, event_type="failed"),
    Transition("T13", State.CLAIMED, Stage.PLANNING, State.FAILED, Stage.PLANNING,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, requires_reason=True, event_type="failed"),
    Transition("T13", State.CLAIMED, Stage.IMPLEMENTING, State.FAILED, Stage.IMPLEMENTING,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, requires_reason=True, event_type="failed"),
    Transition("T13", State.RUNNING, Stage.IMPLEMENTING, State.FAILED, Stage.IMPLEMENTING,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, requires_reason=True, event_type="failed"),
    Transition("T13", State.RETRYING, Stage.IMPLEMENTING, State.FAILED, Stage.IMPLEMENTING,
               {ActorType.AGENT, ActorType.USER, ActorType.SYSTEM}, requires_reason=True, event_type="failed"),
    # T16 manual_retry：人工重置，从 FAILED/RETRYING 任意阶段 → (QUEUED, READY)
    Transition("T16", State.FAILED, Stage.BRAINSTORMING, State.QUEUED, Stage.READY,
               {ActorType.USER}, requires_reason=True, event_type="reopened"),
    Transition("T16", State.FAILED, Stage.DESIGN, State.QUEUED, Stage.READY,
               {ActorType.USER}, requires_reason=True, event_type="reopened"),
    Transition("T16", State.FAILED, Stage.PLANNING, State.QUEUED, Stage.READY,
               {ActorType.USER}, requires_reason=True, event_type="reopened"),
    Transition("T16", State.FAILED, Stage.IMPLEMENTING, State.QUEUED, Stage.READY,
               {ActorType.USER}, requires_reason=True, event_type="reopened"),
    Transition("T16", State.FAILED, Stage.REVIEW, State.QUEUED, Stage.READY,
               {ActorType.USER}, requires_reason=True, event_type="reopened"),
    Transition("T16", State.RETRYING, Stage.IMPLEMENTING, State.QUEUED, Stage.READY,
               {ActorType.USER}, requires_reason=True, event_type="reopened"),
]


def _gen_cancel() -> list:
    out = []
    for s, st in sorted(LEGAL_COMBOS, key=lambda x: (x[0].value, x[1].value)):
        if (State.CANCELLED, st) not in LEGAL_COMBOS or s == State.CANCELLED:
            continue
        out.append(Transition("T14", s, st, State.CANCELLED, st,
                              {ActorType.USER, ActorType.SYSTEM}, event_type="cancelled"))
    return out


def _gen_reopen() -> list:
    out = []
    for s in (State.CANCELLED, State.FAILED):
        for st in Stage:
            if st in (Stage.REVIEW, Stage.DONE):
                continue  # review/done 不可 reopen（done 是终态入口，review 走 T5+T6）
            if (s, st) not in LEGAL_COMBOS or (State.QUEUED, st) not in LEGAL_COMBOS:
                continue
            out.append(Transition("T15", s, st, State.QUEUED, st,
                                  {ActorType.USER}, event_type="reopened"))
    return out


TRANSITIONS: list = _EXPLICIT + _gen_cancel() + _gen_reopen()
TRANSITION_INDEX: dict = {(t.from_state, t.from_stage, t.to_state, t.to_stage): t for t in TRANSITIONS}


def find_transition(from_state, from_stage, to_state, to_stage):
    return TRANSITION_INDEX.get((from_state, from_stage, to_state, to_stage))


class IllegalTransition(Exception):
    def __init__(self, from_state, from_stage, to_state, to_stage, reason="非法状态转换"):
        self.from_state = from_state
        self.from_stage = from_stage
        self.to_state = to_state
        self.to_stage = to_stage
        self.reason = reason
        super().__init__(
            f"{reason}: ({from_state.value},{from_stage.value}) -> "
            f"({to_state.value},{to_stage.value})"
        )


def validate_transition(from_state, from_stage, to_state, to_stage, actor_type):
    t = find_transition(from_state, from_stage, to_state, to_stage)
    if t is None:
        # Fallback: 自由拖拽 stage-only 跳转（同 state、不同 stage、非终态）
        if (from_state == to_state
                and from_stage != to_stage
                and is_valid_stage_move(from_state, from_stage, to_stage)):
            t = Transition(
                id="T18_manual_move",
                from_state=from_state, from_stage=from_stage,
                to_state=to_state, to_stage=to_stage,
                allowed_actors={ActorType.USER, ActorType.AGENT, ActorType.SYSTEM},
                event_type="stage_changed",
            )
        else:
            # 显式校验目标组合
            if not is_legal_combo(to_state, to_stage):
                raise IllegalTransition(
                    from_state, from_stage, to_state, to_stage,
                    f"目标组合非法 ({to_state.value},{to_stage.value})",
                )
            raise IllegalTransition(from_state, from_stage, to_state, to_stage, "未定义的转换")
    if actor_type not in t.allowed_actors:
        raise IllegalTransition(
            from_state, from_stage, to_state, to_stage,
            f"actor_type={actor_type.value} 无权执行 {t.id}",
        )
    return t


# ---------- Composite status (前后端共享映射表) ----------
COMPOSITE_LABEL: dict = {
    (State.QUEUED, Stage.BRAINSTORMING): ("待认领 · 需求理解", "neutral"),
    (State.QUEUED, Stage.DESIGN): ("待认领 · 设计", "neutral"),
    (State.QUEUED, Stage.PLANNING): ("待认领 · 计划", "neutral"),
    (State.QUEUED, Stage.READY): ("待认领", "neutral"),
    (State.QUEUED, Stage.IMPLEMENTING): ("返工重排", "warn"),
    (State.CLAIMED, Stage.BRAINSTORMING): ("需求理解中", "neutral"),
    (State.CLAIMED, Stage.DESIGN): ("设计中", "neutral"),
    (State.CLAIMED, Stage.PLANNING): ("计划中", "neutral"),
    (State.CLAIMED, Stage.READY): ("就绪 · 待执行", "neutral"),
    (State.CLAIMED, Stage.IMPLEMENTING): ("已认领 · 实现", "neutral"),
    (State.CLAIMED, Stage.REVIEW): ("评审中", "neutral"),
    (State.RUNNING, Stage.IMPLEMENTING): ("执行中", "live"),
    (State.RETRYING, Stage.IMPLEMENTING): ("重试中", "warn"),
    (State.COMPLETED, Stage.IMPLEMENTING): ("实现完成 · 待评审", "ok-soft"),
    (State.COMPLETED, Stage.REVIEW): ("评审通过 · 待归并", "ok-soft"),
    (State.COMPLETED, Stage.DONE): ("已完成", "ok"),
    (State.FAILED, Stage.BRAINSTORMING): ("失败 · 需求理解", "danger"),
    (State.FAILED, Stage.DESIGN): ("失败 · 设计", "danger"),
    (State.FAILED, Stage.PLANNING): ("失败 · 计划", "danger"),
    (State.FAILED, Stage.IMPLEMENTING): ("失败 · 实现", "danger"),
    (State.FAILED, Stage.REVIEW): ("失败 · 评审不通过", "danger"),
    (State.CANCELLED, Stage.BRAINSTORMING): ("已取消", "muted"),
    (State.CANCELLED, Stage.DESIGN): ("已取消", "muted"),
    (State.CANCELLED, Stage.PLANNING): ("已取消", "muted"),
    (State.CANCELLED, Stage.READY): ("已取消", "muted"),
    (State.CANCELLED, Stage.IMPLEMENTING): ("已取消", "muted"),
    (State.CANCELLED, Stage.REVIEW): ("已取消", "muted"),
}


def composite_status(state, stage, block_reason=None):
    terminal = is_fully_done(state, stage)
    if state == State.QUEUED and block_reason:
        short = (block_reason[:24] + "…") if len(block_reason) > 24 else block_reason
        return {"label": f"等待 · {short}", "tone": "warn", "is_terminal": terminal}
    lt = COMPOSITE_LABEL.get((state, stage))
    if lt is None:
        return {"label": "未知", "tone": "muted", "is_terminal": terminal}
    label, tone = lt
    return {"label": label, "tone": tone, "is_terminal": terminal}


def export_mapping_json():
    """供前端复制的单一映射表（state/stage/actor 枚举、合法组合、综合状态、终态规则）。"""
    return {
        "states": [s.value for s in State],
        "stages": [s.value for s in Stage],
        "actor_types": [a.value for a in ActorType],
        "legal_combos": [
            {"state": s.value, "stage": st.value}
            for s, st in sorted(LEGAL_COMBOS, key=lambda x: (x[0].value, x[1].value))
        ],
        "composite": {
            f"{s.value}|{st.value}": {"label": label, "tone": tone}
            for (s, st), (label, tone) in COMPOSITE_LABEL.items()
        },
        "is_fully_done_check": "state==completed AND stage==done",
        "is_terminal_check_legacy": "scheduling-terminal (cancelled/failed/done) — see is_terminal()",
        "transition_count": len(TRANSITIONS),
    }
