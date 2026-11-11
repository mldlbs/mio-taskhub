# -*- coding: utf-8 -*-
"""MCP 工具风险门控（task 7d7ff97a / 评估 round3 P1-C；F-004 第 3 步收敛）。

问题：mcp_server 的写/破坏性工具此前无执行层策略检查——agent 可自主调用
直接改状态，只靠工具描述"自觉"。不依赖 agent 自觉的安全必须在**统一执行层**强制。

本模块提供：
- RISK：工具路径 → 风险级（read / write / destructive）显式登记（覆盖全部工具面）；
- classify(method, path)：把一次调用归类到风险级；
- check(method, path)：策略判定，返回 (allowed, reason)。
  默认拒绝 destructive；MIO_MCP_ALLOW_DESTRUCTIVE=1 只放宽**MCP 客户端这一层**。

与**服务端** destructive 门控（destructive_guard.py）的关系：
- destructive 模式清单从 `destructive_guard.DESTRUCTIVE_ENDPOINTS` **派生**
  （单一事实源），两端不再漂移；
- 服务端对所有 destructive 端点要求**管理面凭据**（REST 直连同判 403），
  且**不读** `MIO_MCP_ALLOW_DESTRUCTIVE`——本模块的放行开关只影响 MCP 客户端
  这一层（省一次网络往返 / 非本仓库 hub 等场景），不构成服务端绕过。

门控点在 mcp_server._request（所有工具调用统一收口）。
"""
import os
import re
from typing import Optional

from mio_taskhub.destructive_guard import DESTRUCTIVE_ENDPOINTS

READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"

# 破坏性：不可逆 / 删除 / 可绕过安全门控的高影响操作。
# 模式表**从服务端清单派生**（`^METHOD\s+<path>` 形式，供 "METHOD path" 整串匹配），
# 不再在本模块手工维护第二份清单（2026-11-11 F-004 第 3 步：消除两端漂移）。
# 判定标准（从严界定，避免误伤正常 agent 工作流——本任务初版曾把 update_task/
# move_to_stage 也列为 destructive，会阻断合法工作流，已收窄）：
#   DELETE /tasks/{id}                    取消任务（删除/终态语义）
#   POST   /tasks/{id}/doc/{kind}/status  推进文档生命周期（含 force 可绕过质量门/棘轮）
#   DELETE /tasks/{id}/subtasks/{sid}     删除子任务（不可恢复；本模块原清单漏登，补齐）
#   POST   /ideas/gate/force-generate     绕过想法闸门（原清单漏登，补齐）
# 说明：update_task（改字段）、move_to_stage（推进阶段，本身已受 stage 门控约束）
# 属正常写操作，归 write——它们不绕过门控，也不是删除。
_DESTRUCTIVE_PATTERNS = [
    (r"^%s\s+%s" % (method, path.lstrip("^")), name)
    for method, path, name, _why in DESTRUCTIVE_ENDPOINTS
]

# 写（非破坏）：创建/更新但不删除、不绕过门控。
_WRITE_METHODS = {"POST", "PUT", "PATCH"}


def _norm(method: str, path: str) -> str:
    p = path.split("?")[0]
    return f"{method.upper()} {p}"


def classify(method: str, path: str) -> str:
    """把 (method, path) 归类为 read/write/destructive。"""
    key = _norm(method, path)
    for pat, _name in _DESTRUCTIVE_PATTERNS:
        if re.match(pat, key):
            return DESTRUCTIVE
    if method.upper() in _WRITE_METHODS:
        return WRITE
    return READ


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def check(method: str, path: str) -> tuple[bool, str]:
    """策略判定。返回 (allowed, reason)。

    默认：read/write 放行；destructive 拒绝（除非显式放行）。
    - MIO_MCP_ALLOW_DESTRUCTIVE=1：放行**MCP 客户端这一层**。注意：服务端
      destructive 门控（destructive_guard）要求管理面凭据且不读该开关——
      即使本层放行，REST/服务端仍会 403，改由人/前端（管理面凭据）执行。
    """
    level = classify(method, path)
    if level == DESTRUCTIVE:
        if _truthy("MIO_MCP_ALLOW_DESTRUCTIVE"):
            return True, "allowed (MIO_MCP_ALLOW_DESTRUCTIVE=1, MCP client layer only)"
        return False, (
            f"blocked: '{_norm(method, path)}' is a destructive operation; "
            f"agent auto-execution is denied by default. "
            f"该动作需管理面凭据（服务端对 destructive 端点强制，REST 直连同判 403；"
            f"MIO_MCP_ALLOW_DESTRUCTIVE=1 只放宽本 MCP 客户端层，服务端不认）——"
            f"由人/前端执行。"
        )
    return True, "ok"


def risk_table() -> dict:
    """供交付说明/前端展示：已知破坏性模式清单。"""
    return {"destructive_patterns": [p for p, _ in _DESTRUCTIVE_PATTERNS]}
