"""Lightweight Prometheus-format metrics (no external dependencies)."""
import os
import time
import threading
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

            # ========== USE Metrics (Utilization / Saturation / Errors) ==========

            # --- Process ---
            try:
                import psutil
                proc = psutil.Process(os.getpid())

                # CPU
                cpu_pct = proc.cpu_percent(interval=0.1)
                lines.append(f'taskhub_process_cpu_percent {cpu_pct:.1f}')

                # Memory
                mem = proc.memory_info()
                lines.append(f'taskhub_process_memory_rss_bytes {mem.rss}')
                lines.append(f'taskhub_process_memory_vms_bytes {mem.vms}')

                mem_pct = proc.memory_percent()
                lines.append(f'taskhub_process_memory_percent {mem_pct:.1f}')

                # Threads
                thread_count = proc.num_threads()
                lines.append(f'taskhub_process_threads {thread_count}')

                # File descriptors (Linux only)
                try:
                    fds = proc.num_fds()
                    lines.append(f'taskhub_process_fds {fds}')
                except (AttributeError, psutil.AccessDenied):
                    pass

                # Open connections
                try:
                    conns = proc.num_connections()
                    lines.append(f'taskhub_process_connections {conns}')
                except (psutil.AccessDenied, OSError):
                    pass

            except ImportError:
                # psutil not available — fallback to /proc (Linux) or skip
                pass

            # --- SQLAlchemy Connection Pool ---
            try:
                pool = engine.pool
                checked_out = pool.checkedout()
                checked_in = pool.checkedin()
                overflow = pool.overflow()
                size = pool.size()

                lines.append(f'taskhub_db_pool_size {size}')
                lines.append(f'taskhub_db_pool_checked_out {checked_out}')
                lines.append(f'taskhub_db_pool_checked_in {checked_in}')
                lines.append(f'taskhub_db_pool_overflow {overflow}')

                # Saturation: ratio of checked out to total capacity
                capacity = size + pool._max_overflow if hasattr(pool, '_max_overflow') else size
                if capacity > 0:
                    lines.append(f'taskhub_db_pool_utilization {checked_out / capacity:.4f}')

            except Exception:
                pass

            # --- Thread pool (background workers) ---
            try:
                th = get_thread_health()
                alive_count = sum(1 for d in th.values() if d.get("alive"))
                total_count = len(th)
                lines.append(f'taskhub_thread_pool_alive {alive_count}')
                lines.append(f'taskhub_thread_pool_total {total_count}')
                if total_count > 0:
                    lines.append(f'taskhub_thread_pool_utilization {alive_count / total_count:.4f}')
            except Exception:
                pass

            # ========== Business Metrics ==========

            # Task success rate (completed / total terminal)
            rate_rows = s.exec(text("""
                SELECT
                    SUM(CASE WHEN state = 'COMPLETED' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN state = 'FAILED' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN state = 'CANCELLED' THEN 1 ELSE 0 END) as cancelled,
                    COUNT(*) as total_terminal
                FROM task
                WHERE state IN ('COMPLETED', 'FAILED', 'CANCELLED')
            """)).first()
            if rate_rows and rate_rows[3] and rate_rows[3] > 0:
                completed, failed, cancelled, total = rate_rows
                lines.append(f'taskhub_task_success_rate {completed / total:.4f}')
                lines.append(f'taskhub_task_failure_rate {failed / total:.4f}')
                lines.append(f'taskhub_task_cancel_rate {cancelled / total:.4f}')
                lines.append(f'taskhub_task_terminal_total {total}')

            # Task throughput (tasks created per hour in last 24h)
            throughput_rows = s.exec(text("""
                SELECT COUNT(*) as cnt
                FROM task
                WHERE created_at IS NOT NULL
                AND julianday('now') - julianday(created_at) <= 1.0
            """)).first()
            if throughput_rows:
                lines.append(f'taskhub_task_throughput_24h {throughput_rows[0]}')

            throughput_7d = s.exec(text("""
                SELECT COUNT(*) as cnt
                FROM task
                WHERE created_at IS NOT NULL
                AND julianday('now') - julianday(created_at) <= 7.0
            """)).first()
            if throughput_7d:
                lines.append(f'taskhub_task_throughput_7d {throughput_7d[0]}')

            # Retry metrics
            retry_rows = s.exec(text("""
                SELECT
                    SUM(retry_count) as total_retries,
                    AVG(retry_count) as avg_retries,
                    MAX(retry_count) as max_retries,
                    SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) as retried_tasks
                FROM task
                WHERE state IN ('COMPLETED', 'FAILED', 'CANCELLED')
            """)).first()
            if retry_rows and retry_rows[0] is not None:
                lines.append(f'taskhub_task_retries_total {retry_rows[0]}')
                lines.append(f'taskhub_task_retries_avg {retry_rows[1]:.2f}')
                lines.append(f'taskhub_task_retries_max {retry_rows[2]}')
                lines.append(f'taskhub_task_retried_count {retry_rows[3]}')

            # Average completion time (completed tasks only)
            avg_time = s.exec(text("""
                SELECT AVG(julianday(completed_at) - julianday(created_at)) * 86400.0 as avg_seconds
                FROM task
                WHERE state = 'COMPLETED'
                AND created_at IS NOT NULL AND completed_at IS NOT NULL
            """)).first()
            if avg_time and avg_time[0] is not None:
                lines.append(f'taskhub_task_avg_completion_seconds {avg_time[0]:.1f}')

            # P50/P90/P99 completion time
            p50 = s.exec(text("""
                SELECT AVG(julianday(completed_at) - julianday(created_at)) * 86400.0
                FROM (
                    SELECT julianday(completed_at) - julianday(created_at) as dur
                    FROM task
                    WHERE state = 'COMPLETED'
                    AND created_at IS NOT NULL AND completed_at IS NOT NULL
                    ORDER BY dur
                    LIMIT (SELECT MAX(1, COUNT(*) / 2) FROM task WHERE state = 'COMPLETED')
                )
            """)).first()
            if p50 and p50[0] is not None:
                lines.append(f'taskhub_task_completion_p50_seconds {p50[0]:.1f}')

            # Agent utilization (agents with active tasks / total online agents)
            agent_util = s.exec(text("""
                SELECT
                    (SELECT COUNT(DISTINCT assigned_agent) FROM task WHERE state = 'RUNNING') as active_agents,
                    (SELECT COUNT(*) FROM agent WHERE status = 'online') as online_agents
            """)).first()
            if agent_util and agent_util[1] and agent_util[1] > 0:
                lines.append(f'taskhub_agent_utilization {agent_util[0] / agent_util[1]:.4f}')
                lines.append(f'taskhub_agents_active {agent_util[0]}')
                lines.append(f'taskhub_agents_online {agent_util[1]}')

    except Exception:
        pass

    return "\n".join(lines) + "\n"
