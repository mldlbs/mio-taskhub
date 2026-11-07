"""危险端点的管理员凭据校验（P0 安全修复，2026-11-07）。

审计结论：`PUT /nightrun/config` + `POST /nightrun/spawn-now` 未鉴权，且命令以
`shell=True` 执行，任何本机进程都能让 hub 以自身权限跑任意命令。

本模块提供 `require_admin` 依赖，规则：

1. 若配置了 `MIO_TASKHUB_ADMIN_TOKEN` 或 hub 已启用 `--auth`（app.state.auth_token）：
   请求必须携带 `Authorization: Bearer <token>`（或 `X-TaskHub-Admin-Token`），否则 403。
2. 完全未配置 token（本机无鉴权模式）：
   仅允许环回来源 + 显式 `X-TaskHub-Admin: 1` 请求头，否则 403。
   这是「速度闸」而不是强鉴权——同一台机器上的任意进程仍可伪造该头。
   真正隔离请启用 `--auth` 或 `MIO_TASKHUB_ADMIN_TOKEN`。
"""
import os
import secrets

from fastapi import HTTPException, Request

ADMIN_HEADER = "x-taskhub-admin"
ADMIN_TOKEN_HEADER = "x-taskhub-admin-token"

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def admin_tokens(request: Request) -> list[str]:
    tokens = []
    env_token = os.environ.get("MIO_TASKHUB_ADMIN_TOKEN", "").strip()
    if env_token:
        tokens.append(env_token)
    app_token = getattr(request.app.state, "auth_token", "") or ""
    if app_token and app_token not in tokens:
        tokens.append(app_token)
    return tokens


def _supplied_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get(ADMIN_TOKEN_HEADER, "").strip()


def require_admin(request: Request) -> None:
    tokens = admin_tokens(request)
    supplied = _supplied_token(request)
    if tokens:
        if supplied and any(secrets.compare_digest(supplied, t) for t in tokens):
            return
        raise HTTPException(403, detail={
            "code": "admin.token_required",
            "message": "该端点需要管理员凭据：Authorization: Bearer <MIO_TASKHUB_ADMIN_TOKEN>",
        })
    client = request.client.host if request.client else ""
    if client in _LOOPBACK and request.headers.get(ADMIN_HEADER) == "1":
        return
    raise HTTPException(403, detail={
        "code": "admin.local_confirmation_required",
        "message": ("该端点需要管理员确认：未配置 MIO_TASKHUB_ADMIN_TOKEN 时，"
                    "仅接受环回来源且携带 X-TaskHub-Admin: 1 的请求。"),
    })
