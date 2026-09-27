"""想法驾驶舱聚合端点（想法落地闭环 P0 FR-3/FR-4，P1 FR-13~FR-16）。

- 每区块独立超时（默认 1s，hypotheses 3s 预留给 Mio 跨服务调用）；
- 总预算 5s：未就绪区块裁剪为 degraded，已就绪照常返回；
- 单区块失败仅该区块 status=degraded，接口整体不 500；
- 禁止整包 degraded 字段——前端按 sections[x].status 只灰对应区块；
- P1：hypotheses 接 Mio creativity（进程内 TTL 5min 缓存；断链 hyp 标 broken 不静默移除）。
"""
import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from mio_taskhub import mio_runtime
from mio_taskhub.db import get_session
from mio_taskhub.models import Idea
from mio_taskhub.next_action import compute_next_action, dismiss_rule, is_high_risk
from mio_taskhub.utils import _now

router = APIRouter(prefix="/ideas", tags=["cockpit"])

# 区块级超时预算（秒）；缺省键 "default" 兜底。hypotheses 3s = Mio 跨服务预算（FR-16）。
SECTION_TIMEOUTS = {"default": 1.0, "hypotheses": 3.0}
TOTAL_BUDGET = 5.0


class SectionDegraded(Exception):
    """单区块显式降级（reason 透传到 sections[x].reason，而非异常类名）。"""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# 假设区进程内缓存：key=关联 hyp id 集合 → (epoch, iso, items)；TTL 5min，重启失效属预期（FR-16）
HYP_TTL = 300.0
_HYP_CACHE: dict = {}


# ---------- P0 区块构建器（FR-3：仅 idea 字段区真实聚合，其余空壳 status=ok） ----------

async def _build_goal(idea: Idea, db: Session) -> dict:
    return {"data": {
        "goal": idea.goal or "",
        "success_metric": idea.success_metric or "",
        "constraints": idea.constraints or "",
        "out_of_scope": idea.out_of_scope or "",
    }}


async def _build_hypotheses(idea: Idea, db: Session) -> dict:
    """FR-13/FR-14/FR-16：透传关联假设的三元分 + 断链 broken 标记 + 3s 超时/5min 缓存。

    Mio 不可用/CLI 失败 → SectionDegraded（仅本区 degraded，接口不 500）。
    """
    raw = idea.hypotheses if isinstance(idea.hypotheses, list) else []
    ids = [x for x in raw if isinstance(x, str) and x.strip()]
    if not ids:
        return {"data": {"items": [], "source": "none", "total": 0}}

    key = tuple(sorted(set(ids)))
    now = time.time()
    hit = _HYP_CACHE.get(key)
    if hit and now - hit[0] < HYP_TTL:
        _, iso, items = hit
        return {"data": {"items": items, "source": "mio", "total": len(items)},
                "cached_at": iso}

    try:
        cr = await asyncio.to_thread(mio_runtime.creativity,
                                     limit=100, timeout=2.5, with_status=False)
    except Exception:  # noqa: BLE001 —— 跨服务异常一律按超时降级
        raise SectionDegraded("mio_timeout")
    if not cr.get("available"):
        raise SectionDegraded("mio_unavailable")
    if not cr.get("ok", True):
        raise SectionDegraded("mio_timeout" if cr.get("reason") == "cli_failed"
                              else "mio_unavailable")

    by_id = {h.get("id"): h for h in (cr.get("items") or []) if isinstance(h, dict)}
    items = []
    for hid in ids:  # 保 idea 关联顺序
        h = by_id.get(hid)
        if h is None:
            items.append({"id": hid, "broken": True})  # FR-14：断链标 broken，不静默移除
        else:
            items.append({
                "id": hid,
                "title": h.get("title") or "",
                "status": h.get("status") or "",
                "novelty": h.get("novelty"),
                "feasibility": h.get("feasibility"),
                "impact": h.get("impact"),
                "score": h.get("score"),
                "broken": False,
            })

    iso = datetime.now(timezone.utc).isoformat()
    _HYP_CACHE[key] = (now, iso, items)
    return {"data": {"items": items, "source": "mio", "total": len(items)},
            "cached_at": iso}


async def _build_mvp(idea: Idea, db: Session) -> dict:
    return {"data": {"mvp_scope": idea.mvp_scope or ""}}


async def _build_tasks(idea: Idea, db: Session) -> dict:
    """任务图（P0 FR-9 + P3 FR-26）：直接关联为根，多层上游/下游闭包；
    节点上限 100 截断置 truncated；含环返回环路径 cycles 并按 P0 降级列表；>20 折叠保留。"""
    from sqlmodel import select
    from mio_taskhub.models import Task

    rows = db.exec(select(Task.id, Task.title, Task.state, Task.stage,
                          Task.block_reason, Task.idea_id, Task.depends_on)).all()
    by_id = {r.id: r for r in rows}
    direct_ids = {r.id for r in rows if r.idea_id == idea.id}
    CAP = 100

    node_ids = set(direct_ids)
    upstream_ids: set = set()
    downstream_ids: set = set()
    truncated = False

    def _absorb(candidates: set, into: set) -> set:
        """按批加入候选（超出 CAP 截断），返回实际新入集节点。"""
        nonlocal truncated, node_ids
        fresh = {c for c in candidates if c in by_id and c not in node_ids}
        if not fresh:
            return set()
        room = CAP - len(node_ids)
        if len(fresh) > room:
            added = {c for c in sorted(fresh)[:max(0, room)]}
            node_ids |= added
            into |= added
            truncated = True
            return added
        node_ids |= fresh
        into |= fresh
        return fresh

    # 上游闭包：我依赖谁（depends_on 传递，层序 BFS）
    frontier = set(direct_ids)
    while frontier:
        nxt = set()
        for tid in frontier:
            nxt.update(d for d in (by_id[tid].depends_on or []))
        added = _absorb(nxt, upstream_ids)
        if truncated or not added:
            break
        frontier = added

    # 下游闭包：谁依赖我（反向边传递）
    if not truncated:
        rev: dict = {}
        for r in rows:
            for d in (r.depends_on or []):
                rev.setdefault(d, set()).add(r.id)
        frontier = set(direct_ids)
        while frontier:
            nxt = set()
            for tid in frontier:
                nxt.update(rev.get(tid, ()))
            added = _absorb(nxt, downstream_ids)
            if truncated or not added:
                break
            frontier = added

    edges = []
    for r in rows:
        if r.id not in node_ids:
            continue
        for d in (r.depends_on or []):
            if d in node_ids and d != r.id:
                edges.append({"from": d, "to": r.id})

    cycles = _find_cycles(node_ids, edges)
    has_cycle = bool(cycles)

    def _kind(r):
        if r.id in direct_ids:
            return "direct"
        return "upstream" if r.id in upstream_ids else "downstream"

    def _item(r):
        kind = _kind(r)
        return {"id": r.id, "title": r.title,
                "state": r.state.value if hasattr(r.state, "value") else str(r.state),
                "stage": r.stage.value if hasattr(r.stage, "value") else str(r.stage),
                "blocked": bool(r.block_reason) or str(getattr(r.state, "value", r.state)) == "blocked_failed",
                "downstream": kind == "downstream",  # P0 键保留（upstream/downstream 皆非 direct）
                "kind": kind}

    items = [_item(r) for r in rows if r.id in node_ids]
    graph = None
    warning = None
    if has_cycle:
        warning = "检测到依赖环，已降级为任务列表"
    else:
        graph = {"nodes": [{"id": r.id, "title": r.title, "kind": _kind(r)}
                           for r in rows if r.id in node_ids],
                 "edges": edges}
    return {"data": {
        "items": items,
        "graph": graph,
        "has_cycle": has_cycle,
        "cycles": cycles,
        "truncated": truncated,
        "upstream_total": len(upstream_ids),
        "downstream_total": len(downstream_ids),
        "warning": warning,
        "total": len(items),
        "folded": len(items) > 20,
    }}


def _find_cycles(node_ids, edges, limit: int = 5) -> list:
    """DFS 染色找依赖环，返回环路径列表（节点序列，回边不入列，至多 limit 个去重）。"""
    graph = {n: [] for n in node_ids}
    for e in edges:
        if e["from"] in graph and e["to"] in graph and e["from"] != e["to"]:
            graph[e["from"]].append(e["to"])
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in node_ids}
    cycles: list = []
    seen: set = set()
    stack: list = []

    def dfs(u):
        color[u] = GRAY
        stack.append(u)
        for v in graph.get(u, ()):
            if color[v] == GRAY:
                cyc = tuple(stack[stack.index(v):])  # 回边 v→u，环段从 v 起
                key = frozenset(cyc)
                if key not in seen:
                    seen.add(key)
                    cycles.append(list(cyc))
            elif color[v] == WHITE:
                dfs(v)
                if len(cycles) >= limit:
                    break
        stack.pop()
        color[u] = BLACK

    for n in node_ids:
        if color[n] == WHITE and len(cycles) < limit:
            dfs(n)
    return cycles


async def _build_risks(idea: Idea, db: Session) -> dict:
    return {"data": {"items": idea.risks if isinstance(idea.risks, list) else []}}


async def _build_approvals(idea: Idea, db: Session) -> dict:
    # P0 空壳；汇总关联任务 doc_statuses 状态由后续迭代填充
    return {"data": {"items": []}}


async def _build_retrospective(idea: Idea, db: Session) -> dict:
    """P3 FR-27：复盘区真实聚合——关联任务 run 成败 + 最近明细 + P2 结构化评审记录。

    数据源全本地（Run/Discussion），单区异常照走 SectionDegraded；空数据 status=ok。
    """
    from sqlmodel import select
    from mio_taskhub.models import Discussion, Run, Task

    task_rows = db.exec(select(Task.id, Task.title)
                        .where(Task.idea_id == idea.id)).all()
    task_titles = {r.id: r.title for r in task_rows}

    def _ts(dt):
        if dt is None:
            return datetime.min
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    summary = {"success": 0, "failure": 0, "pending": 0, "total": 0}
    items: list = []
    if task_titles:
        runs = db.exec(select(Run).where(
            Run.task_id.in_(set(task_titles)))).all()
        finished = []
        for r in runs:
            st = r.state.value if hasattr(r.state, "value") else str(r.state)
            if st == "finished":
                summary["success" if r.exit_code == 0 else "failure"] += 1
                finished.append(r)
            else:  # claimed / running / retrying
                summary["pending"] += 1
        summary["total"] = len(runs)
        finished.sort(key=lambda r: _ts(r.finished_at), reverse=True)
        for r in finished[:5]:
            items.append({
                "task_id": r.task_id,
                "task_title": task_titles.get(r.task_id, ""),
                "run_id": r.id,
                "exit_code": r.exit_code,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "result_excerpt": (r.result or "")[:200],
            })

    reviews = db.exec(select(Discussion).where(
        Discussion.idea_id == idea.id,
        Discussion.mode == "review",
        Discussion.status == "closed")).all()
    reviews = sorted(reviews, key=lambda d: _ts(d.ended_at), reverse=True)
    review_items = []
    for d in reviews[:5]:
        rv = d.review if isinstance(d.review, dict) else {}
        decisions = rv.get("decisions") if isinstance(rv.get("decisions"), list) else []
        actions = rv.get("action_items") if isinstance(rv.get("action_items"), list) else []
        review_items.append({
            "id": d.id,
            "topic": d.topic,
            "ended_at": d.ended_at.isoformat() if d.ended_at else None,
            "decision_count": len(decisions),
            "action_item_count": len(actions),
            "converted_count": sum(1 for a in actions
                                   if isinstance(a, dict) and a.get("task_id")),
        })

    return {"data": {"summary": summary, "items": items, "reviews": review_items}}


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
            # SectionDegraded 透传业务 reason（mio_timeout/mio_unavailable），其余用异常类名
            results[name] = {"status": "degraded",
                             "reason": getattr(e, "reason", None) or type(e).__name__}
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
        "high_risk": is_high_risk(idea, db=db),  # FR-8：供前端红队默认值等使用（FR-20 词表 env>DB>默认）
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
