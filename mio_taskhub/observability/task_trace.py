"""Task full-chain tracing: per-task lifecycle span visualization.

Builds a timeline from TaskEvent records showing every state/stage transition
with duration, actor, and reason.
"""
import logging
from typing import Optional
from sqlmodel import Session, text
from mio_taskhub.db import engine

logger = logging.getLogger("mio_taskhub.observability.task_trace")


def get_task_trace(task_id: str) -> dict:
    """Build a full lifecycle trace for a task.

    Returns:
        {
            "task_id": "...",
            "title": "...",
            "created_at": "...",
            "spans": [
                {
                    "event_type": "created",
                    "from_state": null, "to_state": "queued",
                    "from_stage": null, "to_stage": "ready",
                    "actor_type": null, "actor_id": null,
                    "reason": "",
                    "started_at": "...",
                    "duration_seconds": 123.4,
                    "metadata": {}
                },
                ...
            ],
            "summary": {
                "total_duration_seconds": ...,
                "time_in_states": {"queued": 60, "claimed": 30, ...},
                "time_in_stages": {"brainstorming": 120, ...},
                "transition_count": 5,
                "retry_count": 0,
                "actor_summary": {"agent-a": 3, "user": 1}
            }
        }
    """
    try:
        with Session(engine) as db:
            task_row = db.exec(
                text("SELECT id, title, created_at, state, stage, retry_count FROM task WHERE id = :id"),
                {"id": task_id},
            ).first()
            if not task_row:
                return {"error": f"Task {task_id} not found"}

            events = db.exec(
                text("""
                    SELECT event_type, from_state, from_stage, to_state, to_stage,
                           actor_type, actor_id, reason, event_metadata, created_at
                    FROM taskevent
                    WHERE task_id = :tid
                    ORDER BY created_at ASC
                """),
                {"tid": task_id},
            ).all()

            spans = []
            time_in_states = {}
            time_in_stages = {}
            actor_summary = {}

            for i, ev in enumerate(events):
                started = ev.created_at
                if i + 1 < len(events):
                    ended = events[i + 1].created_at
                else:
                    ended = None

                duration = None
                if started and ended:
                    duration = (ended - started).total_seconds()

                span = {
                    "event_type": ev.event_type,
                    "from_state": ev.from_state,
                    "to_state": ev.to_state,
                    "from_stage": ev.from_stage,
                    "to_stage": ev.to_stage,
                    "actor_type": ev.actor_type,
                    "actor_id": ev.actor_id,
                    "reason": ev.reason or "",
                    "started_at": started.isoformat() if started else None,
                    "duration_seconds": round(duration, 2) if duration else None,
                    "metadata": ev.event_metadata if hasattr(ev, "event_metadata") else None,
                }
                spans.append(span)

                # Aggregate time
                if duration and ev.to_state:
                    time_in_states[ev.to_state] = time_in_states.get(ev.to_state, 0) + duration
                if duration and ev.to_stage:
                    time_in_stages[ev.to_stage] = time_in_stages.get(ev.to_stage, 0) + duration
                if ev.actor_id:
                    actor_summary[ev.actor_id] = actor_summary.get(ev.actor_id, 0) + 1

            # Total duration
            total = None
            if spans:
                first_start = spans[0]["started_at"]
                last_end = None
                for s in reversed(spans):
                    if s["started_at"]:
                        last_end = s["started_at"]
                        break
                if first_start and last_end:
                    from datetime import datetime
                    t1 = datetime.fromisoformat(first_start)
                    t2 = datetime.fromisoformat(last_end)
                    total = round((t2 - t1).total_seconds(), 2)

            return {
                "task_id": task_id,
                "title": task_row.title,
                "created_at": task_row.created_at.isoformat() if task_row.created_at else None,
                "current_state": task_row.state,
                "current_stage": task_row.stage,
                "spans": spans,
                "summary": {
                    "total_duration_seconds": total,
                    "time_in_states": {k: round(v, 2) for k, v in time_in_states.items()},
                    "time_in_stages": {k: round(v, 2) for k, v in time_in_stages.items()},
                    "transition_count": len(spans),
                    "retry_count": task_row.retry_count or 0,
                    "actor_summary": actor_summary,
                },
            }
    except Exception as e:
        logger.error("Failed to build task trace: %s", e)
        return {"error": str(e)}


def get_task_traces_summary(limit: int = 50) -> list[dict]:
    """Get recent task lifecycle summaries for dashboard."""
    try:
        with Session(engine) as db:
            rows = db.exec(text("""
                SELECT t.id, t.title, t.state, t.stage, t.created_at, t.completed_at,
                       t.retry_count,
                       (SELECT COUNT(*) FROM taskevent WHERE task_id = t.id) as event_count
                FROM task t
                WHERE t.state IN ('completed', 'failed', 'cancelled')
                ORDER BY t.created_at DESC
                LIMIT :lim
            """), {"lim": limit}).all()

            results = []
            for r in rows:
                duration = None
                if r.created_at and r.completed_at:
                    duration = round((r.completed_at - r.created_at).total_seconds(), 2)
                results.append({
                    "task_id": r.id,
                    "title": r.title,
                    "state": r.state,
                    "stage": r.stage,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "duration_seconds": duration,
                    "retry_count": r.retry_count or 0,
                    "event_count": r.event_count,
                })
            return results
    except Exception as e:
        logger.error("Failed to get trace summaries: %s", e)
        return []
