"""Lightweight Prometheus-format metrics (no external dependencies)."""
import time
from sqlmodel import Session, text
from mio_taskhub.db import engine
from mio_taskhub.middleware import get_http_metrics
from mio_taskhub.background import get_thread_health

_start_time = time.time()

def render_metrics() -> str:
    lines = []
    lines.append("# HELP taskhub_uptime_seconds Process uptime in seconds")
    lines.append("# TYPE taskhub_uptime_seconds gauge")
    lines.append(f"taskhub_uptime_seconds {time.time() - _start_time:.1f}")

    lines.append("# HELP taskhub_tasks_total Total tasks by state")
    lines.append("# TYPE taskhub_tasks_total gauge")
    try:
        with Session(engine) as s:
            rows = s.exec(text("SELECT state, COUNT(*) FROM task GROUP BY state")).all()
            for state, count in rows:
                lines.append(f'taskhub_tasks_total{{state="{state}"}} {count}')
    except Exception:
        pass

    lines.append("# HELP taskhub_events_total Total events by type")
    lines.append("# TYPE taskhub_events_total gauge")
    try:
        with Session(engine) as s:
            rows = s.exec(text("SELECT type, COUNT(*) FROM event GROUP BY type")).all()
            for etype, count in rows:
                lines.append(f'taskhub_events_total{{type="{etype}"}} {count}')
    except Exception:
        pass

    lines.append("# HELP taskhub_agents_online Online agents")
    lines.append("# TYPE taskhub_agents_online gauge")
    try:
        with Session(engine) as s:
            rows = s.exec(text("SELECT status, COUNT(*) FROM agent GROUP BY status")).all()
            for status, count in rows:
                lines.append(f'taskhub_agents_online{{status="{status}"}} {count}')
    except Exception:
        pass

    lines.append("# HELP taskhub_scheduled_jobs_total Scheduled jobs by enabled status")
    lines.append("# TYPE taskhub_scheduled_jobs_total gauge")
    try:
        with Session(engine) as s:
            rows = s.exec(text("SELECT enabled, COUNT(*) FROM scheduledjob GROUP BY enabled")).all()
            for enabled, count in rows:
                lines.append(f'taskhub_scheduled_jobs_total{{enabled="{enabled}"}} {count}')
    except Exception:
        pass

    lines.append("# HELP taskhub_scheduled_job_runs_total Total scheduled job executions by status")
    lines.append("# TYPE taskhub_scheduled_job_runs_total gauge")
    try:
        with Session(engine) as s:
            rows = s.exec(text("SELECT status, COUNT(*) FROM scheduledjobexecution GROUP BY status")).all()
            for status, count in rows:
                lines.append(f'taskhub_scheduled_job_runs_total{{status="{status}"}} {count}')
    except Exception:
        pass

    # ---------- HTTP Observability Metrics ----------
    hm = get_http_metrics()

    lines.append("# HELP taskhub_http_requests_total Total HTTP requests by method,path,status")
    lines.append("# TYPE taskhub_http_requests_total counter")
    for label, count in sorted(hm["request_count"].items()):
        lines.append(f'taskhub_http_requests_total{{label="{label}"}} {count}')

    lines.append("# HELP taskhub_http_request_count_total Total HTTP request count")
    lines.append("# TYPE taskhub_http_request_count_total counter")
    lines.append(f'taskhub_http_request_count_total {hm["request_count_total"]}')

    lines.append("# HELP taskhub_http_active_requests Currently active HTTP requests")
    lines.append("# TYPE taskhub_http_active_requests gauge")
    lines.append(f'taskhub_http_active_requests {hm["active_requests"]}')

    lines.append("# HELP taskhub_http_errors_total Total HTTP errors by status class")
    lines.append("# TYPE taskhub_http_errors_total counter")
    for label, count in sorted(hm["error_count"].items()):
        lines.append(f'taskhub_http_errors_total{{class="{label}"}} {count}')

    lines.append("# HELP taskhub_http_request_duration_ms_total Cumulative HTTP request duration by method,path (ms)")
    lines.append("# TYPE taskhub_http_request_duration_ms_total counter")
    for label, duration in sorted(hm["total_duration_ms"].items()):
        lines.append(f'taskhub_http_request_duration_ms_total{{label="{label}"}} {duration:.1f}')

    # ---------- Background Thread Health Metrics ----------
    th = get_thread_health()

    lines.append("# HELP taskhub_thread_alive Background thread alive status")
    lines.append("# TYPE taskhub_thread_alive gauge")
    for name, data in sorted(th.items()):
        lines.append(f'taskhub_thread_alive{{name="{name}"}} {1 if data.get("alive") else 0}')

    lines.append("# HELP taskhub_thread_heartbeat_age_seconds Seconds since last heartbeat")
    lines.append("# TYPE taskhub_thread_heartbeat_age_seconds gauge")
    for name, data in sorted(th.items()):
        age = data.get("age_seconds", 0)
        if age != float("inf"):
            lines.append(f'taskhub_thread_heartbeat_age_seconds{{name="{name}"}} {age:.1f}')

    lines.append("# HELP taskhub_thread_consecutive_failures Consecutive failures count")
    lines.append("# TYPE taskhub_thread_consecutive_failures gauge")
    for name, data in sorted(th.items()):
        lines.append(f'taskhub_thread_consecutive_failures{{name="{name}"}} {data.get("consecutive_failures", 0)}')

    # ---------- Task Stage Dwell Time Metrics ----------
    try:
        with Session(engine) as s:
            # Stage dwell time: average time tasks spend in each stage (using last_transition_at)
            rows = s.exec(text("""
                SELECT stage, AVG(julianday('now') - julianday(last_transition_at)) * 86400.0 as avg_dwell_seconds
                FROM task
                WHERE state NOT IN ('COMPLETED', 'FAILED', 'CANCELLED') AND last_transition_at IS NOT NULL
                GROUP BY stage
            """)).all()
            for stage, avg_dwell in rows:
                if avg_dwell is not None:
                    lines.append(f'taskhub_task_stage_dwell_seconds{{stage="{stage}"}} {avg_dwell:.1f}')

            # Tasks stuck in stage > threshold (stalled) - using last_transition_at
            stalled_rows = s.exec(text("""
                SELECT stage, COUNT(*) as stuck_count
                FROM task
                WHERE state NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
                AND last_transition_at IS NOT NULL
                AND (julianday('now') - julianday(last_transition_at)) * 86400.0 > 300
                GROUP BY stage
            """)).all()
            for stage, stuck_count in stalled_rows:
                lines.append(f'taskhub_task_stalled_total{{stage="{stage}"}} {stuck_count}')

            # Task state transition latency (created -> claimed, claimed -> running, etc.)
            trans_rows = s.exec(text("""
                SELECT
                    CASE
                        WHEN state = 'QUEUED' THEN 'created_to_queued'
                        WHEN state = 'CLAIMED' THEN 'queued_to_claimed'
                        WHEN state = 'RUNNING' THEN 'claimed_to_running'
                        WHEN state = 'COMPLETED' THEN 'running_to_completed'
                        WHEN state = 'FAILED' THEN 'running_to_failed'
                        ELSE 'other'
                    END as transition,
                    AVG(julianday('now') - julianday(created_at)) * 86400.0 as avg_seconds
                FROM task
                WHERE state IN ('QUEUED', 'CLAIMED', 'RUNNING', 'COMPLETED', 'FAILED')
                GROUP BY transition
            """)).all()
            for transition, avg_seconds in trans_rows:
                if avg_seconds is not None:
                    lines.append(f'taskhub_task_transition_seconds{{transition="{transition}"}} {avg_seconds:.1f}')
    except Exception:
        pass

    return "\n".join(lines) + "\n"
