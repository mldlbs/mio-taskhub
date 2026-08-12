# mio_taskhub/planner.py
from __future__ import annotations
from datetime import time
from dataclasses import dataclass, field
from typing import List, Dict
from collections import deque

@dataclass
class PlanItem:
    task_id: str
    title: str
    est_duration_min: int
    scheduled_start: str
    scheduled_end: str

@dataclass
class NightPlan:
    window_start: str
    window_end: str
    items: List[PlanItem] = field(default_factory=list)
    has_overflow: bool = False

def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute

def _minutes_to_str(mins: int) -> str:
    h = (mins // 60) % 24
    m = mins % 60
    return f"{h:02d}:{m:02d}"


def _topological_sort(tasks: List[dict]) -> List[dict]:
    task_map = {t["id"]: t for t in tasks}
    in_degree = {t["id"]: 0 for t in tasks}
    children: Dict[str, List[str]] = {t["id"]: [] for t in tasks}
    for t in tasks:
        if t.get("depends_on") and t["depends_on"] in task_map:
            in_degree[t["id"]] += 1
            children[t["depends_on"]].append(t["id"])
    queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
    queue = deque(sorted(queue, key=lambda tid: -task_map[tid].get("priority", 0)))
    result = []
    while queue:
        tid = queue.popleft()
        result.append(task_map[tid])
        for child_id in sorted(children[tid], key=lambda c: -task_map[c].get("priority", 0)):
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                queue.append(child_id)
    return result

def generate_night_plan(
    tasks: List[dict],
    window_start: time = time(22, 0),
    window_end: time = time(7, 0),
    buffer_min: int = 5,
) -> NightPlan:
    sorted_tasks = _topological_sort(tasks)
    plan = NightPlan(
        window_start=_minutes_to_str(_time_to_minutes(window_start)),
        window_end=_minutes_to_str(_time_to_minutes(window_end)),
    )
    start_mins = _time_to_minutes(window_start)
    end_mins = _time_to_minutes(window_end)
    if end_mins <= start_mins:
        end_mins += 24 * 60
    window_min = end_mins - start_mins

    cursor = start_mins
    for t in sorted_tasks:
        dur = t.get("est_duration_min", 30)
        end_cursor = cursor + dur
        if end_cursor - start_mins > window_min:
            plan.has_overflow = True
        plan.items.append(PlanItem(
            task_id=t["id"],
            title=t.get("title", ""),
            est_duration_min=dur,
            scheduled_start=_minutes_to_str(cursor),
            scheduled_end=_minutes_to_str(end_cursor),
        ))
        cursor = end_cursor + buffer_min
    return plan
