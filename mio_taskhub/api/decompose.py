"""P2b 拆分 preview/apply 路由（FR-11）。"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.decomposition import (
    DecomposeError,
    apply_decomposition,
    make_apply_token,
    validate_decomposition,
)
from mio_taskhub.events import emit_event
from mio_taskhub.hierarchy import validate_enabled
from mio_taskhub.models import Task

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _parent_or_404(db: Session, task_id: str) -> Task:
    parent = db.get(Task, task_id)
    if parent is None:
        raise HTTPException(404, "task not found")
    return parent


def _reject(detail: dict, status: int = 422):
    raise HTTPException(status, detail=detail)


def _reject_event(db: Session, parent_id: str, result) -> None:
    emit_event(db, type="task_decompose_rejected", entity="task", entity_id=parent_id,
               payload={"errors": [e.get("code") for e in result.errors]})
    db.commit()


@router.post("/{task_id}/decompose/preview")
def preview_decomposition(task_id: str, body: dict,
                          db: Session = Depends(get_session)):
    if not validate_enabled():
        raise HTTPException(404, "decomposition is disabled")
    parent = _parent_or_404(db, task_id)
    if parent.parent_id or (getattr(parent, "role", None) == "child"):
        raise HTTPException(422, detail={
            "code": "decompose.depth_exceeded",
            "message": "child task cannot be decomposed again",
            "refs": [parent.id],
        })
    if parent.state in ("completed", "cancelled") or \
            getattr(parent.stage, "value", parent.stage) == "done":
        raise HTTPException(409, detail={
            "code": "decompose.parent_terminal",
            "message": "parent task is terminal",
            "refs": [parent.id],
        })
    result = validate_decomposition(body, parent)
    token = make_apply_token(parent.id, body)
    emit_event(db, type="task_decompose_previewed", entity="task", entity_id=parent.id,
               payload={"ok": not result.has_errors,
                        "errors": [e.get("code") for e in result.errors]})
    db.commit()
    return result.preview(token)


@router.post("/{task_id}/decompose/apply")
def apply_decomposition_route(task_id: str, body: dict,
                              db: Session = Depends(get_session)):
    if not validate_enabled():
        raise HTTPException(404, "decomposition is disabled")
    parent = _parent_or_404(db, task_id)

    if parent.parent_id or (getattr(parent, "role", None) == "child"):
        raise HTTPException(422, detail={
            "code": "decompose.depth_exceeded",
            "message": "child task cannot be decomposed again",
            "refs": [parent.id],
        })
    existing = db.exec(select(Task).where(Task.parent_id == parent.id)).first()
    if existing is not None or getattr(parent, "decomposition", None):
        raise HTTPException(409, detail={
            "code": "decompose.already_decomposed",
            "message": "task has already been decomposed",
            "refs": [parent.id],
        })
    if parent.state in ("completed", "cancelled") or \
            getattr(parent.stage, "value", parent.stage) == "done":
        raise HTTPException(409, detail={
            "code": "decompose.parent_terminal",
            "message": "parent task is terminal",
            "refs": [parent.id],
        })

    supplied = str(body.get("apply_token") or "").strip()
    token = make_apply_token(parent.id, body)
    if supplied and supplied != token:
        raise HTTPException(422, detail={
            "code": "decompose.preview_stale",
            "message": "apply_token does not match current payload",
            "refs": [parent.id],
        })

    result = validate_decomposition(body, parent)
    gate = (os.environ.get("MIO_DECOMPOSE_GATE") or "").strip().lower()
    if result.has_errors and gate != "warn":
        _reject_event(db, parent.id, result)
        first = result.errors[0]
        _reject({
            "code": first["code"],
            "message": first["message"],
            "refs": first.get("refs") or [],
            "errors": result.errors,
            "warnings": result.warnings,
        })
    if result.has_errors and gate == "warn":
        result.warnings.extend(result.errors)
        result.errors = []

    try:
        applied = apply_decomposition(db, parent, body, result, token)
    except DecomposeError as exc:
        raise HTTPException(exc.status, detail=exc.detail())
    return applied
