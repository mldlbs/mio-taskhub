import os
import sys
import time
import logging
import asyncio
import uvicorn
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Response, WebSocket
from fastapi.staticfiles import StaticFiles
import secrets
from fastapi.responses import JSONResponse
from mio_taskhub.db import get_session, init_db
from mio_taskhub.api import tasks, task_stages, task_graph, task_subtasks, templates, agents, runs, plans, board, ideas, idea_templates, idea_scoring, adr, discussions, events, nightrun, memory, scheduled_jobs, task_documents, reviews, ideas_breakdown, ideas_discussion, observability
from mio_taskhub.api.insights import router as insights_router
from mio_taskhub.api.board import board_summary as _board_summary
from mio_taskhub.observability.logging_config import setup_logging
from mio_taskhub.middleware import RequestIDMiddleware, RateLimitMiddleware
from mio_taskhub.events import ws_manager
from mio_taskhub.observability.otel import init_otel, instrument_app, instrument_sqlalchemy, instrument_httpx, shutdown_otel
from mio_taskhub.observability.alerts import init_alert_manager, get_alert_manager
from mio_taskhub.db import engine as db_engine


def get_token(args_token=None):
    return os.environ.get("MIO_TASKHUB_TOKEN") or args_token or ""


def generate_token() -> str:
    return secrets.token_urlsafe(24)


def make_auth_middleware():
    async def auth_middleware(request, call_next):
        token = getattr(request.app.state, "auth_token", "")
        if not token:
            return await call_next(request)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if secrets.compare_digest(auth, f"Bearer {token}"):
            return await call_next(request)
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return auth_middleware

setup_logging()


@asynccontextmanager
async def lifespan(app):
    # Initialize OpenTelemetry
    init_otel()
    instrument_app(app)
    instrument_sqlalchemy(db_engine)
    instrument_httpx()

    from .background import start_background_jobs, _thread_registry, register_thread
    from mio_taskhub.ops.git_sync import start_git_sync_worker, stop_git_sync_worker
    from mio_taskhub.scheduling.night_runner import start_night_runner, stop_night_runner
    from mio_taskhub.scheduling.cron_engine import start_cron_engine, stop_cron_engine
    app.state.background = start_background_jobs()
    start_git_sync_worker()
    start_night_runner()
    cron_engine = start_cron_engine()
    # SQLite auto-backup (hourly snapshots, 31 kept)
    from mio_taskhub.ops.backup import SQLiteBackup
    from mio_taskhub.db import DB_PATH
    backup = SQLiteBackup(DB_PATH)
    backup.start()
    register_thread("backup", backup._thread, backup)
    app.state.backup = backup

    # Initialize alert manager
    alert_mgr = init_alert_manager()
    app.state.alert_manager = alert_mgr

    # Initialize custom alert rules evaluator
    from mio_taskhub.observability.alert_rules import init_custom_evaluator
    init_custom_evaluator()

    # Start WebSocket real-time metrics push
    from mio_taskhub.observability.ws_push import start_ws_push
    start_ws_push()

    # Start SLO snapshot capture (every 5 minutes)
    import threading
    def _slo_loop():
        from mio_taskhub.observability.slo_history import capture_slo_snapshot
        while True:
            capture_slo_snapshot()
            time.sleep(300)
    _slo_thread = threading.Thread(target=_slo_loop, daemon=True, name="slo-snapshot")
    _slo_thread.start()

    # Start dep metrics snapshot capture (every 5 minutes)
    from mio_taskhub.observability.dep_persist import DepMetricsPersist
    _dep_persist = DepMetricsPersist()
    def _dep_persist_loop():
        while True:
            time.sleep(300)
            _dep_persist.snapshot()
    _dep_persist_thread = threading.Thread(target=_dep_persist_loop, daemon=True, name="dep-persist")
    _dep_persist_thread.start()
    register_thread("dep-persist", _dep_persist_thread, _dep_persist)

    # Start insights evaluator (every 60 seconds)
    from mio_taskhub.observability.insights import InsightsEngine
    _insights_engine = InsightsEngine()
    async def _insights_eval_loop():
        while True:
            await asyncio.sleep(60)
            try:
                from mio_taskhub.observability.metrics import render_metrics
                import re
                metrics_text = render_metrics()
                metrics = {}
                for match in re.finditer(r'(\w+)\s+([\d.]+)', metrics_text):
                    name, val = match.groups()
                    try:
                        metrics[name] = float(val)
                    except ValueError:
                        pass
                _insights_engine.evaluate(metrics)
            except Exception:
                pass
    _insights_thread = threading.Thread(target=lambda: asyncio.run(_insights_eval_loop()), daemon=True, name="insights-eval")
    _insights_thread.start()
    register_thread("insights-eval", _insights_thread, None)

    # Start remediation evaluator (every 2 minutes)
    from mio_taskhub.observability.remediation import RemediationEngine
    _remediation_engine = RemediationEngine()
    async def _remediation_eval_loop():
        while True:
            await asyncio.sleep(120)
            try:
                stalled = _remediation_engine.evaluate_stalled_tasks()
                for action in stalled[:5]:
                    if action["type"] == "requeue_stuck_task":
                        _remediation_engine.requeue_task(action["task_id"])
            except Exception:
                pass
    _remediation_thread = threading.Thread(target=lambda: asyncio.run(_remediation_eval_loop()), daemon=True, name="remediation-eval")
    _remediation_thread.start()
    register_thread("remediation-eval", _remediation_thread, None)

    yield
    jobs = getattr(app.state, "background", None)
    if jobs:
        for job in jobs:
            job.stop()
    # Use ThreadRegistry for unified shutdown ordering
    _thread_registry.stop_all()
    stop_git_sync_worker()
    stop_night_runner()
    stop_cron_engine()
    # Gracefully close DB connections
    from mio_taskhub.db import engine
    engine.dispose()
    # Shutdown OpenTelemetry
    shutdown_otel()


app = FastAPI(
    title="mio-taskhub",
    version="0.2.1",
    description="Multi-agent R&D dispatch system with state machine, task lifecycle, and real-time notifications.",
    lifespan=lifespan,
)
# v3 UX: gzip 响应压缩（仅 > 1KB 才有收益）
# 注意：GZipMiddleware 必须先 add（在最内层），否则会被 BaseHTTPMiddleware 拦截
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)
init_db()

app.include_router(templates.router, prefix="/api/v1", tags=["tasks"])
app.include_router(reviews.router, prefix="/api/v1", tags=["reviews"])
app.include_router(task_stages.router, prefix="/api/v1", tags=["tasks"])
app.include_router(task_graph.router, prefix="/api/v1", tags=["tasks"])
app.include_router(task_subtasks.router, prefix="/api/v1", tags=["tasks"])
app.include_router(tasks.router, prefix="/api/v1", tags=["tasks"])
app.include_router(task_documents.router, prefix="/api/v1", tags=["tasks"])
app.include_router(agents.router, prefix="/api/v1", tags=["agents"])
app.include_router(runs.router, prefix="/api/v1", tags=["runs"])
app.include_router(plans.router, prefix="/api/v1", tags=["plans"])
app.include_router(board.router, prefix="/api/v1", tags=["board"])
app.include_router(ideas_breakdown.router, prefix="/api/v1", tags=["ideas"])
app.include_router(ideas_discussion.router, prefix="/api/v1", tags=["ideas"])
app.include_router(ideas.router, prefix="/api/v1", tags=["ideas"])
app.include_router(idea_templates.router, prefix="/api/v1", tags=["ideas"])
app.include_router(idea_scoring.router, prefix="/api/v1", tags=["ideas"])
app.include_router(adr.router, prefix="/api/v1", tags=["ideas"])
app.include_router(discussions.router, prefix="/api/v1", tags=["discussions"])
app.include_router(events.router, prefix="/api/v1", tags=["events"])
app.include_router(events.task_events_router, prefix="/api/v1", tags=["tasks"])
app.include_router(nightrun.router, prefix="/api/v1", tags=["nightrun"])
app.include_router(scheduled_jobs.router, prefix="/api/v1", tags=["scheduled-jobs"])
app.include_router(memory.router, tags=["memory-gateway"])
app.include_router(observability.router)
app.include_router(insights_router)


@app.get("/api/v1/status", tags=["status"])
def status_alias(agent: str = None, db=Depends(get_session)):
    """调度器与心跳状态别名：复用 board_summary，满足验收中 GET /status 要求。"""
    return _board_summary(agent=agent, db=db)


@app.get("/api/v1/alerts", tags=["alerts"])
def get_alerts():
    """返回当前所有告警状态（active + resolved）"""
    mgr = get_alert_manager()
    if mgr:
        mgr.evaluate()
        return {"alerts": mgr.get_all(), "active_count": len(mgr.get_active())}
    return {"alerts": [], "active_count": 0}


from fastapi.responses import HTMLResponse
from pathlib import Path
import sys
from mio_taskhub.observability.logging_config import log_buffer

@app.get("/dashboard", response_class=HTMLResponse, tags=["dashboard"])
def dashboard():
    """Observatory dashboard"""
    base = getattr(sys, '_MEIPASS', Path(__file__).parent.parent)
    for candidate in [
        Path(base) / "web" / "dist" / "dashboard.html",
        Path(__file__).parent.parent / "web" / "dist" / "dashboard.html",
        Path("web/dist/dashboard.html"),
    ]:
        if candidate.exists():
            return HTMLResponse(content=candidate.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>dashboard.html not found</h1>", status_code=404)


@app.get("/landing", response_class=HTMLResponse, tags=["landing"])
def landing():
    """产品宣传页"""
    base = getattr(sys, '_MEIPASS', Path(__file__).parent.parent)
    for candidate in [
        Path(base) / "web" / "landing" / "index.html",
        Path(__file__).parent.parent / "web" / "landing" / "index.html",
        Path(base) / "web" / "dist" / "landing.html",
        Path(__file__).parent.parent / "web" / "dist" / "landing.html",
        Path("web/landing/index.html"),
        Path("web/dist/landing.html"),
    ]:
        if candidate.exists():
            return HTMLResponse(content=candidate.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Landing page not found</h1>", status_code=404)


from collections import deque
import threading

class LogBuffer:
    """In-memory ring buffer for recent log entries (last 1000)."""
    def __init__(self, maxsize: int = 1000):
        self._buf: deque = deque(maxlen=maxsize)
        self._lock = threading.Lock()

    def add(self, entry: dict):
        with self._lock:
            self._buf.append(entry)

    def query(self, level: str = None, logger: str = None, limit: int = 100) -> list:
        with self._lock:
            items = list(self._buf)
        if level:
            items = [e for e in items if e.get("level") == level.upper()]
        if logger:
            items = [e for e in items if logger in e.get("logger", "")]
        return items[-limit:]


def _install_buffer_handler():
    """Install buffer handler (called after logging setup)."""
    pass


@app.get("/api/v1/logs", tags=["logs"])
def query_logs(level: str = None, logger: str = None, limit: int = 100):
    """查询最近日志（支持按级别/logger 过滤）"""
    return {"logs": log_buffer.query(level=level, logger=logger, limit=limit)}

app.state.auth_token = os.environ.get("MIO_TASKHUB_TOKEN", "")
app.middleware("http")(make_auth_middleware())


def configure_auth(token: str):
    app.state.auth_token = token


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    import secrets
    token = getattr(app.state, "auth_token", "")
    if token:
        auth = ws.headers.get("authorization", "")
        if ws.query_params.get("token") != token and not secrets.compare_digest(auth, f"Bearer {token}"):
            await ws.close(code=4401)
            return
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            await ws.send_json({"type": "pong", "echo": data})
    except Exception:
        pass
    finally:
        ws_manager.disconnect(ws)

@app.get(
    "/healthz",
    tags=["health"],
    summary="Liveness probe",
    description="Returns 200 when the process is alive. Use for k8s liveness probes.",
)
def healthz():
    return {"status": "ok"}


@app.get(
    "/readyz",
    tags=["health"],
    summary="Readiness probe",
    description="Checks SQLite connectivity via SELECT 1. Returns 200 when DB is reachable, 503 with {status:degraded, db:error} otherwise. Use for k8s readiness probes.",
)
def readyz():
    from mio_taskhub.db import check_connection, DB_PATH
    from mio_taskhub.ops.backup import get_backup_status
    result = check_connection()
    status = "ok" if result["ok"] else "degraded"
    backup_info = get_backup_status(DB_PATH)
    return Response(
        content='{"status":"' + status + '","db":"' + ("ok" if result["ok"] else "error") + '","backups":{"count":' + str(backup_info["count"]) + ',"latest":' + ('"' + backup_info["latest"] + '"' if backup_info["latest"] else 'null') + '}}',
        media_type="application/json",
        status_code=200 if result["ok"] else 503,
    )


from mio_taskhub.observability.metrics import render_metrics


@app.get(
    "/metrics",
    tags=["metrics"],
    summary="Prometheus metrics",
    description="Returns Prometheus-format text metrics: task counts by state, event counts by type, agent counts by status, memory gateway call counts, and process uptime.",
)
def metrics():
    from fastapi import Response as _Resp
    from mio_taskhub.memory_store import get_metrics as _mem_metrics
    body = render_metrics()
    # v2: append memory store metrics
    mem = _mem_metrics()
    extra = []
    for tool, count in sorted(mem.get("calls_5m", {}).items()):
        extra.append('taskhub_memory_calls_total{{tool="{}",outcome="ok"}} {}'.format(tool, count))
    for tool, outcome in sorted(mem.get("last_error", {}).items()):
        extra.append('taskhub_memory_calls_total{{tool="{}",outcome="{}"}} 1'.format(tool, outcome))
    if extra:
        body = body.rstrip("\n") + "\n# HELP taskhub_memory_calls_total Memory store call counts\n# TYPE taskhub_memory_calls_total counter\n" + "\n".join(extra) + "\n"
    return _Resp(content=body, media_type="text/plain; version=0.0.4")


def _web_dir() -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "web", "dist")
    return os.path.join(os.path.dirname(__file__), "..", "web", "dist")

WEB_DIR = _web_dir()
if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


@app.middleware("http")
async def no_cache_html(request, call_next):
    resp = await call_next(request)
    if "text/html" in resp.headers.get("content-type", ""):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


def run():
    import argparse
    parser = argparse.ArgumentParser(prog="mio-taskhub")
    parser.add_argument("command", nargs="?", default="serve", help="serve")
    parser.add_argument("--port", type=int, default=48620)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--auth", action="store_true", help="enable Bearer auth")
    parser.add_argument("--token", default=None, help="auth token (default: MIO_TASKHUB_TOKEN env)")
    args = parser.parse_args()

    if args.auth:
        token = get_token(args.token)
        if not token:
            token = generate_token()
            print(f"Token: {token}")
        configure_auth(token)
        os.environ["MIO_TASKHUB_TOKEN"] = token

    uvicorn.run("mio_taskhub.main:app", host=args.host, port=args.port, reload=False)

if __name__ == "__main__":
    run()

