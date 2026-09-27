"""想法驾驶舱聚合端点（想法落地闭环 P0，FR-3/FR-4）。

- 每区块独立超时（默认 1s，hypotheses 3s 预留给 Mio 跨服务调用）；
- 总预算 5s：未就绪区块裁剪为 degraded，已就绪照常返回；
- 单区块失败仅该区块 status=degraded，接口整体不 500；
- 禁止整包 degraded 字段——前端按 sections[x].status 只灰对应区块。
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from mio_taskhub.db import get_session
from mio_taskhub.models import Idea
from mio_taskhub.next_action import compute_next_action, dismiss_rule, is_high_risk
from mio_taskhub.utils import _now

router = APIRouter(prefix="/ideas", tags=["cockpit"])

# 区块级超时预算（秒）；缺省键 "default" 兜底。P1 接入 Mio 时 hypotheses 已预留 3s。
SECTION_TIMEOUTS = {"default": 1.0, "hypotheses": 3.0}
TOTAL_BUDGET = 5.0


# ---------- P0 区块构建器（FR-3：仅 idea 字段区真实聚合，其余空壳 status=ok） ----------

async def _build_goal(idea: Idea, db: Session) -> dict:
    return {"data": {
        "goal": idea.goal or "",
        "success_metric": idea.success_metric or "",
        "constraints": idea.constraints or "",
        "out_of_scope": idea.out_of_scope or "",
    }}


async def _build_hypotheses(idea: Idea, db: Session) -> dict:
    # P0 空壳；P1 关联 Mio creativity 假设 + 分数（超时 3s / 缓存 5min）
    return {"data": {"items": [], "source": "stub"}}


async def _build_mvp(idea: Idea, db: Session) -> dict:
    return {"data": {"mvp_scope": idea.mvp_scope or ""}}


async def _build_tasks(idea: Idea, db: Session) -> dict:
    """任务图 P0（FR-9）：直接关联 + 一层下游（谁依赖我）；有环降级列表；>20 折叠。"""
    from sqlmodel import select
    from mio_taskhub.models import Task

    rows = db.exec(select(Task.id, Task.title, Task.state, Task.stage,
                          Task.block_reason, Task.idea_id, Task.depends_on)).all()
    direct = [r for r in rows if r.idea_id == idea.id]
    direct_ids = {r.id for r in direct}
    downstream = [r for r in rows
                  if r.id not in direct_ids
                  and any(d in direct_ids for d in (r.depends_on or []))]
    node_ids = direct_ids | {r.id for r in downstream}

    edges = []
    for r in direct + downstream:
        for d in (r.depends_on or []):
            if d in node_ids and d != r.id:
                edges.append({"from": d, "to": r.id})

    has_cycle = _has_cycle(node_ids, edges)

    def _item(r, is_downstream):
        return {"id": r.id, "title": r.title,
                "state": r.state.value if hasattr(r.state, "value") else str(r.state),
                "stage": r.stage.value if hasattr(r.stage, "value") else str(r.stage),
                "blocked": bool(r.block_reason) or str(getattr(r.state, "value", r.state)) == "blocked_failed",
                "downstream": is_downstream}

    items = [_item(r, False) for r in direct] + [_item(r, True) for r in downstream]
    graph = None
    warning = None
    if has_cycle:
        warning = "检测到依赖环，已降级为任务列表"
    else:
        graph = {"nodes": [{"id": r.id, "title": r.title,
                            "kind": "downstream" if r in downstream else "direct"}
                           for r in direct + downstream],
                 "edges": edges}
    return {"data": {
        "items": items,
        "graph": graph,
        "has_cycle": has_cycle,
        "warning": warning,
        "total": len(items),
        "folded": len(items) > 20,
    }}


def _has_cycle(node_ids, edges) -> bool:
    """DFS 染色检测依赖环（P0 简化：只看节点集内边）。"""
    graph = {n: [] for n in node_ids}
    for e in edges:
        if e["from"] in graph and e["to"] in graph:
            graph[e["from"]].append(e["to"])
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in node_ids}

    def dfs(u):
        color[u] = GRAY
        for v in graph.get(u, ()):
            if color[v] == GRAY:
                return True
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    return any(color[n] == WHITE and dfs(n) for n in node_ids)


async def _build_risks(idea: Idea, db: Session) -> dict:
    return {"data": {"items": idea.risks if isinstance(idea.risks, list) else []}}


async def _build_approvals(idea: Idea, db: Session) -> dict:
    # P0 空壳；汇总关联任务 doc_statuses 状态由后续迭代填充
    return {"data": {"items": []}}


async def _build_retrospective(idea: Idea, db: Session) -> dict:
    # P0 空壳；P3 复盘区（task_outcome + 评审记录）
    return {"data": {"items": []}}


SECTION_BUILDERS = {
    "goal": _build_goal,
    "hypotheses": _build_hypotheses,
    "mvp": _build_mvp,
    "tasks": _build_tasks,
    "risks": _build_risks,
    "approvals": _build_approvals,
    "retrospective": _build_retrospective,
}


async def build_sections(idea: Idea, db: Session) -> dict:
    """并行聚合所有区块；单区异常/超时只降级该区；总预算裁剪未就绪区。"""
    pending = {}
    for name, builder in SECTION_BUILDERS.items():
        timeout = SECTION_TIMEOUTS.get(name, SECTION_TIMEOUTS.get("default", 1.0))
        fut = asyncio.ensure_future(asyncio.wait_for(builder(idea, db), timeout=timeout))
        pending[fut] = name

    done, _ = await asyncio.wait(pending, timeout=TOTAL_BUDGET)

    results = {}
    for fut, name in pending.items():
        if fut not in done:
            fut.cancel()
            results[name] = {"status": "degraded", "reason": "total_timeout"}
            continue
        try:
            payload = fut.result()
            entry = {"status": "ok", "data": payload.get("data", {})}
            if payload.get("cached_at") is not None:
                entry["cached_at"] = payload["cached_at"]
            results[name] = entry
        except asyncio.TimeoutError:
            results[name] = {"status": "degraded", "reason": "timeout"}
        except Exception as e:  # noqa: BLE001 —— 单区失败不拖垮整包（FR-4）
            results[name] = {"status": "degraded", "reason": type(e).__name__}
    return results


@router.get("/{idea_id}/cockpit")
async def idea_cockpit(idea_id: str, user: str = Query("local"), db: Session = Depends(get_session)):
    idea = db.get(Idea, idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    sections = await build_sections(idea, db)
    try:
        next_action = compute_next_action(db, idea, user=user)
    except Exception:  # noqa: BLE001 —— 规则引擎异常不拖垮驾驶舱（FR-4 精神）
        next_action = None
    return {
        "idea_id": idea_id,
        "generated_at": _now().isoformat(),
        "sections": sections,
        "next_action": next_action,
        "high_risk": is_high_risk(idea),  # FR-8：供前端红队默认值等使用
    }


@router.post("/{idea_id}/next-action/dismiss")
def dismiss_idea_next_action(idea_id: str, body: dict, db: Session = Depends(get_session)):
    """FR-7：服务端记录 dismiss；condition_snapshot 变化即复活，7 天过期。"""
    idea = db.get(Idea, idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    rule_id = (body.get("rule_id") or "").strip()
    if not rule_id:
        raise HTTPException(422, "rule_id is required")
    user = (body.get("user") or "local").strip() or "local"
    try:
        pref = dismiss_rule(db, idea, rule_id, user)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "rule_id": pref.rule_id,
            "dismissed_at": pref.dismissed_at.isoformat(),
            "condition_snapshot": pref.condition_snapshot}
