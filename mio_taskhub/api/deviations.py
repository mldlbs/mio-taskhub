# -*- coding: utf-8 -*-
"""Phase 1 偏离记录与解决端点。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from mio_taskhub.db import get_session
from mio_taskhub.events import emit_event
from mio_taskhub.models import Task

router = APIRouter(prefix="/tasks", tags=["tasks"])
SEVERITIES = {"low", "medium", "high", "critical"}
STATUSES = {"auto_logged", "open", "accepted", "rejected", "fixed", "resolved", "decided"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/{task_id}/deviations")
def log_deviation(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    description = str((body or {}).get("description") or "").strip()
    if not description:
        raise HTTPException(422, "description is required")
    severity = str((body or {}).get("severity") or "medium").strip().lower()
    if severity not in SEVERITIES:
        raise HTTPException(422, "severity must be low/medium/high/critical")
    devs = list(t.deviations or [])
    n = 1 + max([int(str(d.get("id", "D-0")).split("-")[-1])
                  for d in devs if str(d.get("id", "")).startswith("D-")] or [0])
    needs_decision = bool((body or {}).get("requires_user_decision"))
    status = "open" if severity in ("high", "critical") or needs_decision else "auto_logged"
    now = _now_iso()
    dev = {
        "id": f"D-{n}",
        "spec_ref": str((body or {}).get("spec_ref") or ""),
        "description": description,
        "severity": severity,
        "status": status,
        "attempted_fix": str((body or {}).get("attempted_fix") or ""),
        "proposed_fix": str((body or {}).get("proposed_fix") or ""),
        "resolution": "",
        "decision_ref": "",
        "decided_by": "",
        "requires_user_decision": needs_decision,
        "created_at": now,
        "updated_at": now,
        "history": [{"at": now, "from": "", "to": status, "by": "api", "note": ""}],
    }
    devs.append(dev)
    t.deviations = devs
    emit_event(db, type="task_deviation_logged", entity="task", entity_id=t.id,
               payload={"id": dev["id"], "severity": severity, "status": status})
    db.add(t)
    db.commit()
    return dev


@router.post("/{task_id}/deviations/{dev_id}/resolve")
def resolve_deviation(task_id: str, dev_id: str, body: dict,
                      db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    devs = list(t.deviations or [])
    dev = next((d for d in devs if isinstance(d, dict) and d.get("id") == dev_id), None)
    if dev is None:
        raise HTTPException(404, "deviation not found")
    status = str((body or {}).get("status") or "").strip().lower()
    if status not in ("accepted", "rejected", "fixed"):
        raise HTTPException(422, "status must be accepted/rejected/fixed")
    resolution = str((body or {}).get("resolution") or "").strip()
    decision_ref = str((body or {}).get("decision_ref") or "").strip()
    decided_by = str((body or {}).get("decided_by") or "").strip()
    if status == "accepted" and (not decision_ref or not decided_by):
        raise HTTPException(422, detail={
            "code": "gate.deviation_needs_decision",
            "message": "accepted deviation requires decision_ref and decided_by",
        })
    if status in ("rejected", "fixed") and not resolution:
        raise HTTPException(422, "resolution is required")
    if dev.get("status") not in ("open", "auto_logged", "reopened"):
        raise HTTPException(409, "deviation is already resolved")
    now = _now_iso()
    old = dev.get("status")
    updated = dict(dev)
    updated.update({
        "status": status, "resolution": resolution, "decision_ref": decision_ref,
        "decided_by": decided_by, "updated_at": now,
    })
    history = list(updated.get("history") or [])
    history.append({"at": now, "from": old, "to": status,
                    "by": decided_by or "api", "note": resolution})
    updated["history"] = history
    t.deviations = [updated if d is dev else d for d in devs]
    emit_event(db, type="task_deviation_resolved", entity="task", entity_id=t.id,
               payload={"id": dev_id, "status": status, "decision_ref": decision_ref})
    db.add(t)
    db.commit()
    return updated
