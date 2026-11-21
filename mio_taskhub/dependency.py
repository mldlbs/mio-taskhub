# mio_taskhub/dependency.py
"""依赖检查 + 依赖列表归一化。"""
from __future__ import annotations
import json
import logging
from typing import Any

from mio_taskhub.workflow.state_machine import _stage_str

logger = logging.getLogger("mio_taskhub.dependency")


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


def dependencies_satisfied(db, task) -> bool:
    """领取准入硬门禁：所有依赖必须存在且已完成；异常/缺失一律拒绝。

    与 MIO_CLAIM_GATE 无关，避免通过关闭可选 readiness 门禁绕过 DAG 依赖。
    """
    try:
        deps = task_deps(task)
        for dep_id in deps:
            if not isinstance(dep_id, str) or not dep_id.strip():
                return False
            prerequisite = db.get(type(task), dep_id)
            if prerequisite is None or not dependency_satisfied(prerequisite):
                return False
        return True
    except Exception:
        logger.exception("dependency admission check failed: task=%s", getattr(task, "id", None))
        return False
