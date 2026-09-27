"""下一步动作规则引擎（想法落地闭环 P0，FR-6/FR-7/FR-8）。

- 5 级默认优先级序，按序取第一条命中；MIO_NEXT_ACTION_ORDER 逗号分隔可覆盖（NFR-4）；
- dismiss 存服务端 IdeaUserPref：condition_snapshot 为结构化布尔位（不存文本）；
  未过期且 snapshot 相同 → 跳过；snapshot 变化 → 立即复活；7 天自然过期（FR-7）；
- 高风险 = idea.tags ∩ 词表（默认 高风险/合规/用户数据/花钱，MIO_IDEA_RISK_TAGS 覆盖），不做正文匹配（FR-8）。
"""
import os
from datetime import timedelta, timezone

from sqlmodel import Session, select

from mio_taskhub.models import Discussion, Idea, IdeaUserPref, Task, TaskState
from mio_taskhub.utils import _now

DEFAULT_ORDER = (
    "missing_goal",
    "doc_unapproved",
    "blocked_task",
    "unverified_high_risk_assumption",
    "missing_action_items",
)
DISMISS_TTL = timedelta(days=7)
RISK_TAGS_DEFAULT = ("高风险", "合规", "用户数据", "花钱")


def risk_tag_vocab() -> list:
    """高风险词表：环境变量逗号分隔覆盖，否则默认常量（P0 不入 DB，见设计注记 #1）。"""
    env = os.environ.get("MIO_IDEA_RISK_TAGS", "")
    if env.strip():
        return [t.strip() for t in env.split(",") if t.strip()]
    return list(RISK_TAGS_DEFAULT)


def next_action_order() -> list:
    """优先级序：环境变量覆盖默认 5 级序（顺序原则：先补方向，再解阻断，再验证假设，最后补记录）。"""
    env = os.environ.get("MIO_NEXT_ACTION_ORDER", "")
    if env.strip():
        return [s.strip() for s in env.split(",") if s.strip()]
    return list(DEFAULT_ORDER)


def is_high_risk(idea: Idea) -> bool:
    tags = [t for t in (idea.tags if isinstance(idea.tags, list) else []) if isinstance(t, str)]
    vocab = risk_tag_vocab()
    return any(t in vocab for t in tags)


def _build_ctx(db: Session, idea: Idea) -> dict:
    tasks = db.exec(select(Task).where(Task.idea_id == idea.id)).all()
    discussions = db.exec(select(Discussion).where(Discussion.idea_id == idea.id)).all()

    unapproved_docs = 0
    for t in tasks:
        for st in (t.doc_statuses or {}).values():
            state = st.get("state") if isinstance(st, dict) else st
            if state and state != "approved":
                unapproved_docs += 1

    blocked = [t for t in tasks if (t.block_reason or "") or t.state == TaskState.BLOCKED_FAILED]
    closed_empty_reviews = [d for d in discussions
                            if d.status == "closed" and not (d.conclusions or "").strip()]

    assumptions = idea.assumptions if isinstance(idea.assumptions, list) else []
    unverified = [a for a in assumptions
                  if not (isinstance(a, dict) and a.get("status") in ("validated", "rejected"))]

    return {
        "unapproved_docs": unapproved_docs,
        "blocked": blocked,
        "closed_empty_reviews": closed_empty_reviews,
        "unverified_assumptions": unverified,
    }


# ---------- 规则：返回 {snapshot, action, reason} 或 None（未命中） ----------

def _rule_missing_goal(idea: Idea, ctx: dict):
    goal_present = bool((idea.goal or "").strip())
    metric_present = bool((idea.success_metric or "").strip())
    if goal_present and metric_present:
        return None
    return {
        "snapshot": {"rule_id": "missing_goal", "goal_present": goal_present,
                     "metric_present": metric_present},
        "action": "补全目标与成功标准",
        "reason": "没有目标，后面都是空转" if not goal_present else "缺成功标准，无法判断是否达成",
    }


def _rule_doc_unapproved(idea: Idea, ctx: dict):
    if ctx["unapproved_docs"] <= 0:
        return None
    return {
        "snapshot": {"rule_id": "doc_unapproved", "has_unapproved_docs": True},
        "action": f"去审批（{ctx['unapproved_docs']} 个文档未批准）",
        "reason": "卡住执行链",
    }


def _rule_blocked_task(idea: Idea, ctx: dict):
    if not ctx["blocked"]:
        return None
    title = ctx["blocked"][0].title
    return {
        "snapshot": {"rule_id": "blocked_task", "has_blocked_task": True},
        "action": f"解除阻塞：{title}",
        "reason": "有任务被依赖/失败卡住",
    }


def _rule_unverified_high_risk(idea: Idea, ctx: dict):
    if not is_high_risk(idea) or not ctx["unverified_assumptions"]:
        return None
    return {
        "snapshot": {"rule_id": "unverified_high_risk_assumption", "high_risk": True,
                     "has_unverified_assumptions": True},
        "action": f"优先验证假设（{len(ctx['unverified_assumptions'])} 条未验证）",
        "reason": "高风险想法的未验证假设影响方向",
    }


def _rule_missing_action_items(idea: Idea, ctx: dict):
    if not ctx["closed_empty_reviews"]:
        return None
    return {
        "snapshot": {"rule_id": "missing_action_items",
                     "has_closed_review_without_items": True},
        "action": "补行动项（评审已结束但无结论/行动项）",
        "reason": "影响闭环",
    }


RULES = {
    "missing_goal": _rule_missing_goal,
    "doc_unapproved": _rule_doc_unapproved,
    "blocked_task": _rule_blocked_task,
    "unverified_high_risk_assumption": _rule_unverified_high_risk,
    "missing_action_items": _rule_missing_action_items,
}


def _aware_utc(dt):
    """SQLite 读回 naive（存储约定 UTC），统一为 aware UTC 再比较，避免 naive/aware 混比。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dismissed_alive(pref: IdeaUserPref, snapshot: dict, now) -> bool:
    """dismiss 记录仍有效：未过 7 天且 condition_snapshot 未变化（变化即复活）。"""
    dismissed_at = _aware_utc(pref.dismissed_at)
    if dismissed_at is None:
        return False
    if dismissed_at + DISMISS_TTL <= _aware_utc(now):
        return False
    return (pref.condition_snapshot or {}) == snapshot


def compute_next_action(db: Session, idea: Idea, user: str = "local", order: list = None):
    """按优先级序取第一条存活（未被有效 dismiss）的命中规则；无命中返回 None。"""
    order = order or next_action_order()
    ctx = _build_ctx(db, idea)
    prefs = {p.rule_id: p for p in db.exec(
        select(IdeaUserPref).where(IdeaUserPref.idea_id == idea.id,
                                   IdeaUserPref.user == user)).all()}
    now = _now()
    for rid in order:
        fn = RULES.get(rid)
        if fn is None:
            continue
        hit = fn(idea, ctx)
        if hit is None:
            continue
        pref = prefs.get(rid)
        if pref is not None and _dismissed_alive(pref, hit["snapshot"], now):
            continue
        return {"rule_id": rid, "action": hit["action"], "reason": hit["reason"],
                "snapshot": hit["snapshot"]}
    return None


def dismiss_rule(db: Session, idea: Idea, rule_id: str, user: str = "local") -> IdeaUserPref:
    """记录 dismiss（服务端权威：以当前命中的 snapshot 落库）。规则未命中 → ValueError。"""
    fn = RULES.get(rule_id)
    if fn is None:
        raise ValueError(f"unknown rule: {rule_id}")
    hit = fn(idea, _build_ctx(db, idea))
    if hit is None:
        raise ValueError(f"rule {rule_id} not matched, nothing to dismiss")
    pref = db.exec(select(IdeaUserPref).where(IdeaUserPref.idea_id == idea.id,
                                              IdeaUserPref.user == user,
                                              IdeaUserPref.rule_id == rule_id)).first()
    if pref is None:
        pref = IdeaUserPref(idea_id=idea.id, user=user, rule_id=rule_id)
    pref.dismissed_at = _now()
    pref.condition_snapshot = hit["snapshot"]
    db.add(pref)
    db.commit()
    db.refresh(pref)
    return pref
