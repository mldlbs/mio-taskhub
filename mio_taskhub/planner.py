# mio_taskhub/planner.py
from __future__ import annotations
from datetime import time
from dataclasses import dataclass, field
from typing import List, Dict
from collections import deque
from mio_taskhub.status import normalize_depends

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
    max_parallel: int = 1

def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute

def _minutes_to_str(mins: int) -> str:
    h = (mins // 60) % 24
    m = mins % 60
    return f"{h:02d}:{m:02d}"


def _deps_of(t: dict) -> list:
    return normalize_depends(t.get("depends_on"))

def _topological_sort(tasks: List[dict]) -> List[dict]:
    task_map = {t["id"]: t for t in tasks}
    in_degree = {t["id"]: 0 for t in tasks}
    children: Dict[str, List[str]] = {t["id"]: [] for t in tasks}
    for t in tasks:
        for d in _deps_of(t):
            if d in task_map:          # 缺失依赖忽略
                in_degree[t["id"]] += 1
                children[d].append(t["id"])
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


def detect_cycle(deps: Dict[str, List[str]]) -> List[str]:
    """返回一条环路径 [a, b, a]；无环返回 []。deps: {task_id: [dep_ids]}。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in deps}
    stack: List[str] = []

    def dfs(u: str) -> List[str]:
        color[u] = GRAY
        stack.append(u)
        for v in deps.get(u, []):
            if v not in color:
                continue
            if color[v] == GRAY:
                return stack[stack.index(v):] + [v]
            if color[v] == WHITE:
                cyc = dfs(v)
                if cyc:
                    return cyc
        stack.pop()
        color[u] = BLACK
        return []

    for node in deps:
        if color[node] == WHITE:
            cyc = dfs(node)
            if cyc:
                return cyc
    return []

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

    # 资源感知排期：同 agent 串行，不同 agent 可并行；依赖跨组需等待
    resource_cursor: Dict[str, int] = {}   # agent -> 下一个可用时刻
    end_time_of: Dict[str, int] = {}       # task_id -> 结束时刻（供依赖查询）

    for t in sorted_tasks:
        agent = (t.get("target_agent_type") or "").strip() or "__default__"
        dur = t.get("est_duration_min", 30)
        est = resource_cursor.get(agent, start_mins)
        for d in _deps_of(t):
            if d in end_time_of:
                est = max(est, end_time_of[d] + buffer_min)
        end_cursor = est + dur
        if end_cursor - start_mins > window_min:
            plan.has_overflow = True
        plan.items.append(PlanItem(
            task_id=t["id"],
            title=t.get("title", ""),
            est_duration_min=dur,
            scheduled_start=_minutes_to_str(est),
            scheduled_end=_minutes_to_str(end_cursor),
        ))
        resource_cursor[agent] = end_cursor + buffer_min
        end_time_of[t["id"]] = end_cursor

    # 峰值并行度：扫描所有起止事件
    events = []
    for i in plan.items:
        s = _time_to_minutes(_parse_hm_str(i.scheduled_start))
        e = _time_to_minutes(_parse_hm_str(i.scheduled_end))
        if e <= s:
            e += 24 * 60
        events.append((s, 1))
        events.append((e, -1))
    events.sort(key=lambda x: (x[0], x[1]))
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    plan.max_parallel = max(peak, 1)
    return plan


def _parse_hm_str(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))
