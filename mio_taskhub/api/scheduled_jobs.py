"""定时任务管理 API 路由。"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import (
    ScheduledJob, ScheduledJobActionType, ScheduledJobStatus, ScheduledJobExecution,
)
from mio_taskhub.cron_engine import (
    validate_cron, compute_next_run, compute_next_runs, get_cron_engine,
)

router = APIRouter(prefix="/scheduled-jobs", tags=["scheduled-jobs"])


def _parse_action_type(value: str) -> ScheduledJobActionType:
    try:
        return ScheduledJobActionType(value)
    except ValueError:
        raise HTTPException(400, f"invalid action_type: {value}, expected one of {[e.value for e in ScheduledJobActionType]}")


def _job_to_dict(job: ScheduledJob) -> dict:
    return {
        "id": job.id,
        "name": job.name,
        "cron_expr": job.cron_expr,
        "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        "action_type": job.action_type.value,
        "action_config": job.action_config,
        "enabled": job.enabled,
        "max_retries": job.max_retries,
        "timeout_seconds": job.timeout_seconds,
        "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
        "last_status": job.last_status.value if job.last_status else None,
        "last_error": job.last_error,
        "run_count": job.run_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.get("/validate-cron")
def validate_cron_expr(expr: str = Query(...)):
    """校验 cron 表达式并返回未来 N 次执行时间。"""
    if not validate_cron(expr):
        raise HTTPException(422, f"invalid cron expression: {expr}")
    runs = compute_next_runs(expr, count=5)
    return {
        "valid": True,
        "next_runs": [r.isoformat() for r in runs],
    }


@router.get("")
def list_jobs(db: Session = Depends(get_session)):
    jobs = db.exec(select(ScheduledJob).order_by(ScheduledJob.created_at.desc())).all()
    return [_job_to_dict(j) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_session)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "scheduled job not found")
    return _job_to_dict(job)


@router.post("")
def create_job(body: dict, db: Session = Depends(get_session)):
    cron_expr = body.get("cron_expr", "").strip()
    if not cron_expr:
        raise HTTPException(422, "cron_expr is required")
    if not validate_cron(cron_expr):
        raise HTTPException(422, f"invalid cron expression: {cron_expr}")

    action_type_str = body.get("action_type", "create_task")
    action_type = _parse_action_type(action_type_str)

    if action_type == ScheduledJobActionType.WEBHOOK:
        cfg = body.get("action_config", {})
        url = cfg.get("url", "")
        if not url or not url.startswith(("http://", "https://")):
            raise HTTPException(422, "webhook action_config.url must be http(s) URL")

    job = ScheduledJob(
        id=str(uuid.uuid4())[:8],
        name=body.get("name", "").strip() or "unnamed job",
        cron_expr=cron_expr,
        action_type=action_type,
        action_config=body.get("action_config", {}),
        enabled=body.get("enabled", True),
        max_retries=body.get("max_retries", 3),
        timeout_seconds=body.get("timeout_seconds", 30),
    )
    # 计算首次 next_run_at
    job.next_run_at = compute_next_run(cron_expr)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_to_dict(job)


@router.patch("/{job_id}")
def update_job(job_id: str, body: dict, db: Session = Depends(get_session)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "scheduled job not found")

    if "name" in body:
        job.name = body["name"]
    if "cron_expr" in body:
        cron_expr = body["cron_expr"].strip()
        if not validate_cron(cron_expr):
            raise HTTPException(422, f"invalid cron expression: {cron_expr}")
        job.cron_expr = cron_expr
        if job.enabled:
            job.next_run_at = compute_next_run(cron_expr)
    if "action_type" in body:
        job.action_type = _parse_action_type(body["action_type"])
    if "action_config" in body:
        job.action_config = body["action_config"]
    if "enabled" in body:
        job.enabled = body["enabled"]
        if job.enabled and job.cron_expr:
            job.next_run_at = compute_next_run(job.cron_expr)
        elif not job.enabled:
            job.next_run_at = None
    if "max_retries" in body:
        job.max_retries = body["max_retries"]
    if "timeout_seconds" in body:
        job.timeout_seconds = body["timeout_seconds"]

    job.updated_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_to_dict(job)


@router.delete("/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_session)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "scheduled job not found")
    db.delete(job)
    db.commit()
    return {"deleted": True}


@router.post("/{job_id}/trigger")
def trigger_job(job_id: str, db: Session = Depends(get_session)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "scheduled job not found")
    engine = get_cron_engine()
    if not engine:
        raise HTTPException(503, "cron engine not running")
    engine.trigger_now(job_id)
    return {"triggered": True}


@router.post("/{job_id}/pause")
def pause_job(job_id: str, db: Session = Depends(get_session)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "scheduled job not found")
    engine = get_cron_engine()
    if engine:
        job = engine.pause_job(job_id)
    else:
        job.enabled = False
        job.next_run_at = None
        job.updated_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
        db.refresh(job)
    return _job_to_dict(job)


@router.post("/{job_id}/resume")
def resume_job(job_id: str, db: Session = Depends(get_session)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "scheduled job not found")
    engine = get_cron_engine()
    if engine:
        job = engine.resume_job(job_id)
    else:
        job.enabled = True
        job.next_run_at = compute_next_run(job.cron_expr)
        job.updated_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
    return _job_to_dict(job)


@router.get("/{job_id}/executions")
def list_executions(
    job_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "scheduled job not found")
    execs = db.exec(
        select(ScheduledJobExecution)
        .where(ScheduledJobExecution.job_id == job_id)
        .order_by(ScheduledJobExecution.started_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": e.id,
            "job_id": e.job_id,
            "started_at": e.started_at.isoformat() if e.started_at else None,
            "finished_at": e.finished_at.isoformat() if e.finished_at else None,
            "status": e.status,
            "result": e.result,
            "error": e.error,
        }
        for e in execs
    ]
