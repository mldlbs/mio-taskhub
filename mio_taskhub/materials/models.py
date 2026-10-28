# -*- coding: utf-8 -*-
"""素材库 SQLModel：7 张表（spec v1.1 / data-model v1.1）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from mio_taskhub.utils import _now


class MaterialSource(SQLModel, table=True):
    """来源登记。"""
    __tablename__ = "material_source"

    key: str = Field(primary_key=True)
    origin: str
    name: str
    adapter: str
    mode: str = "off"                      # off / trial / active
    config: str = "{}"
    independence_group: str = ""           # 默认等于 key；seed 时写入
    independence_version: int = 1
    cursor: Optional[str] = None
    rebuild_cursor: Optional[str] = None
    extract_version: int = 1
    extract_config_hash: str = ""
    sanitizer_version: int = 1
    sanitizer_config_hash: str = ""
    min_interval_s: int = 600
    daily_cap: int = 300
    lock_owner: Optional[str] = None
    lock_heartbeat_at: Optional[datetime] = None
    lock_until: Optional[datetime] = None
    rebuild_state: str = "idle"            # idle / running / done / failed
    rebuild_resumed_count: int = 0
    last_run_at: Optional[datetime] = None
    last_status: str = "never"             # never / ok / partial / error
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MaterialRaw(SQLModel, table=True):
    """版本化来源快照；payload 写入后不可变。"""
    __tablename__ = "material_raw"
    __table_args__ = (
        UniqueConstraint(
            "source_key", "external_id", "raw_revision",
            name="uq_material_raw_revision",
        ),
        UniqueConstraint(
            "source_key", "external_id", "source_snapshot_hash",
            "sanitizer_version", "sanitizer_config_hash",
            name="uq_material_raw_snapshot",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    source_key: str = Field(foreign_key="material_source.key", index=True)
    external_id: str = Field(index=True)
    raw_revision: int = 1
    observed_at: datetime
    observed_at_estimated: bool = False
    fetched_at: datetime = Field(default_factory=_now)
    payload: str
    source_snapshot_hash: str
    sanitized_payload_hash: str
    sanitizer_version: int = 1
    sanitizer_config_hash: str = ""
    extract_version: int = 0
    extract_config_hash: Optional[str] = None
    extract_status: str = "pending"        # pending / extracted / rejected / skipped
    reject_reason: Optional[str] = None
    payload_truncated: bool = False
    encoding_used: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class Material(SQLModel, table=True):
    """证据单元；quote 不可改写。"""
    __tablename__ = "material"
    __table_args__ = (
        UniqueConstraint(
            "kind", "scope_key", "content_fingerprint",
            name="uq_material_key",
        ),
    )

    id: str = Field(primary_key=True)
    kind: str = Field(index=True)
    origin: str
    scope_key: str = ""
    project: Optional[str] = None
    quote: str
    content_fingerprint: str = Field(index=True)
    title: Optional[str] = None
    url: Optional[str] = None
    observed_at: datetime
    first_seen_at: datetime = Field(default_factory=_now)
    last_seen_at: datetime = Field(default_factory=_now)
    expires_at: Optional[datetime] = None
    status: str = "quarantine"
    status_reason: Optional[str] = None
    quality: float = 0.0
    quality_detail: str = "{}"
    quality_model_version: int = 1
    occurrences: int = 1
    corroboration: int = 1
    needs_review: bool = False
    first_extract_version: int = 1
    last_extract_version: int = 1
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MaterialWitness(SQLModel, table=True):
    """出处边；指向不可变 raw revision。"""
    __tablename__ = "material_witness"
    __table_args__ = (
        UniqueConstraint("material_id", "raw_id", name="uq_material_witness"),
        Index(
            "uq_material_witness_primary",
            "material_id",
            unique=True,
            sqlite_where=text("is_primary = 1"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: str = Field(foreign_key="material.id", index=True)
    raw_id: int = Field(foreign_key="material_raw.id", index=True)
    source_key: str
    external_id: str
    raw_revision: int
    sanitizer_version: int
    sanitized_payload_hash: str
    quote_exact: str
    locator: str
    source_ref: Optional[str] = None
    is_primary: bool = False
    occurrence_count: int = 1
    independence_group: str = ""
    independence_version: int = 1
    current_extract_id: int = Field(foreign_key="material_extract.id")
    observed_at: datetime
    created_at: datetime = Field(default_factory=_now)


class MaterialExtract(SQLModel, table=True):
    """追加式提取历史；只记录通过硬闸的 Draft。"""
    __tablename__ = "material_extract"
    __table_args__ = (
        UniqueConstraint(
            "material_id", "raw_id", "extract_version",
            name="uq_material_extract",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: str = Field(foreign_key="material.id", index=True)
    raw_id: int = Field(foreign_key="material_raw.id", index=True)
    extract_version: int
    extract_config_hash: str
    draft_fingerprint: str
    kind: str
    quote_exact: str
    locator: str
    produced_at: datetime = Field(default_factory=_now)


class MaterialLink(SQLModel, table=True):
    """引用边。"""
    __tablename__ = "material_link"
    __table_args__ = (
        UniqueConstraint(
            "material_id", "ref_type", "ref_id", "relation",
            name="uq_material_link",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: str = Field(foreign_key="material.id", index=True)
    ref_type: str
    ref_id: str
    relation: str
    note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class MaterialSourceAudit(SQLModel, table=True):
    """来源配置审计；只增不改不删。"""
    __tablename__ = "material_source_audit"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_key: str = Field(index=True)
    field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    actor: str
    reason: str
    at: datetime = Field(default_factory=_now)


__all__ = [
    "MaterialSource",
    "MaterialRaw",
    "Material",
    "MaterialWitness",
    "MaterialExtract",
    "MaterialLink",
    "MaterialSourceAudit",
]
