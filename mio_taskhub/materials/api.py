# -*- coding: utf-8 -*-
"""素材库 API（P3 只读来源健康；其余接口在 P5）。"""
from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.materials.adapters import source_health

router = APIRouter(prefix="/materials", tags=["materials"])


@router.get("/sources")
def list_sources():
    with Session(engine) as s:
        return {"items": source_health(s)}
