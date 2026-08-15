# mio_taskhub/status.py
"""统一的任务状态判据（终态 / 依赖满足），供 DAG、Board、Scheduler、Planner、前端联动使用。"""
from __future__ import annotations
import json
import logging
from typing import Any

logger = logging.getLogger("mio_taskhub.status")

# state 维度上的终态集合（stage=done/cancelled 也视为终态，见 is_terminal）
TERMINAL_STATES = {"completed", "cancelled", "failed", "blocked_failed"}


def _stage_str(v) -> str:
    if v is None:
        return ""
    return v.value if not isinstance(v, str) else v


def is_terminal(task) -> bool:
    """任务不可再被调度/放行（终态）。state 或 stage 任一为终态即 True。"""
    s = task.state.value if hasattr(task.state, "value") else task.state
    st = _stage_str(task.stage)
    return s in TERMINAL_STATES or st in ("done", "cancelled")


def dependency_satisfied(task) -> bool:
    """作为前置依赖时是否算满足：state=completed 或 stage=done。"""
    s = task.state.value if hasattr(task.state, "value") else task.state
    return s == "completed" or _stage_str(task.stage) == "done"


def normalize_depends(value: Any) -> list:
    """把 depends_on 的任意旧值/新值归一化为列表。

    - None / 空白字符串 → []
    - 非 JSON 单值字符串（旧库 VARCHAR 列）→ [value]
    - 合法 JSON 数组字符串 → 解析为列表
    - 非法 JSON → [] + warning（不阻塞）
    - 已是 list → 原样（清掉空白项）
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [x for x in value if x]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("[") or s.startswith("{"):
            try:
                arr = json.loads(s)
                return [x for x in arr if x] if isinstance(arr, list) else []
            except ValueError:
                logger.warning("depends_on 非法 JSON，已置空: %r", value)
                return []
        return [s]
    return []


def task_deps(task) -> list:
    """读取任务依赖列表（兼容旧字符串/None/列表）。"""
    return normalize_depends(getattr(task, "depends_on", None))
