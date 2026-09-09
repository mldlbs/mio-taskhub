"""CronEngine: 基于 croniter 的定时任务调度引擎。

职责：
- 解析 cron 表达式，计算 next_run_at
- 后台轮询到期 job，执行动作（创建 task / webhook）
- 记录执行日志
- 暂停/恢复/手动触发
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from croniter import croniter
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.models import (
    ScheduledJob, ScheduledJobActionType, ScheduledJobStatus,
    ScheduledJobExecution, Task, TaskState, TaskStage,
)
from mio_taskhub.events import emit_event, broadcast_for_event

logger = logging.getLogger("cron_engine")

POLL_INTERVAL = 10  # 秒


def validate_cron(expr: str) -> bool:
    """校验 cron 表达式是否合法（5 字段标准格式）。"""
    try:
        croniter(expr)
        return True
    except (ValueError, KeyError):
        return False


def compute_next_run(cron_expr: str, after: Optional[datetime] = None) -> datetime:
    """计算下一次执行时间。"""
    base = after or datetime.now(timezone.utc)
    cron = croniter(cron_expr, base)
    return cron.get_next(datetime)


def compute_next_runs(cron_expr: str, count: int = 5, after: Optional[datetime] = None) -> list:
    """计算未来 N 次执行时间。"""
    base = after or datetime.now(timezone.utc)
    cron = croniter(cron_expr, base)
    return [cron.get_next(datetime) for _ in range(count)]


class CronEngine:
    """后台定时任务调度引擎。"""

    def __init__(self, poll_interval: float = POLL_INTERVAL):
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._executing: set = set()  # 正在执行的 job_id，防止并发

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="cron-engine")
        self._thread.start()
        logger.info("cron engine started")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("cron engine stopped")

    def tick(self):
        """主循环：扫描到期 job 并执行。"""
        now = datetime.now(timezone.utc)
        with Session(engine) as db:
            jobs = db.exec(
                select(ScheduledJob).where(
                    ScheduledJob.enabled == True,
                    ScheduledJob.next_run_at.is_not(None),
                    ScheduledJob.next_run_at <= now,
                )
            ).all()
            for job in jobs:
                if job.id in self._executing:
                    continue
                self._executing.add(job.id)
                try:
                    self._execute_job(job.id, db)
                except Exception:
                    logger.exception(f"cron job {job.id} execution failed")
                finally:
                    self._executing.discard(job.id)

    def _execute_job(self, job_id: str, db: Session):
        """执行单个 job。"""
        job = db.get(ScheduledJob, job_id)
        if not job or not job.enabled:
            return

        execution = ScheduledJobExecution(job_id=job_id, started_at=datetime.now(timezone.utc))
        try:
            if job.action_type == ScheduledJobActionType.CREATE_TASK:
                result = self._create_task(job, db)
            elif job.action_type == ScheduledJobActionType.WEBHOOK:
                result = self._fire_webhook(job)
            else:
                raise ValueError(f"unknown action_type: {job.action_type}")

            execution.status = "ok"
            execution.result = str(result) if result else None
            job.last_status = ScheduledJobStatus.OK
            job.last_error = None
        except Exception as e:
            execution.status = "error"
            execution.error = str(e)[:500]
            job.last_status = ScheduledJobStatus.ERROR
            job.last_error = str(e)[:500]
            logger.error(f"cron job {job_id} failed: {e}")
        finally:
            execution.finished_at = datetime.now(timezone.utc)
            job.last_run_at = execution.started_at
            job.run_count += 1
            # 计算下一次执行时间
            try:
                job.next_run_at = compute_next_run(job.cron_expr, job.last_run_at)
            except Exception:
                job.next_run_at = None
            job.updated_at = datetime.now(timezone.utc)
            db.add(execution)
            db.add(job)
            # 发事件（必须在 commit 前，保证 event 随事务持久化）
            event = emit_event(
                db,
                type="scheduled_job_executed",
                entity="scheduled_job",
                entity_id=job_id,
                payload={
                    "status": execution.status,
                    "action_type": job.action_type.value,
                    "result": execution.result,
                    "error": execution.error,
                },
            )
            db.commit()
            broadcast_for_event(event)

    def _create_task(self, job: ScheduledJob, db: Session) -> str:
        """根据 job.action_config 创建一个 Task。"""
        cfg = job.action_config or {}
        task = Task(
            id=str(uuid.uuid4())[:8],
            title=cfg.get("title", f"[定时] {job.name}"),
            description=cfg.get("description", ""),
            target_agent_type=cfg.get("target_agent_type"),
            priority=cfg.get("priority", 0),
            schedule_type="once",
            stage=TaskStage(cfg.get("stage", "ready")),
            labels=cfg.get("labels", []) + [f"cron:{job.id}"],
            project=cfg.get("project", ""),
            workspace=cfg.get("workspace", ""),
            max_retries=cfg.get("max_retries", job.max_retries),
        )
        db.add(task)
        db.commit()
        logger.info(f"created task {task.id} from cron job {job.id}")
        return task.id

    def _fire_webhook(self, job: ScheduledJob) -> str:
        """发送 Webhook HTTP 请求。"""
        cfg = job.action_config or {}
        url = cfg.get("url", "")
        if not url:
            raise ValueError("webhook url is required")
        method = cfg.get("method", "POST").upper()
        headers = cfg.get("headers", {})
        body = cfg.get("body")
        timeout = job.timeout_seconds or 30
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, url, headers=headers, json=body)
            return f"{resp.status_code} {resp.text[:200]}"

    # ---- 外部操作 ---------------------------------------------------------
    def add_job(self, job: ScheduledJob) -> ScheduledJob:
        """新增 job 并计算首次 next_run_at。"""
        if job.enabled and job.cron_expr:
            job.next_run_at = compute_next_run(job.cron_expr)
        return job

    def pause_job(self, job_id: str) -> Optional[ScheduledJob]:
        with Session(engine) as db:
            job = db.get(ScheduledJob, job_id)
            if not job:
                return None
            job.enabled = False
            job.next_run_at = None
            job.updated_at = datetime.now(timezone.utc)
            db.add(job)
            db.commit()
            db.refresh(job)
            return job

    def resume_job(self, job_id: str) -> Optional[ScheduledJob]:
        with Session(engine) as db:
            job = db.get(ScheduledJob, job_id)
            if not job:
                return None
            job.enabled = True
            job.next_run_at = compute_next_run(job.cron_expr)
            job.updated_at = datetime.now(timezone.utc)
            db.add(job)
            db.commit()
            db.refresh(job)
            return job

    def trigger_now(self, job_id: str) -> bool:
        """立即执行一次（不修改 next_run_at）。"""
        with Session(engine) as db:
            job = db.get(ScheduledJob, job_id)
            if not job:
                return False
            self._execute_job(job_id, db)
            return True

    def remove_job(self, job_id: str) -> bool:
        with Session(engine) as db:
            job = db.get(ScheduledJob, job_id)
            if not job:
                return False
            db.delete(job)
            db.commit()
            return True

    def _run(self):
        while not self._stop.wait(self.poll_interval):
            try:
                self.tick()
            except Exception:
                logger.exception("cron engine tick failed")


_engine: Optional[CronEngine] = None


def start_cron_engine() -> CronEngine:
    global _engine
    if _engine is None:
        _engine = CronEngine()
    _engine.start()
    return _engine


def stop_cron_engine():
    global _engine
    if _engine:
        _engine.stop()


def get_cron_engine() -> Optional[CronEngine]:
    return _engine
