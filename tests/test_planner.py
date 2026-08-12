# tests/test_planner.py
import pytest
from datetime import time
from mio_taskhub.planner import PlanItem, generate_night_plan

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
