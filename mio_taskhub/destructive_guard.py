# -*- coding: utf-8 -*-
"""服务端 destructive 收口（F-004 第 3 步：身份体系——destructive 门控下沉）。

背景：`mcp_risk.py` 的 destructive 门控跑在 **MCP 进程内**（`mcp_server._request`
统一收口），只能拦 MCP 工具调用。持有 API 访问凭据的主体直连 REST（curl / httpx
均可）完全不经过该门——destructive 端点此前对 REST 是裸的（仅
`POST /tasks/{id}/doc/{kind}/status` 在第 1 步加了端点级 `require_admin`）。

本模块把「哪些 (method, path) 算破坏性」提升为**服务端单一事实源**：

- `DESTRUCTIVE_ENDPOINTS`：显式清单（method + 路径正则 + 名称 + 判定依据）；
- `match_destructive(method, path)`：判定，命中返回名称，未命中返回 None；
- `is_destructive(method, path)`：布尔便捷封装；
- `make_destructive_guard_middleware()`：FastAPI 中间件，命中清单 → 复用
  `admin_guard.require_admin` 校验**管理面凭据**，无凭据 403（REST 直连与
  MCP 工具调用同一把尺）。

判定依据（从严，与 mcp_risk 对齐并补齐其缺口）：删除/终态语义、绕过质量门/
闸门的验收动作、不可恢复删除。**不**把普通写操作（claim / heartbeat /
submit_result / write_document / add_discussion / PATCH 普通字段 / advance_stage
等 agent 必需路径）划进来——它们不绕过任何门控，也不是删除。

与 `mcp_risk.py` 的一致性 / 差异：
- mcp_risk 原清单只登记 cancel_task 与 set_doc_status 两个模式；MCP 工具面
  `destructive=True` 的还有 delete_subtask 与 force_generate——本清单补齐这两个，
  mcp_risk 的模式表改为从本模块派生（`^METHOD\\s+<path>` 形式），两端不再漂移。
- 放行开关语义收敛：`MIO_MCP_ALLOW_DESTRUCTIVE` 是 **MCP 客户端侧**开关，只在
  `mcp_risk.check` 生效；本中间件**不读任何放行环境变量**——服务端判定不随它
  变化，不存在「一开全放」的服务端宽口子。服务端唯一的放行路径是管理面凭据：
  配了 `MIO_TASKHUB_ADMIN_TOKEN` 必须带 `X-TaskHub-Admin-Token`；未配置时回落
  「环回来源 + 显式 `X-TaskHub-Admin: 1`」降级态（见 `admin_guard.py`，不是强隔离）。

清单是**代码常量**（`DESTRUCTIVE_ENDPOINTS`），不是配置项：新增破坏性端点必须
改代码 + 过评审 + 带回归，不能靠运行时改环境悄悄扩大破坏面。
"""
import logging
import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

# (method, 路径正则（相对 /api/v1 前缀，也容忍不带前缀）, 名称, 判定依据)
DESTRUCTIVE_ENDPOINTS = [
    ("DELETE", r"^/tasks/[^/]+$", "cancel_task",
     "取消任务：任务终态写入，agent 自主取消会绕过人工排期与验收"),
    ("POST", r"^/tasks/[^/]+/doc/[^/]+/status$", "set_doc_status",
     "推进文档生命周期（含 review/approved/released 等验收态）：绕过质量门/棘轮，"
     "属验收动作，须由人/前端完成"),
    ("DELETE", r"^/tasks/[^/]+/subtasks/[^/]+$", "delete_subtask",
     "删除子任务：不可恢复"),
    ("POST", r"^/ideas/gate/force-generate$", "force_generate",
     "强制触发灵感生成：绕过想法生产闸门（留痕审计）"),
    # ── 2026-11-11 B16-6 残余 #2：operator 面删除端点并入 ──
    ("DELETE", r"^/tasks/templates/[^/]+$", "delete_template",
     "删除任务模板：硬删且连版本历史一起删，不可恢复"),
    ("DELETE", r"^/scheduled-jobs/[^/]+$", "delete_scheduled_job",
     "删除定时任务：硬删不可恢复，且定时任务可驱动 nightrun 等批量动作"),
    ("DELETE", r"^/alert-rules/[^/]+$", "delete_alert_rule",
     "删除告警规则：硬删不可恢复（DELETE FROM），静默削弱运维告警面"),
    ("POST", r"^/update/apply$", "update_apply",
     "应用更新：替换二进制并重启 Hub，等价于对常驻服务执行维护动作，"
     "须由人/前端确认（policy_guard 的 confirm 只是客户端确认，非身份校验）"),
]

_API_PREFIX = re.compile(r"^/api/v1")
_COMPILED = tuple((m, re.compile(p), name) for m, p, name, _why in DESTRUCTIVE_ENDPOINTS)


def _norm_path(path: str) -> str:
    """剥掉 query 与可选的 /api/v1 前缀，统一成清单口径。"""
    p = _API_PREFIX.sub("", (path or "").split("?")[0]) or "/"
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    return p


def match_destructive(method: str, path: str) -> str | None:
    """命中破坏性端点清单返回端点名称，否则返回 None。"""
    m = (method or "").upper()
    p = _norm_path(path)
    for method_, pat, name in _COMPILED:
        if m == method_ and pat.match(p):
            return name
    return None


def is_destructive(method: str, path: str) -> bool:
    return match_destructive(method, path) is not None


def _audit_block(method: str, path: str, code: str) -> None:
    """记录被服务端门控拒绝的破坏性请求（审计留痕）。best-effort，不影响拒绝返回。

    不走 emit_event（其 after_commit 广播在部分上下文会踩事件循环，同
    mcp_server._audit_risk_block 的处理），直接写 Event 行。
    """
    try:
        logging.getLogger("mio_taskhub.destructive").warning(
            "destructive gate blocked: %s %s — %s", method, path, code)
    except Exception:  # noqa: BLE001
        pass
    try:
        import json as _json
        from sqlmodel import Session
        from mio_taskhub.db import engine
        from mio_taskhub.models import Event
        with Session(engine) as db:
            db.add(Event(type="destructive_blocked", entity="api",
                         entity_id=f"{method} {path}",
                         payload=_json.dumps({"method": method, "path": path, "code": code},
                                             ensure_ascii=False)))
            db.commit()
    except Exception:  # noqa: BLE001 — 审计失败不得阻断拒绝返回
        pass


def make_destructive_guard_middleware():
    """destructive 端点服务端门控中间件。

    注册位置要求：在 `make_auth_middleware` **之前** add（内侧）——后注册的
    auth 中间件在外层先跑，无 API 凭据的请求先 401，不会把 401 变成 403。
    """
    async def destructive_guard(request: Request, call_next):
        name = match_destructive(request.method, request.url.path)
        if name:
            from mio_taskhub import admin_guard  # 延迟导入，避免装配期环
            try:
                admin_guard.require_admin(request)
            except HTTPException as e:
                code = e.detail.get("code") if isinstance(e.detail, dict) else name
                _audit_block(request.method, request.url.path, code or name)
                return JSONResponse({"detail": e.detail}, status_code=403)
        return await call_next(request)
    return destructive_guard
