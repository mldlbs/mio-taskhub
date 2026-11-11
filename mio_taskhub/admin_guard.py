"""危险端点的管理面凭据校验（P0 安全修复 2026-11-07；F-004 凭据拆分）。

两级凭据模型（F-004）：

- **API 访问凭据**：`--auth` / `MIO_TASKHUB_TOKEN`（落在 `app.state.auth_token`）。
  由 `main.py::make_auth_middleware` 校验，保护所有 `/api/`、`/metrics`、`/logs`。
  人、前端、MCP、执行任务的 agent 共用同一个值，只回答「能不能访问 API」。
- **管理面凭据**：`MIO_TASKHUB_ADMIN_TOKEN`（env，每次判定现读）。只给 Web 前端 /
  运维，**绝不下发给执行任务的 agent**。回答「是不是管理面操作者」，由本模块的
  `require_admin` / `admin_ok` 单独校验。

两者**不互相等价**：拿到 API 访问凭据不等于拿到管理员凭据。F-004 之前
`admin_tokens()` 把 `app.state.auth_token` 一并并入管理员凭据，等于任何持有全局
token 的 agent 都自动是管理员，`require_admin` 形同虚设。

降级态：只配 `--auth`、未配 `MIO_TASKHUB_ADMIN_TOKEN` 时，`admin_tokens()` 返回空，
`require_admin` 回落到「环回来源 + 显式 `X-TaskHub-Admin: 1`」这道**速度闸**。
同机任意进程都能伪造该头，这是**降级态，不是强隔离**；要强隔离必须两个都配
（`--auth` 挡住外部主体，`MIO_TASKHUB_ADMIN_TOKEN` 把管理面凭据与 agent 持有的
API 凭据切开）。

`require_admin` 判定规则：

1. 配置了 `MIO_TASKHUB_ADMIN_TOKEN`：请求必须携带 `X-TaskHub-Admin-Token: <token>`，
   否则 403 `admin.token_required`。`Authorization: Bearer <token>` 仅在**未启用
   `--auth`** 时可用作兼容——启用 `--auth` 后 Bearer 被 API 访问凭据占用，带
   Bearer 管理面 token 会先被 auth 中间件 401（2026-11-11 线上实测）。
   `X-TaskHub-Admin: 1` 在此模式下**不生效**。
2. 未配置管理面凭据（降级态）：仅允许环回来源 + `X-TaskHub-Admin: 1`，否则 403
   `admin.local_confirmation_required`。
"""
import os
import secrets

from fastapi import HTTPException, Request

ADMIN_HEADER = "x-taskhub-admin"
ADMIN_TOKEN_HEADER = "x-taskhub-admin-token"

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def admin_tokens(request: Request) -> list[str]:
    """返回管理面凭据列表：**只**取 `MIO_TASKHUB_ADMIN_TOKEN`。

    `request` 仅为保持既有调用签名；`app.state.auth_token`（`--auth`）是 API 访问
    凭据，F-004 之后**不再**并入管理员凭据。
    """
    env_token = os.environ.get("MIO_TASKHUB_ADMIN_TOKEN", "").strip()
    return [env_token] if env_token else []


def _supplied_tokens(request: Request) -> list[str]:
    """取出请求里所有可能承载管理面凭据的头，供 `compare_digest` 逐一比对。

    Bearer 与 `X-TaskHub-Admin-Token` 必须**并列**收集：启用 `--auth` 后，前端会在
    同一个请求里同时带 `Authorization: Bearer <API 访问凭据>` 和
    `X-TaskHub-Admin-Token: <管理面凭据>`。若只认第一个命中的头，管理面凭据会被
    API 凭据挤掉，强隔离部署（两个都配）下所有管理员操作都会 403。
    """
    tokens = []
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tokens.append(auth[7:].strip())
    hdr = request.headers.get(ADMIN_TOKEN_HEADER, "").strip()
    if hdr:
        tokens.append(hdr)
    return [t for t in tokens if t]


def require_admin(request: Request) -> None:
    tokens = admin_tokens(request)
    if tokens:
        if any(secrets.compare_digest(s, t)
               for s in _supplied_tokens(request) for t in tokens):
            return
        raise HTTPException(403, detail={
            "code": "admin.token_required",
            "message": ("该端点需要管理面凭据：X-TaskHub-Admin-Token: <MIO_TASKHUB_ADMIN_TOKEN>。"
                        "Authorization: Bearer <MIO_TASKHUB_ADMIN_TOKEN> 仅在未启用 --auth 时可用作兼容；"
                        "启用 --auth 后 Bearer 被 API 凭据占用，只能用 X-TaskHub-Admin-Token。"
                        "注意 --auth / MIO_TASKHUB_TOKEN 是 API 访问凭据，不是管理员凭据。"),
        })
    client = request.client.host if request.client else ""
    if client in _LOOPBACK and request.headers.get(ADMIN_HEADER) == "1":
        return
    raise HTTPException(403, detail={
        "code": "admin.local_confirmation_required",
        "message": ("该端点需要管理员确认：未配置 MIO_TASKHUB_ADMIN_TOKEN 时（降级态），"
                    "仅接受环回来源且携带 X-TaskHub-Admin: 1 的请求。"
                    "只配 --auth 并不能构成强隔离。"),
    })


def admin_ok(request: Request) -> bool:
    """同 `require_admin` 的判定，但不抛异常。

    用于**字段级**保护：整个端点不拦（普通编辑照常），只有 `ADMIN_TASK_FIELDS`
    与门控豁免置真的字段要求管理面凭据。见 `api/tasks.py::_guard_admin_fields`。
    """
    try:
        require_admin(request)
        return True
    except HTTPException:
        return False
