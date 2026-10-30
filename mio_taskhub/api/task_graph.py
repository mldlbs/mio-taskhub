from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskStage, TaskState
from mio_taskhub.dependency import task_deps
from mio_taskhub.planner import detect_cycle

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/graph")
def get_full_graph(db: Session = Depends(get_session)):
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


@router.get("/{task_id}/workstream")
def get_workstream(task_id: str, db: Session = Depends(get_session)):
    """按 part-of:<id> 聚合平级任务，返回依赖层、阻塞与统计。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    labels = [str(x) for x in (task.labels or [])]
    owner = task_id
    for label in labels:
        if label.startswith("part-of:"):
            owner = label.split(":", 1)[1].strip() or task_id
            break

    all_tasks = db.exec(select(Task)).all()
    by_id = {x.id: x for x in all_tasks}
    members = [
        x for x in all_tasks
        if f"part-of:{owner}" in [str(v) for v in (x.labels or [])]
    ]
    cancelled_ids = {
        x.id for x in members
        if x.state == TaskState.CANCELLED or x.stage == TaskStage.CANCELLED
    }
    members = [x for x in members if x.id not in cancelled_ids]
    members.sort(key=lambda x: x.created_at or 0)

    def closed(x):
        return (x.state in (TaskState.COMPLETED, TaskState.CANCELLED)
                or x.stage in (TaskStage.DONE, TaskStage.CANCELLED))

    def done(x):
        return x.state == TaskState.COMPLETED or x.stage == TaskStage.DONE

    ids = {x.id for x in members}
    nodes = [{
        "id": x.id, "title": x.title,
        "state": x.state.value, "stage": x.stage.value,
        "priority": x.priority, "depends_on": task_deps(x),
        "labels": list(x.labels or []), "est_duration_min": x.est_duration_min,
        "target_agent_type": x.target_agent_type,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    } for x in members]

    edges, external, missing = [], [], []
    for x in members:
        for dep in task_deps(x):
            if dep in ids:
                edges.append({"from": dep, "to": x.id})
            elif dep in by_id:
                dep_task = by_id[dep]
                if (dep_task.state == TaskState.CANCELLED
                        or dep_task.stage == TaskStage.CANCELLED):
                    continue
                external.append({"from": dep, "to": x.id,
                                 "from_title": dep_task.title,
                                 "closed": closed(dep_task)})
            else:
                missing.append({"from": dep, "to": x.id})

    succ = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for e in edges:
        succ[e["from"]].append(e["to"])
        indeg[e["to"]] += 1

    layers, depth = [], {}
    frontier = sorted([i for i in ids if indeg[i] == 0])
    d = 0
    while frontier:
        layers.append(frontier)
        nxt = []
        for i in frontier:
            depth[i] = d
            for ch in succ[i]:
                indeg[ch] -= 1
                if indeg[ch] == 0:
                    nxt.append(ch)
        frontier = sorted(nxt)
        d += 1
    leftover = sorted(ids - set(depth))
    if leftover:
        layers.append(leftover)

    # longest chain by node count
    dist, prev = {}, {}
    for layer in layers:
        for i in layer:
            deps = [e["from"] for e in edges if e["to"] == i]
            if not deps:
                dist[i], prev[i] = 1, None
            else:
                best = max(deps, key=lambda x: dist.get(x, 1))
                dist[i], prev[i] = dist.get(best, 1) + 1, best
    cp_ids = []
    if dist:
        end = max(dist, key=dist.get)
        while end is not None:
            cp_ids.append(end)
            end = prev.get(end)
        cp_ids.reverse()

    blocked = []
    for x in members:
        blocked_deps = []
        for dep in task_deps(x):
            if dep in ids and not closed(by_id[dep]):
                blocked_deps.append(dep)
            elif dep in by_id and dep not in ids and not closed(by_id[dep]):
                blocked_deps.append(dep)
            elif dep not in by_id:
                blocked_deps.append(dep)
        if blocked_deps and not closed(x):
            blocked.append({"id": x.id, "blocked_by": blocked_deps})

    graph = {i: [d for d in task_deps(by_id[i]) if d in ids] for i in ids}
    cycle = detect_cycle(graph)

    return {
        "owner": owner,
        "owner_title": by_id[owner].title if owner in by_id else None,
        "is_member": owner != task_id,
        "nodes": nodes,
        "edges": edges,
        "layers": layers,
        "external": external,
        "missing": missing,
        "blocked": blocked,
        "cycle": bool(cycle),
        "cycle_path": cycle,
        "stats": {
            "total": len(members),
            "cancelled": len(cancelled_ids),
            "done": sum(1 for x in members if done(x)),
            "blocked": len(blocked),
            "estimate_min": sum(int(x.est_duration_min or 0) for x in members),
            "critical_path": len(cp_ids),
            "critical_path_ids": cp_ids,
        },
    }

@router.get("/{task_id}/graph")
def get_task_graph(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    all_tasks = db.exec(select(Task)).all()
    by_id = {x.id: x for x in all_tasks}
    succ = {x.id: [] for x in all_tasks}
    for x in all_tasks:
        for d in task_deps(x):
            if d in by_id:
                succ[d].append(x.id)
    ancestors = set()
    stack = list(task_deps(t))
    while stack:
        cur = stack.pop()
        if cur in ancestors or cur not in by_id:
            continue
        ancestors.add(cur)
        stack.extend(task_deps(by_id[cur]))
    descendants = set()
    stack = list(succ.get(t.id, []))
    while stack:
        cur = stack.pop()
        if cur in descendants:
            continue
        descendants.add(cur)
        stack.extend(succ.get(cur, []))
    missing = [d for d in task_deps(t) if d not in by_id]
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
