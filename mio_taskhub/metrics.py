"""Lightweight Prometheus-format metrics (no external dependencies)."""
import time
from sqlmodel import Session, text
from mio_taskhub.db import engine
from mio_taskhub.middleware import get_http_metrics

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

    return "\n".join(lines) + "\n"
