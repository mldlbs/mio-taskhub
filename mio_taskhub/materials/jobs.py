# -*- coding: utf-8 -*-
"""P3 采集调度：按来源间隔/日上限运行适配器，单源失败隔离。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from mio_taskhub.background import BackgroundWorker
from mio_taskhub.db import engine
from mio_taskhub.materials.adapters import run_adapter
from mio_taskhub.materials.extract import extract_for_source
from mio_taskhub.materials.models import MaterialRaw, MaterialSource
from mio_taskhub.materials.pipeline import ingest_records
from mio_taskhub.utils import _now, parse_utc

logger = logging.getLogger("mio_taskhub.materials.jobs")

CHUNK = 200


def _extractor(source):
    def _run(payload, src):
        return extract_for_source(src.key, payload, src)
    return _run


def _utc(dt):
    return parse_utc(dt)


class MaterialIngestJob(BackgroundWorker):
    def __init__(self, poll_interval: float = 30.0):
        super().__init__("materials-ingest", poll_interval=poll_interval)

    def tick(self) -> None:
        now = _now()
        with Session(engine) as s:
            sources = s.exec(
                select(MaterialSource).where(MaterialSource.mode == "active")
            ).all()
            due = []
            for src in sources:
                if src.lock_until is not None:
                    lock_until = _utc(src.lock_until)
                    if lock_until is not None and lock_until > now:
                        continue
                last = _utc(src.last_run_at)
                if last is not None and last + timedelta(
                        seconds=max(30, src.min_interval_s or 600)) > now:
                    continue
                due.append(src.key)
        for key in due:
            try:
                self._run_source(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("materials source %s failed: %s", key, exc)
                self._mark_error(key, str(exc))

    def _daily_used(self, session: Session, key: str) -> int:
        today = _now().date().isoformat()
        return int(session.execute(
            select(func.count()).select_from(MaterialRaw).where(
                MaterialRaw.source_key == key,
                func.date(MaterialRaw.fetched_at) == today,
            )
        ).scalar_one() or 0)

    def _run_source(self, key: str) -> None:
        now = _now()
        with Session(engine) as s:
            src = s.get(MaterialSource, key)
            if src is None or src.mode != "active":
                return
            remaining = max(0, int(src.daily_cap or 300) - self._daily_used(s, key))
            if remaining <= 0:
                return
            # 外部日更文件按天切割，cursor 按天推进会漏掉当天追加；
            # 这里固定回看 2 天，raw 唯一键负责去重。
            if key in ("hn", "github-trending"):
                batch = run_adapter(
                    key, s, days=2, cursor=None, limit=remaining,
                )
            else:
                batch = run_adapter(
                    key, s, days=2, cursor=src.cursor, limit=remaining,
                )

        records = batch.records
        if not records:
            self._finish(key, batch.cursor, "ok", None)
            return

        stats_rejected = 0
        total = len(records)
        for i in range(0, total, CHUNK):
            chunk = records[i:i + CHUNK]
            cursor = batch.cursor if i + CHUNK >= total else None
            stats = ingest_records(key, chunk, _extractor(None), cursor=cursor)
            stats_rejected += stats.rejected
        self._finish(key, batch.cursor, "partial" if stats_rejected else "ok", None)

    def _finish(self, key: str, cursor: Optional[str], status: str,
                error: Optional[str]) -> None:
        with Session(engine) as s:
            src = s.get(MaterialSource, key)
            if src is None:
                return
            src.last_run_at = _now()
            src.last_status = status
            src.last_error = error
            if cursor is not None:
                src.cursor = cursor
            s.commit()

    def _mark_error(self, key: str, error: str) -> None:
        self._finish(key, None, "error", error[:500])
