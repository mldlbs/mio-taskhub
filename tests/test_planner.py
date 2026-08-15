# tests/test_planner.py
import pytest
from datetime import time
from mio_taskhub.planner import PlanItem, detect_cycle, generate_night_plan

def test_basic_serial_plan():
    tasks = [
        {"id": "a", "title": "A", "est_duration_min": 60, "priority": 1, "depends_on": None},
        {"id": "b", "title": "B", "est_duration_min": 30, "priority": 0, "depends_on": None},
    ]
    plan = generate_night_plan(tasks, window_start=time(22, 0), window_end=time(7, 0))
    assert plan.items[0].task_id == "a"
    assert plan.items[1].task_id == "b"
    assert plan.items[0].scheduled_start == "22:00"
    assert plan.items[1].scheduled_start == "23:05"

def test_dependency_ordering():
    tasks = [
        {"id": "child", "title": "child", "est_duration_min": 30, "priority": 1, "depends_on": "parent"},
        {"id": "parent", "title": "parent", "est_duration_min": 60, "priority": 0, "depends_on": None},
    ]
    plan = generate_night_plan(tasks, window_start=time(22, 0), window_end=time(7, 0))
    ids = [i.task_id for i in plan.items]
    assert ids.index("parent") < ids.index("child")

def test_overflow_detection():
    tasks = [
        {"id": "big", "title": "big", "est_duration_min": 600, "priority": 0, "depends_on": None},
    ]
    plan = generate_night_plan(tasks, window_start=time(22, 0), window_end=time(7, 0))
    assert plan.has_overflow

def test_empty_task_list():
    plan = generate_night_plan([], window_start=time(22, 0), window_end=time(7, 0))
    assert plan.items == []
    assert not plan.has_overflow

def test_detect_cycle_none():
    deps = {"a": [], "b": ["a"], "c": ["a", "b"]}
    assert detect_cycle(deps) == []

def test_detect_cycle_simple():
    deps = {"a": ["b"], "b": ["a"]}
    path = detect_cycle(deps)
    assert path, "expected a cycle"
    assert path[0] == path[-1]

def test_detect_cycle_chain():
    # d 无依赖，a→c→b→a 形成环；环外节点 d 不应被误报
    deps = {"d": [], "b": ["a"], "c": ["b"], "a": ["c"]}
    path = detect_cycle(deps)
    assert path, "expected a cycle"
    assert path[0] == path[-1]

def test_multi_dep_ordering():
    # parent done → children；child2 依赖 parent + child1
    tasks = [
        {"id": "p", "title": "p", "est_duration_min": 30, "priority": 0, "depends_on": []},
        {"id": "c1", "title": "c1", "est_duration_min": 30, "priority": 0, "depends_on": ["p"]},
        {"id": "c2", "title": "c2", "est_duration_min": 30, "priority": 0, "depends_on": ["p", "c1"]},
    ]
    plan = generate_night_plan(tasks, window_start=time(22, 0), window_end=time(7, 0))
    ids = [i.task_id for i in plan.items]
    assert ids.index("p") < ids.index("c1") < ids.index("c2")
