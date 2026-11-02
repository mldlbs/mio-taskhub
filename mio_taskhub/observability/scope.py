# -*- coding: utf-8 -*-
"""交付面口径（278）的唯一实现。

页面、observability report、Prometheus metrics 都必须引用本模块，
禁止在各自代码里复制过滤条件。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

from sqlmodel import Session, text

DELIVERY_NOISE_BEFORE = "2026-10-21"
DELIVERY_SCOPE_ID = "delivery"


def delivery_stats(session: Session) -> Dict[str, dict]:
    scope_rows = {
        str(st): int(c) for st, c in session.exec(text(
            "SELECT state, COUNT(*) FROM task "
            "WHERE state != 'CANCELLED' "
            "  AND NOT (state='FAILED' AND UPPER(task_kind)='REVIEW' "
            "           AND created_at < :noise_before) "
            "  AND COALESCE(labels,'[]') NOT LIKE '%part-of:%' "
            "GROUP BY state"
        ), params={"noise_before": DELIVERY_NOISE_BEFORE}).all()
    }
    total = sum(scope_rows.values())
    completed = scope_rows.get("COMPLETED", 0)
    failed = scope_rows.get("FAILED", 0)
    queued = scope_rows.get("QUEUED", 0)

    hist = session.exec(text(
        "SELECT "
        "SUM(CASE WHEN state='COMPLETED' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN state='FAILED' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN state='CANCELLED' THEN 1 ELSE 0 END), "
        "COUNT(*) FROM task WHERE state IN ('COMPLETED','FAILED','CANCELLED')"
    )).first() or (0, 0, 0, 0)
    h_completed, h_failed, h_cancelled, h_total = [int(x or 0) for x in hist]

    def rate(n: int, d: int):
        return round(n / d, 4) if d else None

    return {
        "scope": {
            "id": DELIVERY_SCOPE_ID,
            "label": "当前交付面",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "definition": [
                "state != CANCELLED",
                f"not (FAILED and task_kind=REVIEW and created_at < {DELIVERY_NOISE_BEFORE})",
                "labels not like part-of:%",
            ],
            "total": total,
            "completed": completed,
            "failed": failed,
            "queued": queued,
            "success_rate": rate(completed, total),
            "failure_rate": rate(failed, total),
            "queued_rate": rate(queued, total),
            "by_state": scope_rows,
        },
        "history": {
            "label": "历史全量终态",
            "total": h_total,
            "completed": h_completed,
            "failed": h_failed,
            "cancelled": h_cancelled,
            "success_rate": rate(h_completed, h_total),
            "failure_rate": rate(h_failed, h_total),
        },
    }
