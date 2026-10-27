# -*- coding: utf-8 -*-
"""TH-01 历史决策闭环的服务层（FR-2 ~ FR-8）。

职责边界：
- runtime 只提供「建议」与「写回通道」；本模块负责决策记录、结果投递与降级。
- 本地 SQLite 是权威：runtime 失败一律降级，绝不回滚 claim / submit。
- 本模块不参与候选排序、任务状态迁移或任何门控判定。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from mio_taskhub import mio_runtime as mio
from mio_taskhub.db import engine
from mio_taskhub.models import MioOutbox, RouteDecision, Run, Task
from mio_taskhub.scheduling.scheduler import Scheduler
from mio_taskhub.utils import parse_utc

logger = logging.getLogger("mio_taskhub.mio_bridge")

ROUTE_TIMEOUT_S = 1.5
OUTCOME_TIMEOUT_S = 3.0
MAX_ATTEMPTS = 8
BASE_BACKOFF_S = 30
MAX_BACKOFF_S = 1800
MAX_ERROR_CHARS = 500
_TRUE = ("1", "true", "yes", "on")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 决策记录（FR-3 / FR-4）────────────────────────────────────────────────

def route_query_text(task: Task) -> str:
    """route 的检索文本：标题 + 描述前 300 字。

    不塞 acceptance_criteria：scoreRecord 走 token 重叠，验收标准里的泛化词
    会把无关记忆拉进来（见 spec 2.2）。
    """
    title = (getattr(task, "title", "") or "").strip()
    desc = (getattr(task, "description", "") or "").strip()[:300]
    return (f"{title} {desc}".strip() or title)[:500]


def _agent_health_best(data: dict) -> Optional[str]:
    health = data.get("agentHealth") or {}
    best = health.get("best") or {}
    if not best.get("agent"):
        return None
    return "%s:%s:%s" % (best.get("agent"), best.get("successRate"), best.get("total"))


def route_view(decision_id: str, data: Optional[dict], reason: str = "none") -> Optional[dict]:
    """claim 响应用的精简视图（不复制整份历史记忆）。"""
    if data is None:
        return None
    routes = data.get("routes") or []
    suggestion = ((data.get("summary") or {}).get("suggestion") or "").strip()
    return {
        "decision_id": decision_id,
        "count": int(data.get("count") or len(routes)),
        "top_experience_id": (routes[0] or {}).get("experienceId") if routes else None,
        "agent_health_best": _agent_health_best(data),
        "degraded_reason": reason,
        "suggestion": suggestion[:300] or None,
    }


def record_route_decision(db: Session, task: Task, run: Run) -> Optional[dict]:
    """在 claim 之后写一条决策记录，并返回响应视图。

    失败路径（runtime 不可用 / 超时 / 空 project）同样落一条记录，写清原因，
    让「为什么这次没有建议」可追溯；写库失败只记日志，不影响领取。
    """
    decision_id = "rd-%s" % run.id
    project = (getattr(task, "project", "") or "").strip()
    query = route_query_text(task)
    data = None
    reason = "none"
    try:
        data, reason = mio.task_route_status(
            query, run.agent_name, project, timeout=ROUTE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — 适配层已降级，这里只兜底
        logger.warning("task_route failed unexpectedly", exc_info=True)
        data, reason = None, "runtime_unavailable"

    view = route_view(decision_id, data, reason)
    row = RouteDecision(
        id=decision_id,
        task_id=task.id,
        run_id=run.id,
        agent=run.agent_name,
        project=project,
        query=query,
        route_count=int((data or {}).get("count") or 0),
        top_experience_id=(view or {}).get("top_experience_id"),
        agent_health_best=(view or {}).get("agent_health_best"),
        degraded_reason=reason,
    )
    try:
        db.add(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(RouteDecision, decision_id)
        return route_view(decision_id, data, reason) if existing else view
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("RouteDecision write failed (claim unaffected)", exc_info=True)
    return view


def get_route_decision(db: Session, run_id: str) -> Optional[RouteDecision]:
    return db.exec(select(RouteDecision).where(RouteDecision.run_id == run_id)).first()


# ── 结果投递（FR-5 / FR-6 / FR-7）──────────────────────────────────────────

def _failure_type(outcome: str, result_text: str, verification: str) -> Optional[str]:
    """失败分类：只用已知规则，无法归类即 unknown（不臆造根因）。"""
    if outcome == "success":
        return None
    if outcome == "aborted":
        return "timeout"
    blob = "%s %s" % (result_text or "", verification or "")
    blob = blob.lower()
    if "read_evidence" in blob or "doc_missing" in blob or "diff_gate" in blob:
        return "gate_rejected"
    if blob.strip():
        return "execution_error"
    return "unknown"


def build_outcome_payload(task: Optional[Task], run: Run, outcome: str,
                          result_text: str = "", verification: str = "") -> dict:
    project = (getattr(task, "project", "") or "").strip()
    ftype = _failure_type(outcome, result_text, verification)
    summary = (result_text or "").strip()[:200]
    if ftype:
        summary = ("[%s] %s" % (ftype, summary)).strip()
    elif not summary:
        summary = "taskhub run completed"
    return {
        "outcome": outcome,
        "task": (getattr(task, "title", "") or "")[:500],
        "summary": summary[:1000],
        "verification": (verification or "")[:1000],
        "agentId": run.agent_name,
        "project": project,
        "traceId": "taskhub:%s:%s" % (run.task_id, run.id),
    }


def enqueue_outcome(db: Session, task: Optional[Task], run: Run, outcome: str,
                    result_text: str = "", verification: str = "") -> bool:
    """把一次提交结果放入投递队列（FR-5）。唯一键保证幂等，绝不抛错。

    project 为空时直接置 dead：没有正确项目归属的写回只会污染历史，
    比不写更糟（AC-9）。
    """
    payload = build_outcome_payload(task, run, outcome, result_text, verification)
    key = "run:%s:outcome" % run.id
    row = MioOutbox(
        key=key,
        run_id=run.id,
        task_id=run.task_id,
        payload=json.dumps(payload, ensure_ascii=False),
        status="pending" if payload.get("project") else "dead",
        next_at=_now(),
        last_error=None if payload.get("project") else "empty project",
    )
    try:
        db.add(row)
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("outbox enqueue failed (submit unaffected)", exc_info=True)
        return False


def _next_delay(attempts: int) -> timedelta:
    seconds = min(BASE_BACKOFF_S * (2 ** max(attempts - 1, 0)), MAX_BACKOFF_S)
    return timedelta(seconds=seconds)


def deliver_row(db: Session, row: MioOutbox) -> str:
    """投递一条；返回终态字符串 sent / failed / dead / pending。"""
    if row.status in ("sent", "dead"):
        return row.status
    try:
        payload = json.loads(row.payload or "{}")
    except Exception:  # noqa: BLE001
        row.status = "dead"
        row.last_error = "corrupt payload"
        db.add(row)
        db.commit()
        return "dead"

    data = mio.task_outcome(
        outcome=payload.get("outcome", ""),
        task=payload.get("task", ""),
        agent=payload.get("agentId", ""),
        project=payload.get("project", ""),
        summary=payload.get("summary", ""),
        verification=payload.get("verification", ""),
        trace_id=payload.get("traceId", ""),
        timeout=OUTCOME_TIMEOUT_S,
    )
    row.attempts = int(row.attempts or 0) + 1
    if data is not None:
        row.status = "sent"
        row.last_error = None
        row.next_at = _now()
    else:
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "dead"
            row.last_error = "max attempts reached"
        else:
            row.status = "failed"
            row.next_at = _now() + _next_delay(row.attempts)
            row.last_error = "runtime unavailable or CLI failed"
    db.add(row)
    db.commit()
    return row.status


def drain_outbox(limit: int = 20) -> dict:
    """投递一批到期行。runtime 整体不可用时不消耗重试次数（AC-8）。"""
    if not mio.available() or mio.mio_cli() is None:
        return {"ok": False, "reason": "runtime_unavailable", "sent": 0, "failed": 0}
    now = _now()
    counts = {"ok": True, "sent": 0, "failed": 0, "dead": 0}
    with Session(engine) as db:
        rows = db.exec(
            select(MioOutbox)
            .where(MioOutbox.status.in_(("pending", "failed")))
            .order_by(MioOutbox.next_at)
            .limit(max(1, min(int(limit), 100)))
        ).all()
        for row in rows:
            due = parse_utc(row.next_at)
            if due is not None and due > now:
                continue
            state = deliver_row(db, row)
            if state == "sent":
                counts["sent"] += 1
            elif state == "dead":
                counts["dead"] += 1
            else:
                counts["failed"] += 1
    return counts


def outbox_enabled() -> bool:
    return (os.environ.get("MIO_OUTCOME_DISABLED") or "").strip().lower() not in _TRUE


def outbox_interval_s() -> float:
    return float(int(os.environ.get("MIO_OUTCOME_INTERVAL_SEC", "60")))


class MioOutcomeJob(Scheduler):
    """定时投递 runtime 写回（FR-7）。

    - MIO_OUTCOME_DISABLED=1        关闭（默认开启）
    - MIO_OUTCOME_INTERVAL_SEC      默认 60s
    失败只记日志，不影响 taskhub 任何业务路径。
    """

    def __init__(self) -> None:
        super().__init__(interval=outbox_interval_s(),
                         get_due_tasks=lambda: [], on_enqueue=lambda _x: None)
        self.last = None

    def tick(self):
        if not outbox_enabled():
            return
        try:
            self.last = drain_outbox()
        except Exception:  # noqa: BLE001
            logger.exception("mio outcome tick failed")


# ── 待确认经验（FR-8）──────────────────────────────────────────────────────

def _pending_record(rec: dict) -> dict:
    improved = bool(rec.get("outcomeImproved"))
    return {
        "id": rec.get("id"),
        "experienceId": rec.get("experienceId"),
        "sourceAgent": rec.get("sourceAgent"),
        "targetAgent": rec.get("targetAgent"),
        "project": rec.get("project"),
        "outcomeImproved": improved,
        "confirmable": improved,
        "timestamp": rec.get("timestamp"),
        "notes": rec.get("notes"),
    }


def list_pending(project: str = "", limit: int = 20) -> dict:
    data = mio.experience_pending(project=project, limit=limit, timeout=OUTCOME_TIMEOUT_S)
    if data is None:
        return {"available": False, "reason": "runtime_unavailable",
                "project": project or None, "count": 0, "total": 0, "records": []}
    records = [_pending_record(r) for r in (data.get("records") or [])]
    return {
        "available": True,
        "reason": None,
        "project": data.get("project"),
        "count": len(records),
        "total": int(data.get("total") or len(records)),
        "records": records,
    }


def find_reuse_record(record_id: str) -> Tuple[Optional[dict], str]:
    """按 id 找 reuse 记录。返回 (record|None, state)。

    state ∈ found / not_found / runtime_unavailable
    先查 pending，再查全量（最多各 100 条）——用于区分「不存在」与「已确认」。
    """
    record_id = (record_id or "").strip()
    if not record_id:
        return None, "not_found"
    for status in ("pending", ""):
        args = ["--json", "experience", "list", "--limit", "100"]
        if status:
            args += ["--status", status]
        ok, data = mio._json_stdout(args, timeout=OUTCOME_TIMEOUT_S)  # noqa: SLF001
        if not ok or not isinstance(data, dict):
            return None, "runtime_unavailable"
        for rec in data.get("records") or []:
            if (rec.get("id") or "") == record_id:
                return rec, "found"
    return None, "not_found"


class ConfirmError(Exception):
    """确认失败（携带 HTTP 语义，由 API 层转成响应）。"""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def confirm_experience(record_id: str, by: str = "taskhub", note: str = "") -> dict:
    """人工确认一条 pending 经验（FR-8）。

    规则：
    - outcomeImproved=false → 409（失败来源不可确认，见 spec 2.5）
    - 非 auto_claim → 422
    - 已确认 → state=already（幂等）
    """
    rec, state = find_reuse_record(record_id)
    if state == "runtime_unavailable":
        raise ConfirmError(503, "runtime.unavailable", "runtime unavailable")
    if rec is None:
        raise ConfirmError(404, "experience.not_found", "experience record not found")
    if rec.get("source") not in (None, "auto_claim"):
        raise ConfirmError(422, "experience.not_auto_claim",
                           "only auto-claimed reuse records can be confirmed")
    if rec.get("confirmed") is True:
        return {"id": record_id, "state": "already",
                "outcomeImproved": bool(rec.get("outcomeImproved")),
                "confirmedBy": rec.get("confirmedBy"), "confirmedAt": None}
    if not rec.get("outcomeImproved"):
        raise ConfirmError(409, "experience.not_confirmable",
                           "outcomeImproved=false: failed outcomes cannot be verified")
    data = mio.experience_confirm(record_id, by=by, timeout=OUTCOME_TIMEOUT_S)
    if data is None:
        raise ConfirmError(503, "runtime.unavailable", "runtime confirm failed")
    confirmed_ids = data.get("confirmedIds") or []
    if not confirmed_ids and record_id in (data.get("alreadyConfirmed") or []):
        return {"id": record_id, "state": "already",
                "outcomeImproved": bool(rec.get("outcomeImproved")),
                "confirmedBy": rec.get("confirmedBy"), "confirmedAt": None}
    if not confirmed_ids and record_id in (data.get("rejected") or []):
        raise ConfirmError(422, "experience.not_auto_claim",
                           "only auto-claimed reuse records can be confirmed")
    evidence = data.get("evidence") or {}
    return {
        "id": record_id,
        "state": "confirmed",
        "outcomeImproved": bool(evidence.get("outcomeImproved", True)),
        "confirmedBy": evidence.get("confirmedBy") or by,
        "confirmedAt": evidence.get("confirmedAt"),
    }


# ── 读侧视图（供 API 层复用）───────────────────────────────────────────────

def route_decision_view(row: Optional[RouteDecision]) -> Optional[dict]:
    if row is None:
        return None
    return {
        "decision_id": row.id,
        "task_id": row.task_id,
        "agent": row.agent,
        "project": row.project,
        "query": row.query,
        "route_count": row.route_count,
        "top_experience_id": row.top_experience_id,
        "agent_health_best": row.agent_health_best,
        "degraded_reason": row.degraded_reason,
        "at": row.at.isoformat() if row.at else None,
    }


def outbox_view(row: MioOutbox, include_payload: bool = False) -> dict:
    item = {
        "key": row.key,
        "run_id": row.run_id,
        "task_id": row.task_id,
        "status": row.status,
        "attempts": row.attempts,
        "next_at": row.next_at.isoformat() if row.next_at else None,
        "last_error": row.last_error,
    }
    if include_payload:
        try:
            item["payload"] = json.loads(row.payload or "{}")
        except Exception:  # noqa: BLE001
            item["payload"] = None
    return item


def list_outbox(db: Session, status: str = "", run_id: str = "",
                limit: int = 50) -> List[MioOutbox]:
    q = select(MioOutbox)
    if status:
        q = q.where(MioOutbox.status == status)
    if run_id:
        q = q.where(MioOutbox.run_id == run_id)
    q = q.order_by(MioOutbox.at.desc()).limit(max(1, min(int(limit), 200)))
    return list(db.exec(q).all())
