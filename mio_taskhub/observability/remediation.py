import os
import time
import logging
from mio_taskhub.db import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)

# 只有「已被 agent 接手、却迟迟没有推进」的状态才算卡住。
# QUEUED 是正常排队等待领取（等几天也正常），既不能算卡住，更不能被自动重排——
# 否则会把整块待办队列反复重置。
ACTIVE_STATES = ("CLAIMED", "RUNNING", "RETRYING")
DEFAULT_STALL_MINUTES = 30


def stall_threshold_minutes() -> int:
    """卡住判定阈值（分钟）。可用环境变量 MIO_TASKHUB_STALL_MINUTES 覆盖。"""
    raw = os.environ.get("MIO_TASKHUB_STALL_MINUTES", "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
        logger.warning(
            "Invalid MIO_TASKHUB_STALL_MINUTES=%r, falling back to %d",
            raw, DEFAULT_STALL_MINUTES,
        )
    return DEFAULT_STALL_MINUTES


class RemediationEngine:
    def evaluate_stalled_tasks(self) -> list:
        """找出在活跃状态下长时间没有推进、且还有重试余额的任务。

        时间比较一律在 SQL 里用 julianday() 完成，不把时间取回 Python 算：
        原生 SQL 查出来的是字符串，做减法会抛异常（历史缺陷，导致本函数
        每 2 分钟报一次 AttributeError 且永远返回空）。
        """
        try:
            states = "', '".join(ACTIVE_STATES)
            minutes = stall_threshold_minutes()
            with engine.connect() as conn:
                result = conn.execute(text(f"""
                    SELECT id, title, stage, state,
                           (julianday('now') - julianday(COALESCE(last_transition_at, created_at))) * 1440.0 AS age_minutes
                    FROM task
                    WHERE state IN ('{states}')
                      AND COALESCE(last_transition_at, created_at) IS NOT NULL
                      AND COALESCE(retry_count, 0) < COALESCE(max_retries, 3)
                      AND (julianday('now') - julianday(COALESCE(last_transition_at, created_at))) * 1440.0 > :minutes
                    ORDER BY age_minutes DESC
                """), {"minutes": minutes})
                actions = []
                for row in result:
                    task = dict(row._mapping)
                    actions.append({
                        "type": "requeue_stuck_task",
                        "task_id": task["id"],
                        "task_title": task["title"],
                        "stage": task["stage"],
                        "state": task["state"],
                        "age_minutes": round(task["age_minutes"] or 0.0, 1),
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
