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
from mio_taskhub.api import tasks, task_stages, task_graph, task_subtasks, templates, agents, runs, plans, board, ideas, idea_templates, idea_scoring, adr, discussions, events, nightrun, memory, scheduled_jobs, task_documents, reviews, ideas_breakdown, ideas_discussion, observability, update, mio_runtime
from mio_taskhub.api.insights import router as insights_router
from mio_taskhub.api.board import board_summary as _board_summary
from mio_taskhub.observability.logging_config import setup_logging
from mio_taskhub.middleware import RequestIDMiddleware, RateLimitMiddleware
from mio_taskhub.events import ws_manager
from mio_taskhub.observability.otel import init_otel, instrument_app, instrument_sqlalchemy, instrument_httpx, shutdown_otel
from mio_taskhub.observability.alerts import init_alert_manager, get_alert_manager
from mio_taskhub.db import engine as db_engine
from mio_taskhub.version import __version__


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

    # 更新：启动残留恢复 + 后台检查线程 + runtime sentinel
    from mio_taskhub.update import runtime as update_runtime
    from mio_taskhub.version import __version__ as _ver, is_frozen as _frozen
    update_runtime.startup_recovery()
    try:
        from mio_taskhub.update.service import get_service
        _svc = get_service()
        _interval = float(os.environ.get("MIO_UPDATE_INTERVAL_H", "6") or 6)
        _svc.start_background(delay=20.0, interval_h=_interval)
        app.state.update_service = _svc
    except Exception as e:  # noqa: BLE001
        logging.getLogger("mio_taskhub.update").warning("update service 启动失败：%s", e)
    if _frozen():
        update_runtime.write_runtime_state(
            version=_ver, port=int(os.environ.get("MIO_TASKHUB_PORT", "48620")))

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
    version=__version__,
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
# 静态路由必须先于动态 {idea_id} 注册，否则 GET /ideas/templates 会被遮蔽成 idea not found
app.include_router(idea_templates.router, prefix="/api/v1", tags=["ideas"])
app.include_router(ideas_breakdown.router, prefix="/api/v1", tags=["ideas"])
app.include_router(ideas_discussion.router, prefix="/api/v1", tags=["ideas"])
app.include_router(ideas.router, prefix="/api/v1", tags=["ideas"])
app.include_router(idea_scoring.router, prefix="/api/v1", tags=["ideas"])
app.include_router(adr.router, prefix="/api/v1", tags=["ideas"])
app.include_router(discussions.router, prefix="/api/v1", tags=["discussions"])
app.include_router(events.router, prefix="/api/v1", tags=["events"])
app.include_router(events.task_events_router, prefix="/api/v1", tags=["tasks"])
app.include_router(nightrun.router, prefix="/api/v1", tags=["nightrun"])
app.include_router(scheduled_jobs.router, prefix="/api/v1", tags=["scheduled-jobs"])
app.include_router(memory.router, tags=["memory-gateway"])
app.include_router(mio_runtime.router, tags=["mio-runtime"])
app.include_router(observability.router)
app.include_router(update.router, prefix="/api/v1", tags=["update"])
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
    return HTMLResponse(content=_DASHBOARD_HTML)


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


# Embedded dashboard HTML (fallback when dashboard.html not found in bundle)
_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mio-TaskHub Observatory</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#070b14;color:#f1f5f9;min-height:100vh}
.hdr{padding:20px 32px;border-bottom:1px solid rgba(148,163,184,0.08);display:flex;align-items:center;justify-content:space-between;backdrop-filter:blur(12px);background:rgba(7,11,20,0.7);position:sticky;top:0;z-index:100}
.hdr h1{font-size:20px;font-weight:700;background:linear-gradient(135deg,#f1f5f9,#94a3b8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hdr .st{display:flex;gap:16px;align-items:center}
.hdr .dot{width:7px;height:7px;border-radius:50%;background:#10b981;animation:p 2s ease-in-out infinite}
@keyframes p{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(16,185,129,0.4)}50%{opacity:.6;box-shadow:0 0 0 6px rgba(16,185,129,0)}}
.hdr .meta{font-size:13px;color:#64748b}
.gr{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:24px 32px}
.cd{background:rgba(30,41,59,0.45);backdrop-filter:blur(20px);border:1px solid rgba(148,163,184,0.08);border-radius:16px;padding:24px;box-shadow:0 4px 24px rgba(0,0,0,0.25);transition:0.25s}
.cd:hover{border-color:rgba(148,163,184,0.12);transform:translateY(-1px)}
.cd .tl{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.2px;color:#64748b;margin-bottom:12px}
.cd .v{font-size:36px;font-weight:800;letter-spacing:-1px;line-height:1;margin-bottom:6px}
.cd .sub{font-size:13px;color:#64748b;font-variant-numeric:tabular-nums}
.bar{height:4px;background:rgba(148,163,184,0.1);border-radius:2px;margin-top:14px;overflow:hidden}
.bf{height:100%;border-radius:2px;transition:width 0.8s cubic-bezier(0.4,0,0.2,1)}
.ch{padding:0 32px 32px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
.cc{background:rgba(30,41,59,0.45);backdrop-filter:blur(20px);border:1px solid rgba(148,163,184,0.08);border-radius:16px;padding:24px;box-shadow:0 4px 24px rgba(0,0,0,0.25)}
.cc h3{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.2px;color:#64748b;margin-bottom:16px}
.al{padding:0 32px 32px}
.alist{display:flex;flex-direction:column;gap:8px}
.alr{padding:14px 18px;border-radius:10px;font-size:13px;display:flex;align-items:center;gap:12px}
.alr.critical{background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.2);color:#fca5a5}
.alr.warning{background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.2);color:#fcd34d}
.alr.info{background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.2);color:#93c5fd}
.alr .bdg{font-size:10px;padding:3px 8px;border-radius:4px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px}
.dt{width:100%;border-collapse:collapse;font-size:13px}
.dt th{text-align:left;color:#64748b;font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.8px;padding:10px 14px;border-bottom:1px solid rgba(148,163,184,0.08)}
.dt td{padding:10px 14px;border-bottom:1px solid rgba(148,163,184,0.04);font-variant-numeric:tabular-nums}
.dt tr:hover td{background:rgba(148,163,184,0.03)}
.dt .ok{color:#10b981}.dt .wrn{color:#f59e0b}.dt .err{color:#ef4444}
.rl{padding:8px 18px;border-radius:10px;border:1px solid rgba(148,163,184,0.08);background:rgba(30,41,59,0.45);backdrop-filter:blur(10px);color:#94a3b8;font-size:13px;font-weight:500;cursor:pointer;transition:0.25s}
.rl:hover{background:rgba(30,41,155,0.65);color:#f1f5f9}
.span2{grid-column:span 2}
@media(max-width:1200px){.gr{grid-template-columns:repeat(2,1fr)}.span2{grid-column:span 2}}
@media(max-width:768px){.gr,.ch{grid-template-columns:1fr;padding:16px}.span2{grid-column:span 1}.cd .v{font-size:28px}}
</style>
</head>
<body>
<div class="hdr"><h1>Mio-TaskHub Observatory</h1><div class="st"><div class="dot" id="dot"></div><span class="meta" id="up">Loading...</span><button class="rl" onclick="load()">Refresh</button></div></div>
<div class="gr" id="cards"></div>
<div class="ch">
<div class="cc"><h3>Tasks by State</h3><canvas id="c1" height="200"></canvas></div>
<div class="cc"><h3>Thread Health</h3><canvas id="c2" height="200"></canvas></div>
<div class="cc"><h3>Dependency Latency</h3><canvas id="c3" height="200"></canvas></div>
<div class="cc"><h3>System Resources</h3><canvas id="c4" height="200"></canvas></div>
<div class="cc span2"><h3>SLO Availability Trend (7 Days)</h3><canvas id="sloChart" height="100"></canvas></div>
<div class="cc"><h3>Recent Insights</h3><div id="insightsFeed" style="max-height:300px;overflow-y:auto"></div></div>
</div>
<div class="al"><div class="tl" style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.2px;color:#64748b;margin-bottom:12px">Active Alerts</div><div class="alist" id="alerts"><div class="alr info"><span>No active alerts</span></div></div></div>
<div class="gr" style="margin-bottom:32px"><div class="cd span2" style="grid-column:1/-1"><div class="tl">Dependency Performance</div><table class="dt" id="dpt"><thead><tr><th>Dep</th><th>Op</th><th>Calls</th><th>Errors</th><th>Avg</th><th>P50</th><th>P90</th><th>P99</th><th>Max</th><th>Err%</th></tr></thead><tbody></tbody></table></div></div>
<script>
var ch={};
var CL={COMPLETED:'#10b981',FAILED:'#ef4444',QUEUED:'#3b82f6',CLAIMED:'#8b5cf6',RUNNING:'#f59e0b',RETRYING:'#f97316',CANCELLED:'#64748b'};
function P(t){var m={tasks:{},threads:{},dep:{},biz:{},sys:{}};for(var l of t.split('\\n')){if(!l||l[0]==='#')continue;var x=l.match(/^([a-z_]+)(?:\\{(.+?)\\})?\\s+(.+)$/);if(!x)continue;var lb={};if(x[2])for(var p of x[2].split(',')){var kv=p.split('=');lb[kv[0]]=kv[1].replace(/"/g,'');}var nv=parseFloat(x[3]);var n=x[1];if(n.startsWith('taskhub_tasks_total'))m.tasks[lb.state||'']=nv;
else if(n.startsWith('taskhub_thread_alive')){m.threads[lb.name]=m.threads[lb.name]||{};m.threads[lb.name].alive=nv;}
else if(n.startsWith('taskhub_thread_heartbeat_age')){m.threads[lb.name]=m.threads[lb.name]||{};m.threads[lb.name].age=nv;}
else if(n.startsWith('taskhub_dep_')){var d=lb.dep||'?';var o=lb.op||'?';m.dep[d]=m.dep[d]||{};m.dep[d][o]=m.dep[d][o]||{};m.dep[d][o][n]=nv;}
else if(n==='taskhub_task_success_rate')m.biz.success=nv;
else if(n==='taskhub_uptime_seconds')m.sys.uptime=nv;
else if(n==='taskhub_process_cpu_percent')m.sys.cpu=nv;
else if(n==='taskhub_process_memory_percent')m.sys.memPct=nv;
else if(n==='taskhub_process_memory_rss_bytes')m.sys.memRss=nv;
else if(n==='taskhub_db_pool_checked_out')m.sys.dbPoolOut=nv;
else if(n==='taskhub_slo_availability_30d')m.sys.sloAvail=nv;
else if(n==='taskhub_slo_error_budget_remaining')m.sys.sloBudget=nv;}return m;}
function fs(s){if(!s)return'-';if(s<60)return s.toFixed(0)+'s';if(s<3600)return(s/60).toFixed(1)+'m';return(s/3600).toFixed(1)+'h';}
function fb(b){if(!b)return'-';if(b<1024)return b.toFixed(0)+'B';if(b<1048576)return(b/1024).toFixed(1)+'KB';return(b/1048576).toFixed(1)+'MB';}
function fm(ms){if(!ms&&ms!==0)return'-';if(ms<1)return'<1ms';if(ms<1000)return ms.toFixed(1)+'ms';return(ms/1000).toFixed(2)+'s';}
function RC(m){var c=document.getElementById('cards');document.getElementById('up').textContent='Uptime '+fs(m.sys.uptime);var total=0;for(var k in m.tasks)total+=m.tasks[k];var sr=m.biz.success!=null?(m.biz.success*100).toFixed(1)+'%':'-';var cpu=m.sys.cpu!=null?m.sys.cpu.toFixed(1)+'%':'-';var mem=m.sys.memPct!=null?m.sys.memPct.toFixed(1)+'%':'-';var slo=m.sys.sloAvail!=null?(m.sys.sloAvail*100).toFixed(2)+'%':'-';c.innerHTML='<div class="cd"><div class="tl">Total Tasks</div><div class="v">'+total+'</div><div class="sub">'+(m.tasks.QUEUED||0)+' queued / '+(m.tasks.COMPLETED||0)+' done / '+(m.tasks.FAILED||0)+' failed</div></div><div class="cd"><div class="tl">Success Rate</div><div class="v" style="color:#10b981">'+sr+'</div><div class="bar"><div class="bf" style="width:'+(m.biz.success!=null?m.biz.success*100:0)+'%;background:#10b981"></div></div></div><div class="cd"><div class="tl">CPU</div><div class="v">'+cpu+'</div><div class="bar"><div class="bf" style="width:'+(m.sys.cpu||0)+'%;background:'+(m.sys.cpu||0)>80?'#ef4444':'#f59e0b'+'"></div></div></div><div class="cd"><div class="tl">Memory</div><div class="v">'+mem+'</div><div class="sub">'+fb(m.sys.memRss)+' RSS</div><div class="bar"><div class="bf" style="width:'+(m.sys.memPct||0)+'%;background:'+(m.sys.memPct||0)>80?'#ef4444':'#8b5cf6'+'"></div></div></div><div class="cd"><div class="tl">DB Pool</div><div class="v">'+(m.sys.dbPoolOut||0)+'<span style="font-size:16px;color:#64748b;font-weight:400"> / 15</span></div></div><div class="cd"><div class="tl">Threads</div><div class="v">'+Object.values(m.threads).filter(function(t){return t.alive}).length+'<span style="font-size:16px;color:#64748b;font-weight:400"> / '+Object.keys(m.threads).length+'</span></div></div><div class="cd"><div class="tl">SLO (30d)</div><div class="v" style="color:'+(m.sys.sloAvail!=null&&m.sys.sloAvail<0.99?'#ef4444':'#10b981')+'">'+slo+'</div><div class="sub">Budget: '+(m.sys.sloBudget!=null?(m.sys.sloBudget*100).toFixed(1)+'%':'-')+'</div></div><div class="cd"><div class="tl">Stalled</div><div class="v">'+Object.entries(m.tasks).filter(function(e){return e[0].indexOf('_stalled')===0}).reduce(function(s,e){return s+e[1]},0)+'</div><div class="sub">Tasks stuck > 300s</div></div>';}
function RCH(m){for(var k in ch)ch[k].destroy();ch={};var tl=Object.keys(m.tasks);ch.c1=new Chart(document.getElementById('c1'),{type:'doughnut',data:{labels:tl,datasets:[{data:tl.map(function(l){return m.tasks[l]}),backgroundColor:tl.map(function(l){return CL[l]||'#64748b'}),borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,cutout:'65%',plugins:{legend:{position:'right',labels:{color:'#94a3b8',padding:12}}}}});var tn=Object.keys(m.threads);ch.c2=new Chart(document.getElementById('c2'),{type:'bar',data:{labels:tn,datasets:[{label:'Heartbeat Age (s)',data:tn.map(function(n){return m.threads[n].age||0}),backgroundColor:tn.map(function(n){return m.threads[n].alive?'rgba(16,185,129,0.6)':'rgba(239,68,68,0.6)'}),borderRadius:4,borderSkipped:false}]},options:{responsive:true,maintainAspectRatio:false,scales:{x:{grid:{display:false},ticks:{color:'#64748b',font:{size:10}}},y:{grid:{color:'rgba(148,163,184,0.06)'},ticks:{color:'#64748b'},beginAtZero:true}},plugins:{legend:{display:false}}}});var dl=[],p50=[],p90=[],p99=[];for(var d in m.dep)for(var o in m.dep[d]){dl.push(d+'.'+o);p50.push(m.dep[d][o].taskhub_dep_latency_p50_ms||0);p90.push(m.dep[d][o].taskhub_dep_latency_p90_ms||0);p99.push(m.dep[d][o].taskhub_dep_latency_p99_ms||0);}ch.c3=new Chart(document.getElementById('c3'),{type:'bar',data:{labels:dl.slice(0,8),datasets:[{label:'P50',data:p50.slice(0,8),backgroundColor:'rgba(59,130,246,0.6)',borderRadius:4,borderSkipped:false},{label:'P90',data:p90.slice(0,8),backgroundColor:'rgba(245,158,11,0.6)',borderRadius:4,borderSkipped:false},{label:'P99',data:p99.slice(0,8),backgroundColor:'rgba(239,68,68,0.6)',borderRadius:4,borderSkipped:false}]},options:{responsive:true,maintainAspectRatio:false,scales:{x:{grid:{display:false},ticks:{color:'#64748b',font:{size:10},maxRotation:45}},y:{grid:{color:'rgba(148,163,184,0.06)'},ticks:{color:'#64748b'},beginAtZero:true}},plugins:{legend:{labels:{color:'#94a3b8'}}}}});ch.c4=new Chart(document.getElementById('c4'),{type:'bar',data:{labels:['CPU %','Memory %'],datasets:[{data:[m.sys.cpu||0,m.sys.memPct||0],backgroundColor:['rgba(59,130,246,0.5)','rgba(139,92,246,0.5)'],borderRadius:6,borderSkipped:false,barThickness:32}]},options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',scales:{x:{grid:{color:'rgba(148,163,184,0.06)'},ticks:{color:'#64748b'},max:100},y:{grid:{display:false},ticks:{color:'#94a3b8',font:{size:13,weight:500}}}},plugins:{legend:{display:false}}}});}
function RDT(m){var tb=document.querySelector('#dpt tbody');var h='';for(var d in m.dep)for(var o in m.dep[d]){var dt=m.dep[d][o];var er=dt.taskhub_dep_error_rate||0;var cls=er===0?'ok':er<0.05?'wrn':'err';h+='<tr><td>'+d+'</td><td>'+o+'</td><td>'+(dt.taskhub_dep_latency_count||0)+'</td><td class="'+cls+'">'+(dt.taskhub_dep_latency_errors||0)+'</td><td>'+fm(dt.taskhub_dep_latency_avg_ms)+'</td><td>'+fm(dt.taskhub_dep_latency_p50_ms)+'</td><td>'+fm(dt.taskhub_dep_latency_p90_ms)+'</td><td>'+fm(dt.taskhub_dep_latency_p99_ms)+'</td><td>'+fm(dt.taskhub_dep_latency_max_ms)+'</td><td class="'+cls+'">'+(er*100).toFixed(2)+'%</td></tr>';}tb.innerHTML=h||'<tr><td colspan="10" style="color:#64748b;text-align:center">No dependency data</td></tr>';}
function renderFromMetrics(t){var m=P(t);RC(m);RCH(m);RDT(m);document.getElementById('dot').style.background='#10b981';}
function load(){fetch('/metrics').then(function(r){return r.text()}).then(renderFromMetrics).catch(function(){document.getElementById('dot').style.background='#ef4444';document.getElementById('up').textContent='Connection error'});fetch('/api/v1/alerts').then(function(r){return r.json()}).then(function(d){var el=document.getElementById('alerts');if(d.active_count===0){el.innerHTML='<div class="alr info"><span>No active alerts</span></div>';return}el.innerHTML=d.alerts.filter(function(a){return a.active}).map(function(a){return '<div class="alr '+a.severity+'"><span class="bdg" style="background:'+a.severity+'===critical?rgba(239,68,68,0.2):rgba(245,158,11,0.2)">'+a.severity+'</span><span>'+a.message+'</span></div>'}).join('')}).catch(function(){})}
var wsReconnectTimer=null;
function connectWS(){var proto=location.protocol==='https:'?'wss:':'ws:';var ws=new WebSocket(proto+'//'+location.host+'/ws');ws.onmessage=function(e){try{var msg=JSON.parse(e.data);if(msg.type==='metrics_snapshot'){var lines=[];var snap=msg.data;for(var name in snap.metrics){var v=snap.metrics[name];if(Array.isArray(v))for(var i=0;i<v.length;i++)lines.push(name+' '+v[i]);else lines.push(name+' '+v);}renderFromMetrics(lines.join('\\n'));if(snap.alerts){var el=document.getElementById('alerts');if(snap.alerts.length===0){el.innerHTML='<div class="alr info"><span>No active alerts</span></div>';}else{el.innerHTML=snap.alerts.map(function(a){return '<div class="alr '+a.severity+'"><span class="bdg" style="background:'+a.severity+'===critical?rgba(239,68,68,0.2):rgba(245,158,11,0.2)">'+a.severity+'</span><span>'+a.message+'</span></div>'}).join('');}}}else if(msg.type==='task_update'||msg.type==='idea_update'){load();}}catch(ex){}};ws.onclose=function(){if(wsReconnectTimer)clearTimeout(wsReconnectTimer);wsReconnectTimer=setTimeout(connectWS,3000);};ws.onerror=function(){ws.close();};}
load();connectWS();
async function loadSloChart(){try{var resp=await fetch('/api/v1/slo/history?hours=168&limit=168');var json=await resp.json();var data=json.snapshots||json;if(!data||!data.length)return;var labels=data.map(function(d){return new Date(d.ts*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})});var availability=data.map(function(d){return(d.availability*100).toFixed(2)});new Chart(document.getElementById('sloChart'),{type:'line',data:{labels:labels,datasets:[{label:'Availability %',data:availability,borderColor:'#10b981',backgroundColor:'rgba(16,185,129,0.06)',fill:true,tension:0.4,pointRadius:2},{label:'Target (99%)',data:Array(labels.length).fill(99),borderColor:'rgba(239,68,68,0.5)',borderDash:[6,4],pointRadius:0,borderWidth:1.5}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#94a3b8'}}},scales:{x:{grid:{display:false},ticks:{color:'#64748b',maxTicksLimit:12}},y:{min:95,max:100,grid:{color:'rgba(148,163,184,0.06)'},ticks:{color:'#64748b'}}}}})}catch(e){}}
async function loadInsightsFeed(){try{var resp=await fetch('/api/v1/insights?limit=10');var insights=await resp.json();var feed=document.getElementById('insightsFeed');if(!feed)return;if(!insights.length){feed.innerHTML='<div style="padding:16px;color:#64748b;text-align:center">No insights yet</div>';return}feed.innerHTML=insights.map(function(i){return '<div style="padding:12px 0;border-bottom:1px solid rgba(148,163,184,0.06)"><span style="display:inline-block;font-size:10px;padding:2px 7px;border-radius:4px;font-weight:700;text-transform:uppercase;margin-right:8px;background:'+(i.severity==='critical'?'rgba(239,68,68,0.12)':i.severity==='warning'?'rgba(245,158,11,0.12)':'rgba(59,130,246,0.12)');color:'+(i.severity==='critical'?'#ef4444':i.severity==='warning'?'#f59e0b':'#3b82f6')+'">'+i.severity+'</span><strong style="font-size:13px">'+i.title+'</strong><div style="font-size:12px;color:#64748b;margin-top:4px">'+i.description+'</div>'+(i.recommendation?'<div style="font-size:11px;color:#10b981;margin-top:6px;padding-left:10px;border-left:2px solid rgba(16,185,129,0.3)">'+i.recommendation+'</div>':'')+'</div>'}).join('')}catch(e){}}
loadSloChart();loadInsightsFeed();setInterval(loadInsightsFeed,30000);
</script>
</body>
</html>"""
