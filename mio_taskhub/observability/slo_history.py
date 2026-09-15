"""SLO history snapshots for trend visualization.

Periodically captures SLI/SLO metrics and stores them for dashboard charts.
"""
import json
import time
import logging
import threading
from dataclasses import dataclass
from typing import Optional
from sqlmodel import SQLModel, Field, Session, Column, JSON, text
from mio_taskhub.db import engine
from mio_taskhub.observability.metrics import render_metrics

logger = logging.getLogger("mio_taskhub.observability.slo_history")

_snapshot_lock = threading.Lock()


class SLOSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: float = Field(index=True)  # unix timestamp
    availability: Optional[float] = None
    error_budget_remaining: Optional[float] = None
    latency_avg_ms: Optional[float] = None
    success_rate: Optional[float] = None
    failure_rate: Optional[float] = None
    throughput_24h: Optional[int] = None
    task_total: Optional[int] = None
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    db_pool_utilization: Optional[float] = None
    extra: Optional[dict] = Field(default=None, sa_column=Column(JSON))


def _parse_val(lines: str, metric: str) -> Optional[float]:
    for line in lines.split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        if metric == line.split("{")[0].split(" ")[0]:
            parts = line.rsplit(" ", 1)
            if len(parts) == 2:
                try:
                    return float(parts[1])
                except ValueError:
                    pass
    return None


def capture_slo_snapshot():
    """Capture current SLO metrics into DB."""
    try:
        lines = render_metrics()
        now = time.time()
        snap = SLOSnapshot(
            ts=now,
            availability=_parse_val(lines, "taskhub_slo_availability_30d"),
            error_budget_remaining=_parse_val(lines, "taskhub_slo_error_budget_remaining"),
            latency_avg_ms=_parse_val(lines, "taskhub_slo_latency_avg_ms_1d"),
            success_rate=_parse_val(lines, "taskhub_task_success_rate"),
            failure_rate=_parse_val(lines, "taskhub_task_failure_rate"),
            throughput_24h=_parse_val(lines, "taskhub_task_throughput_24h"),
            cpu_percent=_parse_val(lines, "taskhub_process_cpu_percent"),
            memory_percent=_parse_val(lines, "taskhub_process_memory_percent"),
            db_pool_utilization=_parse_val(lines, "taskhub_db_pool_utilization"),
        )
        with _snapshot_lock:
            with Session(engine) as db:
                db.add(snap)
                db.commit()
                # Keep last 7 days (1 snapshot/5min = 2016 rows)
                db.exec(text("""
                    DELETE FROM slosnapshot WHERE id NOT IN (
                        SELECT id FROM slosnapshot ORDER BY ts DESC LIMIT 2016
                    )
                """))
                db.commit()
    except Exception as e:
        logger.debug("SLO snapshot capture failed: %s", e)


def get_slo_history(hours: int = 24, limit: int = 288) -> list[dict]:
    """Get SLO snapshots for the last N hours."""
    cutoff = time.time() - hours * 3600
    try:
        with Session(engine) as db:
            rows = db.exec(
                text("SELECT * FROM slosnapshot WHERE ts > :cutoff ORDER BY ts ASC LIMIT :lim"),
                {"cutoff": cutoff, "lim": limit},
            ).all()
            return [
                {
                    "ts": r.ts,
                    "availability": r.availability,
                    "error_budget_remaining": r.error_budget_remaining,
                    "latency_avg_ms": r.latency_avg_ms,
                    "success_rate": r.success_rate,
                    "failure_rate": r.failure_rate,
                    "throughput_24h": r.throughput_24h,
                    "cpu_percent": r.cpu_percent,
                    "memory_percent": r.memory_percent,
                    "db_pool_utilization": r.db_pool_utilization,
                }
                for r in rows
            ]
    except Exception:
        return []


def cleanup_old_snapshots(days: int = 30):
    cutoff = time.time() - days * 86400
    try:
        with Session(engine) as db:
            db.exec(text("DELETE FROM slosnapshot WHERE ts < :cutoff"), {"cutoff": cutoff})
            db.commit()
    except Exception:
        pass
