from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task
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
