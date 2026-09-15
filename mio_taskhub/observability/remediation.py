import time
import logging
from datetime import datetime, timezone, timedelta
from mio_taskhub.db import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


class RemediationEngine:
    def evaluate_stalled_tasks(self) -> list:
        """Detect tasks stuck in non-terminal states for too long."""
        try:
            with engine.connect() as conn:
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=600)  # 10 minutes
                result = conn.execute(
                    text("SELECT id, title, stage, state, created_at FROM task WHERE state NOT IN ('completed', 'failed', 'cancelled') AND created_at < :cutoff"),
                    {"cutoff": cutoff}
                )
                actions = []
                now = datetime.now(timezone.utc)
                for row in result:
                    task = dict(row._mapping)
                    created_at = task["created_at"]
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    age_minutes = (now - created_at).total_seconds() / 60
                    if age_minutes > 30:
                        actions.append({
                            "type": "requeue_stuck_task",
                            "task_id": task["id"],
                            "task_title": task["title"],
                            "age_minutes": round(age_minutes, 1),
                            "action": "reset_task_state"
                        })
                return actions
        except Exception:
            logger.exception("Failed to evaluate stalled tasks")
            return []

    def restart_stalled_thread(self, thread_name: str) -> dict:
        """Attempt to restart a stalled background thread."""
        try:
            from mio_taskhub.background import _thread_registry
            _thread_registry.stop_all()
            self.log("restart_thread", f"Restarted thread pool due to stalled: {thread_name}", success=True)
            return {"success": True, "message": f"Thread pool restarted, stalled: {thread_name}"}
        except Exception as e:
            self.log("restart_thread", f"Failed to restart: {e}", success=False)
            return {"success": False, "error": str(e)}

    def requeue_task(self, task_id: str) -> dict:
        """Reset a stuck task back to queued state."""
        try:
            from sqlmodel import Session, select
            from mio_taskhub.models import Task, TaskState
            with Session(engine) as session:
                task = session.get(Task, task_id)
                if task and task.state not in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
                    task.state = TaskState.QUEUED
                    task.retry_count = (task.retry_count or 0) + 1
                    session.add(task)
                    session.commit()
                    self.log("requeue_task", f"Requeued task {task_id}", success=True)
                    return {"success": True, "task_id": task_id}
            self.log("requeue_task", f"Task {task_id} not found or in terminal state", success=False)
            return {"success": False, "error": "task not found or in terminal state"}
        except Exception as e:
            self.log("requeue_task", f"Failed to requeue {task_id}: {e}", success=False)
            return {"success": False, "error": str(e)}

    def log(self, action: str, message: str, success: bool = True):
        try:
            with engine.connect() as conn:
                conn.execute(
                    text("INSERT INTO insight (ts, kind, title, description, severity) VALUES (:ts, :kind, :title, :desc, :severity)"),
                    {"ts": time.time(), "kind": "remediation", "title": f"Auto-remediation: {action}",
                     "desc": message, "severity": "info" if success else "warning"}
                )
                conn.commit()
        except Exception:
            logger.exception("Failed to log remediation")

    def recent(self, limit: int = 20) -> list:
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT * FROM insight WHERE kind = 'remediation' ORDER BY ts DESC LIMIT :limit"),
                    {"limit": limit}
                )
                return [dict(row._mapping) for row in result]
        except Exception:
            logger.exception("Failed to query remediation log")
            return []
