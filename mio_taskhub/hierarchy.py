"""任务层级（P2a）：主子关系与领取过滤。

边界（严格限定 P2a）：
- 只提供 parent_id / role 两个字段的语义与校验；
- 不实现拆分校验、上下文包、集成门禁（P2b–P2d）；
- 所有领取入口共用 `claimable_task_clause()`，避免某个入口绕过过滤。

role 取值：
- `leaf`：普通任务（存量任务默认值，行为不变）
- `child`：子任务，可被普通 worker 领取
- `main`：主任务/容器，不可被普通 worker 领取

兼容性：role 为 NULL 的历史行按 `leaf` 处理，不因 NULL 被误排除。
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import or_

from mio_taskhub.models import Task

ROLE_MAIN = "main"
ROLE_CHILD = "child"
ROLE_LEAF = "leaf"
ROLES = (ROLE_MAIN, ROLE_CHILD, ROLE_LEAF)

STAGE_OFF = "off"
STAGE_HIERARCHY = "hierarchy"
STAGE_VALIDATE = "validate"
STAGE_CONTEXT = "context"
STAGE_INTEGRATE = "integrate"
_STAGE_ORDER = {
    STAGE_OFF: 0,
    STAGE_HIERARCHY: 1,
    STAGE_VALIDATE: 2,
    STAGE_CONTEXT: 3,
    STAGE_INTEGRATE: 4,
}


def stage_at_least(stage: str) -> bool:
    """MIO_DECOMPOSE_STAGE 是否已达到（含越过）指定阶段。"""
    return _STAGE_ORDER[current_stage()] >= _STAGE_ORDER.get(stage, 99)


def current_stage() -> str:
    """读取 MIO_DECOMPOSE_STAGE；未知值按 off 处理（fail-closed）。"""
    raw = (os.environ.get("MIO_DECOMPOSE_STAGE") or STAGE_OFF).strip().lower()
    return raw if raw in _STAGE_ORDER else STAGE_OFF


def hierarchy_enabled() -> bool:
    """P2a 层级能力是否启用。默认 off，验证后逐级打开。"""
    return stage_at_least(STAGE_HIERARCHY)


def validate_enabled() -> bool:
    """P2b 拆分校验是否启用；P2b 起子任务只能由 decompose/apply 创建。"""
    return stage_at_least(STAGE_VALIDATE)


def normalize_role(task: Task) -> str:
    """读取任务 role；NULL/未知值按 leaf 处理（历史数据兼容）。"""
    role = getattr(task, "role", None)
    return role if role in ROLES else ROLE_LEAF


def is_main(task: Task) -> bool:
    return normalize_role(task) == ROLE_MAIN


def claimable_task_clause():
    """领取候选的共享谓词：state/stage 之外，额外排除主任务。

    NULL role 按 leaf 处理——`role IS NULL OR role != 'main'`，
    避免 SQL 三值逻辑把历史行误判为不可领取。
    """
    return or_(Task.role.is_(None), Task.role != ROLE_MAIN)


def validate_child_parent(db, parent_id: str) -> Optional[Task]:
    """校验父任务并返回；不合法时抛 ValueError（由 API 转 HTTP 状态码）。"""
    parent = db.get(Task, parent_id)
    if parent is None:
        raise ValueError("parent_not_found")
    if parent.parent_id:
        raise ValueError("depth_exceeded")
    if normalize_role(parent) == ROLE_CHILD:
        raise ValueError("parent_is_child")
    if parent.state in ("completed", "cancelled"):
        raise ValueError("parent_terminal")
    return parent
