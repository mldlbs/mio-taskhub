from datetime import datetime, timezone
from sqlmodel import Session, select
from mio_taskhub.db import engine
from mio_taskhub.models import Run, RunState, Task, TaskStage, TaskState
from mio_taskhub.heartbeat import HeartbeatSweep, RunInfo
from mio_taskhub.scheduler import Scheduler

DEFAULT_TIMEOUT_SECONDS = 120


def _get_runs():
    with Session(engine) as db:
        runs = db.exec(select(Run).where(Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))).all()
        out = []
        for run in runs:
            task = db.get(Task, run.task_id)
            timeout_sec = (task.timeout_min * 60) if task and task.timeout_min else DEFAULT_TIMEOUT_SECONDS
            last_hb = run.last_heartbeat or run.started_at
            if last_hb.tzinfo is None:
                last_hb = last_hb.replace(tzinfo=timezone.utc)
            out.append(RunInfo(
                run_id=run.id, task_id=run.task_id, agent_name=run.agent_name,
                state=run.state, last_heartbeat=last_hb.timestamp(),
                attempt=run.attempt, max_retries=task.max_retries if task else 3,
                timeout_seconds=timeout_sec,
            ))
        return out


def _on_timeout(run_id: str, task_id: str):
    with Session(engine) as db:
        run = db.get(Run, run_id)
        task = db.get(Task, task_id)
        if run and run.state in (RunState.CLAIMED, RunState.RUNNING):
            run.state = RunState.FINISHED
            run.result = "heartbeat timeout"
            run.finished_at = datetime.now(timezone.utc)
            run.exit_code = 1
            db.add(run)
            if task:
                if task.attempt >= task.max_retries:
                    task.state = TaskState.FAILED
                else:
                    task.state = TaskState.QUEUED
                    task.stage = TaskStage.READY
                db.add(task)
        db.commit()


def _on_alive(run_id: str):
    pass


def _get_due_tasks():
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        tasks = db.exec(select(Task).where(Task.state == TaskState.QUEUED)).all()
        due = []
        for t in tasks:
            run_at = t.run_at
            if run_at is not None and run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if t.schedule_type == "cron" or (run_at and run_at <= now):
                due.append({"id": t.id})
        return due


def _on_enqueue(task_id: str):
    # task already queued; nothing to change, but touch for completeness
    pass


def start_background_jobs():
    sweep = HeartbeatSweep(get_runs=_get_runs, on_timeout=_on_timeout, on_alive=_on_alive)
    scheduler = Scheduler(get_due_tasks=_get_due_tasks, on_enqueue=_on_enqueue)
    sweep.start()
    scheduler.start()
    return sweep, scheduler
