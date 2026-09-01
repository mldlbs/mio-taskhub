"""Lightweight Prometheus-format metrics (no external dependencies)."""
import time
from sqlmodel import Session, text
from mio_taskhub.db import engine

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

    return "\n".join(lines) + "\n"
