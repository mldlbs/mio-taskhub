# tests/test_status.py
import pytest
from mio_taskhub.status import is_terminal, dependency_satisfied, task_deps, normalize_depends
from mio_taskhub.models import Task, TaskState, TaskStage


def _mk(state="queued", stage="ready"):
    t = Task(title="t")
    t.state = TaskState(state) if isinstance(state, str) else state
    if isinstance(stage, str):
        t.stage = stage
    return t


def test_is_terminal_via_state():
    assert is_terminal(_mk(state="completed"))
    assert is_terminal(_mk(state="cancelled"))
    assert is_terminal(_mk(state="failed"))
    assert is_terminal(_mk(state="blocked_failed"))
    assert not is_terminal(_mk(state="queued"))


def test_is_terminal_via_stage():
    assert is_terminal(_mk(state="queued", stage="done"))
    assert is_terminal(_mk(state="queued", stage="cancelled"))
    assert not is_terminal(_mk(state="queued", stage="ready"))


def test_dependency_satisfied():
    assert dependency_satisfied(_mk(state="completed"))
    assert dependency_satisfied(_mk(state="queued", stage="done"))
    assert not dependency_satisfied(_mk(state="queued", stage="ready"))
    assert not dependency_satisfied(_mk(state="cancelled"))
    assert not dependency_satisfied(_mk(state="failed"))


def test_task_deps_normalizes_str_and_none():
    t1 = _mk(); t1.depends_on = "abc"          # 旧单值字符串
    assert task_deps(t1) == ["abc"]
    t2 = _mk(); t2.depends_on = None
    assert task_deps(t2) == []
    t3 = _mk(); t3.depends_on = ["a", "b"]
    assert task_deps(t3) == ["a", "b"]


def test_normalize_depends_table():
    assert normalize_depends(None) == []
    assert normalize_depends("") == []
    assert normalize_depends("  ") == []
    assert normalize_depends("abc") == ["abc"]
    assert normalize_depends('["a","b"]') == ["a", "b"]
    assert normalize_depends("[") == []          # 非法 JSON
    assert normalize_depends("{x") == []
    assert normalize_depends(["a"]) == ["a"]
