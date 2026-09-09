# mio_taskhub/composite.py
"""综合状态标签 + 前端映射导出。"""
from __future__ import annotations

from mio_taskhub.state_machine import (
    State, Stage, ActorType,
    LEGAL_COMBOS, is_fully_done, TRANSITIONS,
)


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
