from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import select
from mio_taskhub.models import Task
from mio_taskhub.status import task_deps
from mio_taskhub.planner import detect_cycle


def parse_dt(value, name: str):
    if not isinstance(value, str):
        return value
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"invalid {name}: {value}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def parse_enum(enum_cls, value, default=None):
    if value is None and default is not None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        raise HTTPException(400, f"invalid value: {value}, expected one of {[e.value for e in enum_cls]}")


def _graph_with(task, db) -> dict:
    graph = {}
    for t in db.exec(select(Task)).all():
        graph[t.id] = task_deps(t)
    graph[task.id] = task_deps(task)
    return graph


def check_cycle(task, db):
    cyc = detect_cycle(_graph_with(task, db))
    if cyc:
        raise HTTPException(422, f"cyclic dependency: {' → '.join(cyc)}")


def validate_depends(task, db):
    for dep in task_deps(task):
        if dep == task.id:
            raise HTTPException(422, f"cannot depend on itself: {dep}")
        if db.get(Task, dep) is None:
            raise HTTPException(422, f"dependency not found: {dep}")
