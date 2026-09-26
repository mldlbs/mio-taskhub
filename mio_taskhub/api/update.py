# -*- coding: utf-8 -*-
"""更新 REST API。"""
from fastapi import APIRouter, Query

from mio_taskhub.policy_guard import guard_action
from mio_taskhub.update.service import get_service

router = APIRouter(prefix="/update", tags=["update"])

# 测试注入点（None → 用全局单例）
_service_override = None


def _svc():
    return _service_override or get_service()


@router.get("/status")
def status():
    return _svc().status()


@router.post("/check")
def check():
    return _svc().check()


@router.post("/download")
def download():
    return _svc().download()


@router.post("/apply")
def apply(confirm: bool = Query(False)):
    """应用更新（替换二进制 + 重启 Hub）——最危险的调用点，先过 Mio 风险评估。"""
    policy = guard_action("taskhub:update-apply", confirm=confirm)
    result = _svc().apply()
    if isinstance(result, dict):
        return {**result, "policy": policy}
    return result


@router.post("/dismiss")
def dismiss():
    return _svc().dismiss()
