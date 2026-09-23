# -*- coding: utf-8 -*-
"""更新 REST API。"""
from fastapi import APIRouter

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
def apply():
    return _svc().apply()


@router.post("/dismiss")
def dismiss():
    return _svc().dismiss()
