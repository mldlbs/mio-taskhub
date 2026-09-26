# -*- coding: utf-8 -*-
"""Mio Agent Runtime 只读端点（低耦合：仅读 MIO_HOME，不写、不依赖 Node）。

- GET  /api/v1/mio/status     — 运行时概览（配置安全子集 / 观察器状态 / 文件计数 / CLI 可用性）
- GET  /api/v1/mio/traces     — 最近 trace（倒序，容错）
- GET  /api/v1/mio/memory     — 最近记忆（倒序，容错）
- GET  /api/v1/mio/creativity — 创意假设（status + 三维评分）
- GET  /api/v1/mio/insight    — 洞察（生成走 MCP）
- GET  /api/v1/mio/ferment    — 发酵映射：Mio 假设 → taskhub idea 生命周期建议（只读）
- POST /api/v1/mio/ferment/{hyp_id}/sync — 把假设同步为 taskhub 想法（幂等，带 mio-hyp 关联标签）
- GET  /api/v1/mio/contract   — 契约冒烟自检结果（缓存；ContractJob 每 60min 跑一次；?run=1 立即重跑）
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from mio_taskhub import mio_runtime as mio
from mio_taskhub.db import get_session
from mio_taskhub.events import emit_event
from mio_taskhub.models import Idea, IdeaStatus

router = APIRouter(prefix="/api/v1/mio", tags=["mio-runtime"])


class PolicyCheckBody(BaseModel):
    action: str
    project: str | None = None
    timeout: float = 3.0

# Mio 假设状态 → taskhub idea 生命周期（分工：Mio 管发酵，taskhub 管生命周期）
FERMENT_MAP = {
    "draft": "new",         # 草稿 → 记录中
    "active": "fermenting", # 发酵中 → 发酵中
    "validated": "formed",  # 已验证 → 已成形
    "rejected": "cancelled",# 已否决 → 已取消
}
# taskhub 普通 idea 进度链（can_advance 只允许相邻推进）
_PROGRESS = ["new", "fermenting", "formed", "broken_down"]


def _status_val(s) -> str:
    return s.value if hasattr(s, "value") else str(s)


def _action_for(cur: str, suggested: str, idea) -> dict | None:
    """算出当前可执行的下一步（多步目标给相邻一步；无路可走 → None）。"""
    if not suggested or cur == suggested:
        return None
    try:
        src, dst = IdeaStatus(cur), IdeaStatus(suggested)
    except ValueError:
        return None
    if IdeaStatus.can_advance(src, dst):
        return {"to": suggested, "toward": suggested, "idea_id": idea.id}
    # 非相邻（如 new → formed）：朝目标走一步
    if suggested in _PROGRESS and cur in _PROGRESS:
        si, di = _PROGRESS.index(cur), _PROGRESS.index(suggested)
        if di > si + 1 and si + 1 < len(_PROGRESS):
            nxt = _PROGRESS[si + 1]
            if IdeaStatus.can_advance(src, IdeaStatus(nxt)):
                return {"to": nxt, "toward": suggested, "idea_id": idea.id}
    return None


def _ferment_items(hypotheses: list, ideas: list) -> list:
    """关联（label mio-hyp:<id> 优先，其次精确标题）+ 映射 + 可执行动作。"""
    by_label, by_title = {}, {}
    for i in ideas:
        for lb in (i.labels or []):
            if isinstance(lb, str) and lb.startswith("mio-hyp:"):
                by_label[lb[len("mio-hyp:"):]] = i
        t = (i.title or "").strip()
        if t:
            by_title.setdefault(t, i)

    items = []
    for h in hypotheses:
        hid = h.get("id")
        title = (h.get("title") or "").strip()
        linked = by_label.get(hid) or (by_title.get(title) if title else None)
        suggested = FERMENT_MAP.get(h.get("status"))
        idea_json, action = None, None
        if linked is not None:
            cur = _status_val(linked.status)
            idea_json = {"id": linked.id, "title": linked.title, "status": cur}
            action = _action_for(cur, suggested, linked)
        items.append({
            "id": hid, "title": h.get("title"), "status": h.get("status"),
            "novelty": h.get("novelty"), "feasibility": h.get("feasibility"),
            "impact": h.get("impact"), "score": h.get("score"),
            "suggested_status": suggested,
            "linked": linked is not None,
            "idea": idea_json,
            "action": action,
        })
    return items


@router.get("/status")
def mio_status():
    return mio.status()


@router.get("/traces")
def mio_traces(limit: int = Query(20, ge=1, le=200)):
    return mio.traces(limit)


@router.get("/memory")
def mio_memory(limit: int = Query(20, ge=1, le=200)):
    return mio.memory(limit)


@router.get("/creativity")
def mio_creativity(limit: int = Query(20, ge=1, le=100)):
    """创意假设（只读）：Mio creativity 引擎的 status 计数 + 假设列表（含三维评分）。"""
    return mio.creativity(limit)


@router.get("/insight")
def mio_insight(limit: int = Query(20, ge=1, le=100)):
    """洞察（只读）：Mio insight 引擎的 status 计数 + 洞察列表（生成走 MCP）。"""
    return mio.insight(limit)


@router.post("/policy/check")
def mio_policy_check(body: PolicyCheckBody):
    """历史风险预检（只读、fail-open）：动作执行前先问一句要不要 confirm。

    与 /api/memory/policy/check（taskhub 内置占位规则）互补：这里查的是
    Mio 跨 agent 历史失败记录；5 个危险调用点内联用的是同一个 policy_guard。
    """
    action = body.action.strip()
    if not action:
        raise HTTPException(400, "action is required")
    return mio.policy_check(action, body.project, timeout=body.timeout)


@router.get("/contract")
def mio_contract(run: bool = Query(False)):
    """契约冒烟自检：断言 mio CLI 关键输出字段，捕获 runtime 升级造成的静默契约漂移。

    默认返回最近一次缓存（后台 ContractJob 定期跑）；`?run=1` 立即重跑一次
    （4 条只读 CLI，阻塞数秒）。失败详情同时喂给 /api/v1/alerts 的 MioContractDrift。
    """
    if run:
        return mio.contract_check()
    last = mio.contract_last()
    if last is not None:
        return last
    return {"ok": None, "available": mio.available(), "checked_at": None,
            "epoch": None, "probes": [],
            "hint": "not checked yet; ?run=1 to run now"}


@router.get("/ferment")
def mio_ferment(limit: int = Query(50, ge=1, le=100),
                db: Session = Depends(get_session)):
    """发酵映射（只读）：假设 ↔ idea 关联 + 生命周期建议 + 可执行的下一步。

    不自动推进——推进由前端显式调用既有 /ideas/{id}/status（人工确认）。
    """
    cr = mio.creativity(limit)
    ideas = list(db.exec(select(Idea)).all())
    items = _ferment_items(cr.get("items") or [], ideas)
    return {
        "available": cr.get("available", False),
        "mapping": FERMENT_MAP,
        "items": items,
        "counts": {
            "total": len(items),
            "linked": sum(1 for x in items if x["linked"]),
            "pending_actions": sum(1 for x in items if x["action"]),
        },
    }


@router.post("/ferment/{hyp_id}/sync")
def mio_ferment_sync(hyp_id: str, db: Session = Depends(get_session)):
    """把 Mio 假设同步为 taskhub 想法（幂等：已同步 → 返回已有，不重复建）。"""
    cr = mio.creativity(100)
    hyp = next((x for x in (cr.get("items") or []) if x.get("id") == hyp_id), None)
    if hyp is None:
        raise HTTPException(404, "hypothesis not found (or creativity unavailable)")

    label = f"mio-hyp:{hyp_id}"
    for i in db.exec(select(Idea)).all():
        if label in (i.labels or []):
            return {"created": False, "already": True,
                    "idea": {"id": i.id, "title": i.title,
                             "status": _status_val(i.status)}}

    idea = Idea(
        title=(hyp.get("title") or "Mio 假设").strip(),
        description=(
            f"{hyp.get('idea') or ''}\n\n---\n"
            f"来源：Mio creativity 假设（status={hyp.get('status')}, "
            f"Σscore={hyp.get('score')}，N{hyp.get('novelty')}/"
            f"F{hyp.get('feasibility')}/I{hyp.get('impact')}）\n"
            f"假设 id：{hyp_id}"
        ),
        project="",
        labels=["mio-intelligence", label,
                f"mio-status:{hyp.get('status')}"],
    )
    db.add(idea)
    db.add(emit_event(db, type="idea_created", entity="idea",
                      entity_id=idea.id, payload={"title": idea.title,
                                                  "source": "mio-ferment"}))
    db.commit()
    db.refresh(idea)
    return {"created": True, "already": False,
            "idea": {"id": idea.id, "title": idea.title,
                     "status": _status_val(idea.status)}}
