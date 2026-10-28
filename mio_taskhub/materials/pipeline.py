# -*- coding: utf-8 -*-
"""素材库流水线：单批事务、raw 版本化、幂等归并、提取历史、重建。

设计：docs/taskhub/materials/spec.md §3.1 / §3.2 / §3.7
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.materials.common import (
    canonical_json, content_fingerprint, draft_fingerprint,
)
from mio_taskhub.materials.constants import BATCH_MAX_ROWS, REBUILD_LOCK_SECONDS
from mio_taskhub.materials.extract import (
    has_citable_signal, is_generic_failure,
)
from mio_taskhub.materials.models import (
    Material, MaterialExtract, MaterialLink, MaterialRaw, MaterialSource,
    MaterialWitness,
)
from mio_taskhub.materials.quality import (
    Draft, GateResult, evaluate_draft, expires_at, initial_status,
    score_material,
)
from mio_taskhub.materials.sanitize import sanitize_payload, scan_text
from mio_taskhub.utils import _now

Extractor = Callable[[Dict[str, Any], MaterialSource], List[Draft]]


class ExtractNondeterminism(RuntimeError):
    """同一 (material, raw, version) 的重复提取结果不一致。"""


class SimulatedCrash(RuntimeError):
    """测试用：模拟重建进程崩溃。"""


@dataclass
class RawRecord:
    source_key: str
    external_id: str
    observed_at: datetime
    payload: Dict[str, Any]
    source_ref: Optional[dict] = None
    project: Optional[str] = None
    occurrence_hint: int = 1


@dataclass
class IngestStats:
    raw_new: int = 0
    raw_reused: int = 0
    accepted: int = 0
    rejected: int = 0
    materials_new: int = 0
    materials_merged: int = 0
    skipped: int = 0


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def materials_dir() -> Path:
    p = Path(os.environ.get("MIO_MATERIALS_DIR")
             or (Path.home() / ".mio_taskhub" / "materials"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_quarantine(source_key: str, reason: str, draft: Draft) -> None:
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        d = materials_dir() / "quarantine"
        d.mkdir(parents=True, exist_ok=True)
        row = {
            "at": datetime.now(timezone.utc).isoformat(),
            "source_key": source_key,
            "reason": reason,
            "kind": draft.kind,
            "quote": draft.quote,
            "field": draft.field,
        }
        with (d / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        # 隔离区写入失败不影响主流程
        pass


def _source_or_raise(session: Session, source_key: str) -> MaterialSource:
    source = session.get(MaterialSource, source_key)
    if source is None:
        raise ValueError(f"material source not registered: {source_key}")
    return source


def _get_or_create_raw(session: Session, source: MaterialSource,
                       record: RawRecord) -> tuple[MaterialRaw, bool]:
    clean = sanitize_payload(record.payload or {})
    existing = session.exec(
        select(MaterialRaw).where(
            MaterialRaw.source_key == source.key,
            MaterialRaw.external_id == record.external_id,
            MaterialRaw.source_snapshot_hash == clean.source_hash,
            MaterialRaw.sanitizer_version == clean.version,
            MaterialRaw.sanitizer_config_hash == clean.config_hash,
        )
    ).first()
    if existing is not None:
        return existing, False

    max_rev = session.execute(
        select(func.max(MaterialRaw.raw_revision)).where(
            MaterialRaw.source_key == source.key,
            MaterialRaw.external_id == record.external_id,
        )
    ).scalar_one_or_none()
    raw = MaterialRaw(
        source_key=source.key,
        external_id=record.external_id,
        raw_revision=int(max_rev or 0) + 1,
        observed_at=record.observed_at,
        fetched_at=_now(),
        payload=canonical_json(clean.payload),
        source_snapshot_hash=clean.source_hash,
        sanitized_payload_hash=clean.sanitized_hash,
        sanitizer_version=clean.version,
        sanitizer_config_hash=clean.config_hash,
    )
    session.add(raw)
    session.flush()
    return raw, True


def _recount(session: Session, material: Material) -> tuple[int, int]:
    rows = session.exec(
        select(
            MaterialWitness.source_key,
            MaterialWitness.external_id,
            func.max(MaterialWitness.occurrence_count),
        ).where(MaterialWitness.material_id == material.id)
        .group_by(MaterialWitness.source_key, MaterialWitness.external_id)
    ).all()
    occurrences = sum(int(r[2] or 0) for r in rows) or 1
    corroboration = session.execute(
        select(func.count(func.distinct(MaterialWitness.independence_group)))
        .where(MaterialWitness.material_id == material.id)
    ).scalar_one()
    material.occurrences = occurrences
    material.corroboration = int(corroboration or 1)
    return material.occurrences, material.corroboration


def _accept_draft(session: Session, source: MaterialSource, raw: MaterialRaw,
                  draft: Draft, gate: GateResult, scope_key: str,
                  stats: IngestStats, now: datetime) -> None:
    fp = content_fingerprint(draft.quote)
    material = session.exec(
        select(Material).where(
            Material.kind == draft.kind,
            Material.scope_key == scope_key,
            Material.content_fingerprint == fp,
        )
    ).first()

    if material is None:
        material = Material(
            id="mat_" + uuid.uuid4().hex[:8],
            kind=draft.kind,
            origin=source.origin,
            scope_key=scope_key,
            project=scope_key or None,
            quote=draft.quote,
            content_fingerprint=fp,
            title=draft.title,
            url=draft.url,
            observed_at=raw.observed_at,
            first_seen_at=now,
            last_seen_at=raw.observed_at,
            first_extract_version=source.extract_version,
            last_extract_version=source.extract_version,
        )
        session.add(material)
        session.flush()
        stats.materials_new += 1
    else:
        material.last_seen_at = max(_utc(material.last_seen_at), _utc(raw.observed_at))
        material.last_extract_version = max(
            material.last_extract_version or 0, source.extract_version)
        stats.materials_merged += 1

    locator_json = canonical_json(gate.locator)
    extract = session.exec(
        select(MaterialExtract).where(
            MaterialExtract.material_id == material.id,
            MaterialExtract.raw_id == raw.id,
            MaterialExtract.extract_version == source.extract_version,
        )
    ).first()
    new_fp = draft_fingerprint(draft.kind, draft.quote, gate.locator)
    if extract is None:
        extract = MaterialExtract(
            material_id=material.id,
            raw_id=raw.id,
            extract_version=source.extract_version,
            extract_config_hash=source.extract_config_hash or "",
            draft_fingerprint=new_fp,
            kind=draft.kind,
            quote_exact=draft.quote,
            locator=locator_json,
        )
        session.add(extract)
        session.flush()
    elif extract.draft_fingerprint != new_fp:
        raise ExtractNondeterminism(
            f"extract mismatch for material={material.id} raw={raw.id} "
            f"version={source.extract_version}"
        )

    witness = session.exec(
        select(MaterialWitness).where(
            MaterialWitness.material_id == material.id,
            MaterialWitness.raw_id == raw.id,
        )
    ).first()
    if witness is None:
        primary_count = session.execute(
            select(func.count()).select_from(MaterialWitness).where(
                MaterialWitness.material_id == material.id,
                MaterialWitness.is_primary == True,  # noqa: E712
            )
        ).scalar_one()
        witness = MaterialWitness(
            material_id=material.id,
            raw_id=raw.id,
            source_key=source.key,
            external_id=raw.external_id,
            raw_revision=raw.raw_revision,
            sanitizer_version=raw.sanitizer_version,
            sanitized_payload_hash=raw.sanitized_payload_hash,
            quote_exact=draft.quote,
            locator=locator_json,
            source_ref=canonical_json(draft.source_ref) if draft.source_ref else None,
            is_primary=(int(primary_count or 0) == 0),
            occurrence_count=draft.occurrence_count,
            independence_group=(source.independence_group or source.key),
            independence_version=source.independence_version,
            current_extract_id=extract.id,
            observed_at=raw.observed_at,
        )
        session.add(witness)
        session.flush()
    else:
        # 旧 witness 的 raw 版本与分组快照不改；只移动当前提取指针
        witness.current_extract_id = extract.id
        witness.quote_exact = draft.quote
        witness.locator = locator_json
        witness.occurrence_count = draft.occurrence_count

    _recount(session, material)
    quality, detail = score_material(
        material.kind, material.origin, draft.evidence_tier, material.quote,
        material.last_seen_at, material.corroboration, now=now,
    )
    material.quality = quality
    material.quality_detail = canonical_json(detail)
    material.status, material.status_reason = initial_status(quality, source.mode)
    material.expires_at = expires_at(material.kind, material.last_seen_at)
    material.updated_at = now

    # 入库后复扫：命中即隔离（spec §3.3）
    if scan_text(material.quote):
        material.status = "quarantine"
        material.status_reason = "secret_rescan"


def _extract_raw(session: Session, source: MaterialSource, raw: MaterialRaw,
                 extractor: Extractor, stats: IngestStats, now: datetime,
                 force: bool = False, project: Optional[str] = None) -> None:
    if not force and raw.extract_version >= source.extract_version \
            and raw.extract_status in ("extracted", "rejected", "skipped"):
        stats.skipped += 1
        return

    try:
        payload = json.loads(raw.payload)
    except Exception as exc:
        raw.extract_status = "rejected"
        raw.reject_reason = f"payload_error:{exc}"
        stats.rejected += 1
        return

    try:
        drafts = extractor(payload, source) or []
    except Exception as exc:
        raw.extract_status = "rejected"
        raw.reject_reason = f"extract_error:{exc}"
        stats.rejected += 1
        return

    accepted = 0
    rejected = 0
    first_reason = None
    for draft in drafts:
        scope = draft.scope_key if draft.scope_key is not None else (project or "")
        gate = evaluate_draft(
            draft, payload, source.key, scope, raw.raw_revision,
            raw.sanitizer_version,
        )
        if not gate.ok:
            _write_quarantine(source.key, gate.reason, draft)
            rejected += 1
            first_reason = first_reason or gate.reason
            continue
        if source.key in ("hn", "github-trending") \
                and not has_citable_signal(draft.quote, source.key):
            _write_quarantine(source.key, "HG-10:no_citable_signal", draft)
            rejected += 1
            first_reason = first_reason or "HG-10:no_citable_signal"
            continue
        if source.key in ("run.failure", "history.root_cause") \
                and is_generic_failure(draft.quote):
            _write_quarantine(source.key, "HG-11:generic_failure", draft)
            rejected += 1
            first_reason = first_reason or "HG-11:generic_failure"
            continue
        try:
            _accept_draft(session, source, raw, draft, gate, scope, stats, now)
            accepted += 1
        except ExtractNondeterminism as exc:
            raw.extract_status = "rejected"
            raw.reject_reason = str(exc)
            stats.rejected += 1
            return

    raw.extract_version = source.extract_version
    raw.extract_config_hash = source.extract_config_hash or None
    raw.extract_status = "extracted" if accepted else ("rejected" if drafts else "skipped")
    raw.reject_reason = None if accepted else first_reason
    stats.accepted += accepted
    stats.rejected += rejected


def _ingest_records_once(source_key: str, records: Sequence[RawRecord],
                         extractor: Extractor, cursor: Optional[str],
                         now: datetime, target_engine) -> IngestStats:
    stats = IngestStats()
    with Session(target_engine or engine) as session:
        source = _source_or_raise(session, source_key)
        for record in records:
            raw, created = _get_or_create_raw(session, source, record)
            stats.raw_new += 1 if created else 0
            stats.raw_reused += 0 if created else 1
            _extract_raw(session, source, raw, extractor, stats, now,
                         project=record.project)
        source.last_run_at = now
        source.last_status = "ok"
        if cursor is not None:
            source.cursor = cursor
        session.commit()
    return stats


def ingest_records(source_key: str, records: Sequence[RawRecord],
                   extractor: Extractor, cursor: Optional[str] = None,
                   now: Optional[datetime] = None,
                   target_engine=None) -> IngestStats:
    """单批采集入库；整批一个事务，失败回滚；锁冲突退避重试。"""
    now = now or _now()
    delays = (0.0, 0.2, 0.5, 1.0)
    last_exc: Optional[Exception] = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            return _ingest_records_once(source_key, records, extractor,
                                        cursor, now, target_engine)
        except OperationalError as exc:
            text = str(exc).lower()
            if "locked" not in text and "busy" not in text:
                raise
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("ingest_records exhausted retries")


def add_link(session: Session, material_id: str, ref_type: str, ref_id: str,
             relation: str, note: Optional[str] = None,
             created_by: Optional[str] = None) -> MaterialLink:
    material = session.get(Material, material_id)
    if material is None:
        raise ValueError(f"material not found: {material_id}")
    if material.status not in ("active", "stale"):
        raise ValueError("only active/stale material can be linked")
    existing = session.exec(
        select(MaterialLink).where(
            MaterialLink.material_id == material_id,
            MaterialLink.ref_type == ref_type,
            MaterialLink.ref_id == ref_id,
            MaterialLink.relation == relation,
        )
    ).first()
    if existing is not None:
        return existing
    link = MaterialLink(
        material_id=material_id, ref_type=ref_type, ref_id=ref_id,
        relation=relation, note=note, created_by=created_by,
    )
    session.add(link)
    session.flush()
    return link


def _read_cursor(source: MaterialSource) -> int:
    if not source.rebuild_cursor:
        return 0
    try:
        return int(json.loads(source.rebuild_cursor).get("last_raw_id", 0))
    except Exception:
        return 0


def rebuild_source(source_key: str, extractor: Extractor,
                   to_version: Optional[int] = None,
                   to_config_hash: Optional[str] = None,
                   batch_size: int = BATCH_MAX_ROWS,
                   crash_after_batches: Optional[int] = None,
                   resume: bool = False,
                   now: Optional[datetime] = None) -> Dict[str, int]:
    """版本化重建；批次原子，不是全局原子。

    resume=True 时要求 rebuild_state=running 且 lock 已过期，从 rebuild_cursor 续跑。
    """
    now = now or _now()
    stats = IngestStats()

    with Session(engine) as session:
        source = _source_or_raise(session, source_key)
        if resume:
            if source.rebuild_state != "running":
                raise ValueError("resume requires rebuild_state=running")
            if source.lock_until is not None and _utc(source.lock_until) > _utc(now):
                raise ValueError("rebuild lock is still active")
            source.rebuild_resumed_count = (source.rebuild_resumed_count or 0) + 1
        else:
            source.rebuild_state = "running"
            source.rebuild_cursor = None
            source.lock_owner = f"rebuild:{uuid.uuid4().hex[:8]}"
            source.rebuild_resumed_count = 0
        if to_version is not None:
            if (to_config_hash is not None
                    and to_version == source.extract_version
                    and to_config_hash != (source.extract_config_hash or "")):
                raise ValueError("config_version_mismatch")
            source.extract_version = to_version
        if to_config_hash is not None:
            source.extract_config_hash = to_config_hash
        source.lock_heartbeat_at = now
        source.lock_until = now + timedelta(seconds=REBUILD_LOCK_SECONDS)
        session.commit()

    last_id = 0
    with Session(engine) as session:
        source = _source_or_raise(session, source_key)
        last_id = _read_cursor(source)

    batches = 0
    while True:
        with Session(engine) as session:
            source = _source_or_raise(session, source_key)
            rows = session.exec(
                select(MaterialRaw).where(MaterialRaw.id > last_id)
                .order_by(MaterialRaw.id).limit(batch_size)
            ).all()
            if not rows:
                source.rebuild_state = "done"
                source.lock_owner = None
                source.lock_until = None
                source.lock_heartbeat_at = _now()
                session.commit()
                break

            for raw in rows:
                _extract_raw(session, source, raw, extractor, stats, now, force=True)

            last_id = rows[-1].id
            source.rebuild_cursor = canonical_json({"last_raw_id": last_id})
            source.lock_heartbeat_at = _now()
            source.lock_until = _now() + timedelta(seconds=REBUILD_LOCK_SECONDS)
            session.commit()

        batches += 1
        if crash_after_batches is not None and batches >= crash_after_batches:
            raise SimulatedCrash(f"simulated crash after {batches} batch(es)")

    return {
        "raw_new": stats.raw_new,
        "accepted": stats.accepted,
        "rejected": stats.rejected,
        "materials_new": stats.materials_new,
        "materials_merged": stats.materials_merged,
        "batches": batches,
    }
