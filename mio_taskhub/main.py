import os
import sys
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Response, WebSocket
from fastapi.staticfiles import StaticFiles
import secrets
from fastapi.responses import JSONResponse
from mio_taskhub.db import get_session, init_db
from mio_taskhub.api import tasks, task_stages, task_graph, task_subtasks, templates, agents, runs, plans, board, ideas, idea_templates, idea_scoring, adr, discussions, events, nightrun, memory, scheduled_jobs, task_documents, reviews, ideas_breakdown, ideas_discussion
from mio_taskhub.api.board import board_summary as _board_summary
from mio_taskhub.logging_config import setup_logging
from mio_taskhub.middleware import RequestIDMiddleware, RateLimitMiddleware
from mio_taskhub.events import ws_manager
from mio_taskhub.otel import init_otel, instrument_app, instrument_sqlalchemy, instrument_httpx, shutdown_otel
from mio_taskhub.alerts import init_alert_manager, get_alert_manager
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
    from mio_taskhub.git_sync import start_git_sync_worker, stop_git_sync_worker
    from mio_taskhub.night_runner import start_night_runner, stop_night_runner
    from mio_taskhub.cron_engine import start_cron_engine, stop_cron_engine
    app.state.background = start_background_jobs()
    start_git_sync_worker()
    start_night_runner()
    cron_engine = start_cron_engine()
    # SQLite auto-backup (hourly snapshots, 31 kept)
    from mio_taskhub.backup import SQLiteBackup
    from mio_taskhub.db import DB_PATH
    backup = SQLiteBackup(DB_PATH)
    backup.start()
    register_thread("backup", backup._thread, backup)
    app.state.backup = backup

    # Initialize alert manager
    alert_mgr = init_alert_manager()
    app.state.alert_manager = alert_mgr

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
    version="0.2.0",
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
from mio_taskhub.logging_config import log_buffer

@app.get("/dashboard", response_class=HTMLResponse, tags=["dashboard"])
def dashboard():
    """自包含仪表盘：CPU/内存/任务/线程/依赖延迟/告警"""
    # Try external file first, fallback to embedded
    base = getattr(sys, '_MEIPASS', Path(__file__).parent.parent)
    for candidate in [
        Path(base) / "web" / "dist" / "dashboard.html",
        Path(__file__).parent.parent / "web" / "dist" / "dashboard.html",
        Path("web/dist/dashboard.html"),
    ]:
        if candidate.exists():
            return HTMLResponse(content=candidate.read_text(encoding="utf-8"))
    # Embedded fallback
    return HTMLResponse(content="<h1>Landing page not found</h1>", status_code=404)


@app.get("/landing", response_class=HTMLResponse, tags=["landing"])
def landing():
    """产品宣传页"""
    base = getattr(sys, '_MEIPASS', Path(__file__).parent.parent)
    for candidate in [
        Path(base) / "web" / "dist" / "landing.html",
        Path(__file__).parent.parent / "web" / "dist" / "landing.html",
        Path("web/dist/landing.html"),
    ]:
        if candidate.exists():
            return HTMLResponse(content=candidate.read_text(encoding="utf-8"))
    return HTMLResponse(content=_LANDING_HTML)


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
    from mio_taskhub.backup import get_backup_status
    result = check_connection()
    status = "ok" if result["ok"] else "degraded"
    backup_info = get_backup_status(DB_PATH)
    return Response(
        content='{"status":"' + status + '","db":"' + ("ok" if result["ok"] else "error") + '","backups":{"count":' + str(backup_info["count"]) + ',"latest":' + ('"' + backup_info["latest"] + '"' if backup_info["latest"] else 'null') + '}}',
        media_type="application/json",
        status_code=200 if result["ok"] else 503,
    )


from mio_taskhub.metrics import render_metrics


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
<title>mio-taskhub Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.hdr{background:linear-gradient(135deg,#1e293b,#334155);padding:20px 32px;border-bottom:1px solid #475569;display:flex;align-items:center;justify-content:space-between}
.hdr h1{font-size:20px;font-weight:600;color:#f8fafc}
.hdr .st{display:flex;gap:16px;align-items:center}
.hdr .dot{width:8px;height:8px;border-radius:50%;background:#22c55e;animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.4}}
.hdr .meta{font-size:13px;color:#94a3b8}
.gr{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;padding:24px 32px}
.cd{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px}
.cd h3{font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:#94a3b8;margin-bottom:12px}
.cd .v{font-size:32px;font-weight:700;color:#f8fafc}
.cd .u{font-size:14px;color:#64748b;margin-left:4px}
.cd .sub{font-size:12px;color:#64748b;margin-top:4px}
.cd .bar{height:6px;background:#334155;border-radius:3px;margin-top:8px;overflow:hidden}
.cd .bf{height:100%;border-radius:3px}
.ch{padding:0 32px 32px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
.cc{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px}
.cc h3{font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:#94a3b8;margin-bottom:12px}
.al{padding:0 32px 32px}
.alist{display:flex;flex-direction:column;gap:8px}
.alr{padding:12px 16px;border-radius:8px;font-size:13px;display:flex;align-items:center;gap:10px}
.alr.critical{background:#450a0a;border:1px solid #dc2626;color:#fca5a5}
.alr.warning{background:#422006;border:1px solid #d97706;color:#fcd34d}
.alr.info{background:#0c4a6e;border:1px solid #0284c7;color:#7dd3fc}
.alr .bdg{font-size:11px;padding:2px 8px;border-radius:4px;font-weight:600;text-transform:uppercase}
.dt{width:100%;border-collapse:collapse;font-size:13px}
.dt th{text-align:left;color:#94a3b8;font-weight:500;padding:8px 12px;border-bottom:1px solid #334155}
.dt td{padding:8px 12px;border-bottom:1px solid #1e293b}
.dt tr:hover td{background:#1e293b}
.dt .ok{color:#22c55e}.dt .wrn{color:#eab308}.dt .err{color:#ef4444}
.rl{background:#3b82f6;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px}
@media(max-width:900px){.ch{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="hdr"><h1>mio-taskhub Dashboard</h1><div class="st"><div class="dot" id="dot"></div><span class="meta" id="up">Loading...</span><button class="rl" onclick="load()">Refresh</button></div></div>
<div class="gr" id="cards"></div>
<div class="ch">
<div class="cc"><h3>Tasks by State</h3><canvas id="c1" height="200"></canvas></div>
<div class="cc"><h3>Thread Health</h3><canvas id="c2" height="200"></canvas></div>
<div class="cc"><h3>Dependency Latency</h3><canvas id="c3" height="200"></canvas></div>
<div class="cc"><h3>System Resources</h3><canvas id="c4" height="200"></canvas></div>
</div>
<div class="al"><h3 style="font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:#94a3b8;margin-bottom:12px">Active Alerts</h3><div class="alist" id="alerts"><div class="alr info"><span>No active alerts</span></div></div></div>
<div class="gr" style="margin-bottom:32px"><div class="cd" style="grid-column:1/-1"><h3>Dependency Performance</h3><table class="dt" id="dpt"><thead><tr><th>Dep</th><th>Op</th><th>Calls</th><th>Errors</th><th>Avg</th><th>P50</th><th>P90</th><th>P99</th><th>Max</th><th>Err%</th></tr></thead><tbody></tbody></table></div></div>
<script>
var ch={};
var CL={COMPLETED:'#22c55e',FAILED:'#ef4444',QUEUED:'#3b82f6',CLAIMED:'#8b5cf6',RUNNING:'#f59e0b',RETRYING:'#f97316',CANCELLED:'#6b7280'};
function P(t){var m={tasks:{},http:{},threads:{},dep:{},biz:{},sys:{}};for(var l of t.split('\\n')){if(!l||l[0]==='#')continue;var x=l.match(/^([a-z_]+)(?:\\{(.+?)\\})?\\s+(.+)$/);if(!x)continue;var lb={};if(x[2])for(var p of x[2].split(',')){var kv=p.split('=');lb[kv[0]]=kv[1].replace(/"/g,'');}var nv=parseFloat(x[3]);var n=x[1];if(n.startsWith('taskhub_tasks_total'))m.tasks[lb.state||'']=nv;
else if(n.startsWith('taskhub_http_request_count_total')){m.http.rc=m.http.rc||{};m.http.rc[lb.label||'total']=nv;}
else if(n.startsWith('taskhub_thread_alive')){m.threads[lb.name]=m.threads[lb.name]||{};m.threads[lb.name].alive=nv;}
else if(n.startsWith('taskhub_thread_heartbeat_age')){m.threads[lb.name]=m.threads[lb.name]||{};m.threads[lb.name].age=nv;}
else if(n.startsWith('taskhub_dep_')){var d=lb.dep||'?';var o=lb.op||'?';m.dep[d]=m.dep[d]||{};m.dep[d][o]=m.dep[d][o]||{};m.dep[d][o][n]=nv;}
else if(n==='taskhub_task_success_rate')m.biz.success=nv;
else if(n==='taskhub_uptime_seconds')m.sys.uptime=nv;
else if(n==='taskhub_process_cpu_percent')m.sys.cpu=nv;
else if(n==='taskhub_process_memory_percent')m.sys.memPct=nv;
else if(n==='taskhub_process_memory_rss_bytes')m.sys.memRss=nv;
else if(n==='taskhub_db_pool_checked_out')m.sys.dbPoolOut=nv;}return m;}
function fs(s){if(!s)return'-';if(s<60)return s.toFixed(0)+'s';if(s<3600)return(s/60).toFixed(1)+'m';return(s/3600).toFixed(1)+'h';}
function fb(b){if(!b)return'-';if(b<1024)return b.toFixed(0)+'B';if(b<1048576)return(b/1024).toFixed(1)+'KB';return(b/1048576).toFixed(1)+'MB';}
function fm(ms){if(!ms&&ms!==0)return'-';if(ms<1)return'<1ms';if(ms<1000)return ms.toFixed(1)+'ms';return(ms/1000).toFixed(2)+'s';}
function RC(m){var c=document.getElementById('cards');document.getElementById('up').textContent='Uptime: '+fs(m.sys.uptime);var total=0;for(var k in m.tasks)total+=m.tasks[k];var sr=m.biz.success!=null?(m.biz.success*100).toFixed(1)+'%':'-';var cpu=m.sys.cpu!=null?m.sys.cpu.toFixed(1)+'%':'-';var mem=m.sys.memPct!=null?m.sys.memPct.toFixed(1)+'%':'-';c.innerHTML='<div class="cd"><h3>Total Tasks</h3><div class="v">'+total+'</div><div class="sub">'+(m.tasks.QUEUED||0)+' queued / '+(m.tasks.COMPLETED||0)+' completed / '+(m.tasks.FAILED||0)+' failed</div></div><div class="cd"><h3>Success Rate</h3><div class="v">'+sr+'</div><div class="bar"><div class="bf" style="width:'+(m.biz.success!=null?m.biz.success*100:0)+'%;background:#22c55e"></div></div></div><div class="cd"><h3>CPU</h3><div class="v">'+cpu+'</div><div class="bar"><div class="bf" style="width:'+(m.sys.cpu||0)+'%;background:#3b82f6"></div></div></div><div class="cd"><h3>Memory</h3><div class="v">'+mem+'</div><div class="bar"><div class="bf" style="width:'+(m.sys.memPct||0)+'%;background:#8b5cf6"></div></div></div><div class="cd"><h3>DB Pool</h3><div class="v">'+(m.sys.dbPoolOut||0)+'<span class="u">/ 15</span></div></div><div class="cd"><h3>Threads</h3><div class="v">'+Object.values(m.threads).filter(t=>t.alive).length+'<span class="u">/ '+Object.keys(m.threads).length+'</span></div></div>';}
function RCH(m){for(var k in ch)ch[k].destroy();ch={};var tl=Object.keys(m.tasks);var td=tl.map(l=>m.tasks[l]);ch.c1=new Chart(document.getElementById('c1'),{type:'doughnut',data:{labels:tl,datasets:[{data:td,backgroundColor:tl.map(l=>CL[l]||'#6b7280')}]},options:{responsive:true,plugins:{legend:{position:'right',labels:{color:'#94a3b8'}}}}});var tn=Object.keys(m.threads);ch.c2=new Chart(document.getElementById('c2'),{type:'bar',data:{labels:tn,datasets:[{label:'Heartbeat Age (s)',data:tn.map(n=>m.threads[n].age||0),backgroundColor:tn.map(n=>m.threads[n].alive?'#22c55e':'#ef4444')}]},options:{responsive:true,scales:{x:{ticks:{color:'#94a3b8'}},y:{ticks:{color:'#94a3b8'},beginAtZero:true}},plugins:{legend:{display:false}}}});var dl=[];var p5=[];var p90=[];var p99=[];for(var d in m.dep)for(var o in m.dep[d]){dl.push(d+'.'+o);p5.push(m.dep[d][o].taskhub_dep_latency_p50_ms||0);p90.push(m.dep[d][o].taskhub_dep_latency_p90_ms||0);p99.push(m.dep[d][o].taskhub_dep_latency_p99_ms||0);}ch.c3=new Chart(document.getElementById('c3'),{type:'bar',data:{labels:dl.slice(0,10),datasets:[{label:'P50',data:p5.slice(0,10),backgroundColor:'#3b82f6'},{label:'P90',data:p90.slice(0,10),backgroundColor:'#f59e0b'},{label:'P99',data:p99.slice(0,10),backgroundColor:'#ef4444'}]},options:{responsive:true,scales:{x:{ticks:{color:'#94a3b8',maxRotation:45}},y:{ticks:{color:'#94a3b8'},beginAtZero:true}},plugins:{legend:{labels:{color:'#94a3b8'}}}}});ch.c4=new Chart(document.getElementById('c4'),{type:'bar',data:{labels:['CPU %','Memory %'],datasets:[{data:[m.sys.cpu||0,m.sys.memPct||0],backgroundColor:['#3b82f6','#8b5cf6']}]},options:{responsive:true,indexAxis:'y',scales:{x:{ticks:{color:'#94a3b8'},max:100},y:{ticks:{color:'#94a3b8'}}},plugins:{legend:{display:false}}}});}
function RDT(m){var tb=document.querySelector('#dpt tbody');var h='';for(var d in m.dep)for(var o in m.dep[d]){var dt=m.dep[d][o];var er=dt.taskhub_dep_error_rate||0;var cls=er===0?'ok':er<0.05?'wrn':'err';h+='<tr><td>'+d+'</td><td>'+o+'</td><td>'+(dt.taskhub_dep_latency_count||0)+'</td><td class="'+cls+'">'+(dt.taskhub_dep_latency_errors||0)+'</td><td>'+fm(dt.taskhub_dep_latency_avg_ms)+'</td><td>'+fm(dt.taskhub_dep_latency_p50_ms)+'</td><td>'+fm(dt.taskhub_dep_latency_p90_ms)+'</td><td>'+fm(dt.taskhub_dep_latency_p99_ms)+'</td><td>'+fm(dt.taskhub_dep_latency_max_ms)+'</td><td class="'+cls+'">'+(er*100).toFixed(2)+'%</td></tr>';}tb.innerHTML=h||'<tr><td colspan="10" style="color:#64748b;text-align:center">No dependency data</td></tr>';}
function load(){fetch('/metrics').then(r=>r.text()).then(t=>{var m=P(t);RC(m);RCH(m);RDT(m);document.getElementById('dot').style.background='#22c55e'}).catch(()=>{document.getElementById('dot').style.background='#ef4444';document.getElementById('up').textContent='Connection error'});fetch('/api/v1/alerts').then(r=>r.json()).then(d=>{var el=document.getElementById('alerts');if(d.active_count===0){el.innerHTML='<div class="alr info"><span>No active alerts</span></div>';return}el.innerHTML=d.alerts.filter(a=>a.active).map(a=>'<div class="alr '+a.severity+'"><span class="bdg">'+a.severity+'</span><span>'+a.message+'</span></div>').join('')}).catch(()=>{})}
load();setInterval(load,30000);
</script>
</body>
</html>"""


# Embedded landing page HTML
_LANDING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mio-TaskHub - 跨 Agent 任务协调中心</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0a0e1a;--card:#111827;--border:#1e293b;--accent:#3b82f6;--accent2:#8b5cf6;--green:#22c55e;--text:#e2e8f0;--dim:#64748b}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans SC',sans-serif;background:var(--bg);color:var(--text);line-height:1.6;overflow-x:hidden}
a{color:var(--accent);text-decoration:none}
.container{max-width:1200px;margin:0 auto;padding:0 24px}
.hero{min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;position:relative;overflow:hidden}
.hero::before{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle at 30% 50%,rgba(59,130,246,0.08) 0%,transparent 50%),radial-gradient(circle at 70% 50%,rgba(139,92,246,0.06) 0%,transparent 50%);animation:drift 20s ease-in-out infinite}
@keyframes drift{0%,100%{transform:translate(0,0)}50%{transform:translate(-2%,2%)}}
.hero-content{position:relative;z-index:1}
.hero h1{font-size:clamp(48px,8vw,80px);font-weight:800;background:linear-gradient(135deg,#3b82f6,#8b5cf6,#ec4899);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:16px;letter-spacing:-2px}
.hero .tagline{font-size:clamp(18px,3vw,28px);color:var(--dim);margin-bottom:40px;font-weight:300}
.hero .badges{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin-bottom:40px}
.badge{padding:6px 16px;border-radius:20px;font-size:13px;font-weight:500;border:1px solid var(--border);background:rgba(255,255,255,0.03)}
.badge.green{border-color:rgba(34,197,94,0.3);color:var(--green)}
.badge.blue{border-color:rgba(59,130,246,0.3);color:var(--accent)}
.badge.purple{border-color:rgba(139,92,246,0.3);color:var(--accent2)}
.hero .cta{display:flex;gap:16px;justify-content:center;flex-wrap:wrap}
.btn{padding:14px 32px;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer;border:none;transition:all 0.2s}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 8px 30px rgba(59,130,246,0.3)}
.btn-outline{background:transparent;color:var(--text);border:1px solid var(--border)}
.btn-outline:hover{border-color:var(--accent);background:rgba(59,130,246,0.05)}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:24px;margin:80px 0}
.stat{text-align:center;padding:32px 16px;background:var(--card);border:1px solid var(--border);border-radius:16px}
.stat .num{font-size:42px;font-weight:800;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.stat .label{color:var(--dim);font-size:14px;margin-top:8px}
@media(max-width:768px){.stats{grid-template-columns:repeat(2,1fr)}}
section{padding:100px 0}
.section-title{text-align:center;margin-bottom:60px}
.section-title h2{font-size:clamp(28px,5vw,42px);font-weight:700;margin-bottom:12px}
.section-title p{color:var(--dim);font-size:18px;max-width:600px;margin:0 auto}
.features{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
@media(max-width:900px){.features{grid-template-columns:repeat(2,1fr)}}
@media(max-width:600px){.features{grid-template-columns:1fr}}
.feature{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:32px;transition:all 0.3s}
.feature:hover{border-color:var(--accent);transform:translateY(-4px);box-shadow:0 0 40px rgba(59,130,246,0.15)}
.feature .icon{font-size:32px;margin-bottom:16px}
.feature h3{font-size:18px;font-weight:600;margin-bottom:8px}
.feature p{color:var(--dim);font-size:14px;line-height:1.7}
.arch{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:48px;margin:0 auto;max-width:900px}
.arch-diagram{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;margin-top:32px}
.arch-layer{text-align:center;padding:24px 16px;border-radius:12px;border:1px solid var(--border)}
.arch-layer h4{font-size:14px;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;color:var(--accent)}
.arch-layer .items{display:flex;flex-direction:column;gap:8px}
.arch-layer .item{padding:8px 12px;background:rgba(255,255,255,0.03);border-radius:8px;font-size:13px;color:var(--dim)}
@media(max-width:768px){.arch-diagram{grid-template-columns:1fr}}
.code-block{background:#0d1117;border:1px solid var(--border);border-radius:12px;padding:24px;font-family:'Fira Code','Cascadia Code',monospace;font-size:14px;overflow-x:auto;margin:24px 0;line-height:1.8}
.code-block .comment{color:#6a737d}
.code-block .keyword{color:#ff7b72}
.code-block .string{color:#a5d6ff}
.code-block .func{color:#d2a8ff}
.metrics-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
@media(max-width:768px){.metrics-grid{grid-template-columns:1fr}}
.metric-card{background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:12px;padding:20px}
.metric-card h4{font-size:13px;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px}
.metric-card code{font-size:12px;color:var(--green);word-break:break-all}
footer{border-top:1px solid var(--border);padding:40px 0;text-align:center;color:var(--dim);font-size:14px}
footer .links{display:flex;gap:24px;justify-content:center;margin-bottom:16px}
</style>
</head>
<body>
<section class="hero"><div class="hero-content"><h1>Mio-TaskHub</h1><p class="tagline">跨 Agent 任务协调中心 · 多 Agent 研发流水线</p><div class="badges"><span class="badge green">v0.2.0</span><span class="badge blue">FastAPI + SQLite</span><span class="badge purple">MCP Protocol</span><span class="badge">PyInstaller EXE</span><span class="badge green">可观测性全覆盖</span></div><div class="cta"><a href="https://github.com/mldlbs/mio-taskhub" class="btn btn-primary">GitHub</a><a href="#features" class="btn btn-outline">功能特性</a><a href="#quickstart" class="btn btn-outline">快速开始</a></div></div></section>
<div class="container"><div class="stats"><div class="stat"><div class="num">507</div><div class="label">测试用例</div></div><div class="stat"><div class="num">44</div><div class="label">API 端点</div></div><div class="stat"><div class="num">24</div><div class="label">告警规则</div></div><div class="stat"><div class="num">80+</div><div class="label">架构评分</div></div></div></div>
<section id="features"><div class="container"><div class="section-title"><h2>功能特性</h2><p>为多 Agent 协作场景设计的全栈任务协调方案</p></div><div class="features"><div class="feature"><div class="icon">🎯</div><h3>任务状态机</h3><p>7 阶段研发流水线：brainstorming → design → planning → ready → implementing → review → done。支持依赖、重试、优先级、截止时间。</p></div><div class="feature"><div class="icon">🤖</div><h3>多 Agent 调度</h3><p>Agent 注册 → 领取 → 心跳 → 提交。自动分配空闲 agent，超时任务自动重试，支持 agent 类型匹配。</p></div><div class="feature"><div class="icon">🔌</div><h3>MCP 协议集成</h3><p>原生 MCP Server，任何支持 MCP 的 Agent（Claude Code、OpenCode、Codex、Hermes）可直接调用 44+ 工具。</p></div><div class="feature"><div class="icon">📊</div><h3>实时仪表盘</h3><p>自包含 HTML 仪表盘，Chart.js 可视化任务分布、线程健康、依赖延迟。无需 Grafana，开箱即用。</p></div><div class="feature"><div class="icon">🔔</div><h3>内建告警</h3><p>线程死亡/停滞、HTTP 高错误率、任务卡死、依赖异常——24 条告警规则，支持 Prometheus + Alertmanager。</p></div><div class="feature"><div class="icon">📈</div><h3>全链路可观测</h3><p>OpenTelemetry 追踪、Prometheus 指标、结构化日志。HTTP → DB → Git → MCP 全链路可视。</p></div><div class="feature"><div class="icon">💡</div><h3>想法 → 任务</h3><p>想法记录 → 发酵讨论 → 拆解为任务。支持评审、ADR 投影到 Git、版本历史追踪。</p></div><div class="feature"><div class="icon">🔒</div><h3>安全与备份</h3><p>Bearer Token 认证、SQLite WAL 模式、自动备份（每小时快照，保留 31 份）、请求限流。</p></div><div class="feature"><div class="icon">📦</div><h3>单文件部署</h3><p>PyInstaller 打包为单个 EXE，Windows 托盘模式运行。无需 Docker、无需 Node.js、无需数据库服务。</p></div></div></div></section>
<section id="arch"><div class="container"><div class="section-title"><h2>系统架构</h2><p>分层设计，模块化扩展</p></div><div class="arch"><div class="arch-diagram"><div class="arch-layer"><h4>接入层</h4><div class="items"><div class="item">REST API (44+ endpoints)</div><div class="item">MCP Server (44+ tools)</div><div class="item">WebSocket 实时推送</div><div class="item">HTML 仪表盘</div></div></div><div class="arch-layer"><h4>业务层</h4><div class="items"><div class="item">任务状态机引擎</div><div class="item">Agent 调度器</div><div class="item">想法评审系统</div><div class="item">定时任务 (Cron)</div></div></div><div class="arch-layer"><h4>基础设施</h4><div class="items"><div class="item">SQLite (WAL + QueuePool)</div><div class="item">OpenTelemetry</div><div class="item">Prometheus 指标</div><div class="item">结构化日志</div></div></div></div></div></div></section>
<section id="quickstart"><div class="container"><div class="section-title"><h2>快速开始</h2><p>3 行命令启动服务</p></div><div class="code-block"><span class="comment"># 1. 克隆仓库</span><br><span class="keyword">git clone</span> https://github.com/mldlbs/mio-taskhub.git<br><span class="keyword">cd</span> mio-taskhub<br><br><span class="comment"># 2. 安装依赖</span><br><span class="keyword">pip install</span> -e .<br><br><span class="comment"># 3. 启动服务</span><br><span class="keyword">python -m</span> <span class="func">mio_taskhub.main</span> serve --port <span class="string">48620</span><br><br><span class="comment"># 或直接运行 EXE</span><br><span class="keyword">.\\mio-taskhub.exe</span> hub</div><div class="code-block"><span class="comment"># 连接 MCP（在 opencode / claude-code 配置中）</span><br><span class="string">"mcpServers": {</span><br>&nbsp;&nbsp;<span class="string">"mio-taskhub": {</span><br>&nbsp;&nbsp;&nbsp;&nbsp;<span class="string">"command": "npx",</span><br>&nbsp;&nbsp;&nbsp;&nbsp;<span class="string">"args": ["mcp-remote", "http://127.0.0.1:48620/mcp"]</span><br>&nbsp;&nbsp;<span class="string">}</span><br><span class="string">}</span></div></div></section>
<section id="observability"><div class="container"><div class="section-title"><h2>可观测性</h2><p>P0-P3 完整覆盖，从零到生产级</p></div><div class="metrics-grid"><div class="metric-card"><h4>系统指标 (USE)</h4><code>taskhub_process_cpu_percent · memory_rss_bytes · threads · db_pool_utilization</code></div><div class="metric-card"><h4>业务指标</h4><code>taskhub_task_success_rate · throughput_24h · retries_avg · completion_p50_seconds</code></div><div class="metric-card"><h4>依赖延迟</h4><code>taskhub_dep_latency{dep="sqlite|git|mcp"} · p50/p90/p99 · error_rate</code></div><div class="metric-card"><h4>SLO 合规</h4><code>taskhub_slo_availability_30d · error_budget_remaining · latency_breach</code></div><div class="metric-card"><h4>线程健康</h4><code>taskhub_thread_alive · heartbeat_age_seconds · consecutive_failures</code></div><div class="metric-card"><h4>HTTP 监控</h4><code>taskhub_http_request_count · active_requests · errors_total · duration_ms</code></div><div class="metric-card"><h4>告警系统</h4><code>GET /api/v1/alerts · 24 Prometheus rules · Alertmanager</code></div><div class="metric-card"><h4>日志追踪</h4><code>GET /api/v1/logs · trace_id correlation · structured JSON</code></div></div></div></section>
<footer><div class="container"><div class="links"><a href="https://github.com/mldlbs/mio-taskhub">GitHub</a><a href="https://github.com/mldlbs/mio-taskhub/blob/main/CHANGELOG.md">Changelog</a><a href="https://github.com/mldlbs/mio-taskhub/blob/main/docs/SLO-SLI.md">SLO/SLI</a><a href="http://127.0.0.1:48620/dashboard">Dashboard</a></div><p>Mio-TaskHub v0.2.0 · Built with FastAPI + SQLite + React</p><p style="margin-top:8px;color:#475569">Multi-agent task coordination for AI-powered development</p></div></footer>
</body>
</html>"""
