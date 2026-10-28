# -*- coding: utf-8 -*-
"""只创建素材库 7 张表，不触碰现有业务表、不跑历史迁移。

生产回填脚本必须用这个入口，而不是 db.init_db()——后者会执行
migrations.py，对 task/run 等既有表做幂等 ALTER/索引操作。
"""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel

from mio_taskhub.db import engine as default_engine
from mio_taskhub.materials.models import (
    Material, MaterialExtract, MaterialLink, MaterialRaw, MaterialSource,
    MaterialSourceAudit, MaterialWitness,
)
from mio_taskhub.materials.seed import seed_material_sources

TABLES = [
    MaterialSource.__table__,
    MaterialRaw.__table__,
    Material.__table__,
    MaterialExtract.__table__,
    MaterialWitness.__table__,
    MaterialLink.__table__,
    MaterialSourceAudit.__table__,
]


def init_materials_db(target_engine=None) -> None:
    """幂等建表 + 种子；只写 material_* 表。"""
    eng = target_engine or default_engine
    delays = (0.0, 0.2, 0.5, 1.0)
    last_exc: Optional[Exception] = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            SQLModel.metadata.create_all(eng, tables=TABLES)
            with Session(eng) as session:
                seed_material_sources(session)
            return
        except OperationalError as exc:
            text = str(exc).lower()
            if "locked" not in text and "busy" not in text:
                raise
            last_exc = exc
    if last_exc is not None:
        raise last_exc
