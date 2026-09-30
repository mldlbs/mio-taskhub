"""CronEngine: 基于 croniter 的定时任务调度引擎。

职责：
- 解析 cron 表达式，计算 next_run_at
- 后台轮询到期 job，执行动作（创建 task / webhook）
- 记录执行日志
- 暂停/恢复/手动触发
"""
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from croniter import croniter
from sqlalchemy import func
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.models import (
    Idea, IdeaStatus,
    ScheduledJob, ScheduledJobActionType, ScheduledJobStatus,
    ScheduledJobExecution, Task, TaskState, TaskStage,
)
from mio_taskhub.events import emit_event, broadcast_for_event

logger = logging.getLogger("cron_engine")

# 下游消费意愿检查默认阈值（P1-1）：同类未认领任务 >= 此值时跳过生成
DEFAULT_MAX_PENDING_TASKS = 3
# 生成类 webhook：未消费的 auto-generated 想法 >= 此值时跳过生成
DEFAULT_MAX_PENDING_IDEAS = 10


class WebhookFailed(Exception):
    """webhook 返回 4xx/5xx：作为失败上报（result 保留响应摘要），不做假绿。"""

    def __init__(self, summary: str, status_code: int = 0, body: str = ""):
        super().__init__(summary)
        self.status_code = status_code
        self.body = body
        self.summary = summary

POLL_INTERVAL = 10  # 秒


def validate_cron(expr: str) -> bool:
    """校验 cron 表达式是否合法（5 字段标准格式）。"""
    try:
        croniter(expr)
        return True
    except (ValueError, KeyError):
        return False


def _to_utc_aware(dt: datetime) -> datetime:
    """统一为 UTC aware：SQLite 往返丢 tz（naive 按 UTC 解释），aware 直接转换。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_next_run(cron_expr: str, after: Optional[datetime] = None) -> datetime:
    """下一次执行时间。**cron 按本机时区解释**（0 8 * * * = 本地早 8 点），返回 UTC。"""
    local = _to_utc_aware(after or datetime.now(timezone.utc)).astimezone()
    return croniter(cron_expr, local).get_next(datetime).astimezone(timezone.utc)


def compute_next_runs(cron_expr: str, count: int = 5, after: Optional[datetime] = None) -> list:
    """未来 N 次执行时间。同上：本机时区解释，返回 UTC。"""
    local = _to_utc_aware(after or datetime.now(timezone.utc)).astimezone()
    cron = croniter(cron_expr, local)
    return [cron.get_next(datetime).astimezone(timezone.utc) for _ in range(count)]


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
            skipped = None
            if job.action_type == ScheduledJobActionType.CREATE_TASK:
                # 下游消费意愿检查：同类任务已堆积未认领则跳过本次生成，
                # 避免持续制造无人认领的垃圾任务（见 system-assessment-20260930.md P1-1）。
                skipped = self._should_skip_task_creation(job, db)
                if skipped:
                    result = f"skipped: {skipped}"
                else:
                    result = self._create_task(job, db)
            elif job.action_type == ScheduledJobActionType.WEBHOOK:
                # 生成类 webhook（如「每日创意生成」）：同类 auto-generated 想法已堆积
                # 未被消费时跳过，避免每天空转产出无人处理的素材（P1-1 扩展）。
                skipped = self._should_skip_webhook_generation(job, db)
                if skipped:
                    result = f"skipped: {skipped}"
                else:
                    result = self._fire_webhook(job)
            else:
                raise ValueError(f"unknown action_type: {job.action_type}")

            if skipped:
                execution.status = "skipped"
                execution.result = result
                job.last_status = ScheduledJobStatus.OK
                job.last_error = None
            else:
                execution.status = "ok"
                execution.result = str(result) if result else None
                job.last_status = ScheduledJobStatus.OK
                job.last_error = None
        except WebhookFailed as e:
            # 2xx 之外的响应：如实记失败，并把响应摘要留在 result 便于排查
            execution.status = "error"
            execution.result = e.summary[:500]
            execution.error = f"webhook HTTP {e.status_code}"
            job.last_status = ScheduledJobStatus.ERROR
            job.last_error = e.summary[:500]
            logger.warning(f"cron job {job_id} webhook failed: {e.summary[:200]}")
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

    def _should_skip_task_creation(self, job: ScheduledJob, db: Session) -> Optional[str]:
        """下游消费意愿检查（P1-1）。

        若由同一 job 生成的任务已堆积超过阈值且仍未被认领，则返回跳过原因；
        否则返回 None。阈值与开关：
          - action_config.max_pending_tasks（默认 3，0/None 表示不限）
          - env MIO_CRON_PENDING_GUARD=0 关闭该保护
        """
        if os.environ.get("MIO_CRON_PENDING_GUARD", "1") in ("0", "false", "False", "no"):
            return None
        cfg = job.action_config or {}
        limit = cfg.get("max_pending_tasks", DEFAULT_MAX_PENDING_TASKS)
        if not limit or limit <= 0:
            return None
        # 统计该 job 生成且仍未被认领的任务（queued/ready，无 claimed_at）
        pending = db.exec(
            select(func.count())
            .select_from(Task)
            .where(
                Task.state == TaskState.QUEUED,
                Task.claimed_at.is_(None),
                Task.labels.contains(f"cron:{job.id}"),
            )
        ).one()
        pending = pending[0] if isinstance(pending, (tuple, list)) else pending
        if pending >= limit:
            return (
                f"下游未消费堆积 {pending} 条（阈值 {limit}），跳过本次生成；"
                f"待 agent 认领或清理后再触发"
            )
        return None

    def _should_skip_webhook_generation(self, job: ScheduledJob, db: Session) -> Optional[str]:
        """生成类 webhook 的下游消费意愿检查（P1-1 扩展）。

        仅对「创意生成」语义的 webhook 生效（url 命中创意生成端点，或 action_config
        显式标记 generates_ideas=true）。当 hub 中已有 >= 阈值条 auto-generated 且仍为
        NEW（未被消费/评审/拆解）的想法时，返回跳过原因，避免每日空转产出堆积素材。

        阈值：action_config.max_pending_ideas（默认 DEFAULT_MAX_PENDING_IDEAS）；
        env MIO_CRON_PENDING_GUARD=0 关闭。
        """
        if os.environ.get("MIO_CRON_PENDING_GUARD", "1") in ("0", "false", "False", "no"):
            return None
        cfg = job.action_config or {}
        url = str(cfg.get("url", ""))
        if not cfg.get("generates_ideas") and "ideas/templates/generate" not in url:
            return None  # 非生成类 webhook 不受此保护
        limit = cfg.get("max_pending_ideas", DEFAULT_MAX_PENDING_IDEAS)
        if not limit or limit <= 0:
            return None
        pending = db.exec(
            select(func.count())
            .select_from(Idea)
            .where(
                Idea.status == IdeaStatus.NEW,
                Idea.labels.contains("auto-generated"),
            )
        ).one()
        pending = pending[0] if isinstance(pending, (tuple, list)) else pending
        if pending >= limit:
            return (
                f"已有 {pending} 条 auto-generated 想法未消费（阈值 {limit}），跳过本次生成；"
                f"待评审/拆解后再触发"
            )
        return None

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
        # SSRF 防护（评估 round2 P2-A）：默认拒绝环回/私有/链路本地地址；
        # action_config.allow_private=true 可显式放开（如需内网回调）。
        from mio_taskhub.net_guard import check_outbound_url
        allowed, reason = check_outbound_url(url, allow_private=bool(cfg.get("allow_private")))
        if not allowed:
            raise WebhookFailed(f"blocked by SSRF guard: {reason}", status_code=0)
        method = cfg.get("method", "POST").upper()
        headers = cfg.get("headers", {})
        body = cfg.get("body")
        timeout = job.timeout_seconds or 30
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, url, headers=headers, json=body)
            summary = f"{resp.status_code} {resp.text[:200]}"
            if resp.status_code >= 400:
                # HTTP 失败必须如实上报，否则任务永远"假绿"、空转无人知
                raise WebhookFailed(summary, status_code=resp.status_code, body=resp.text)
            return summary

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
