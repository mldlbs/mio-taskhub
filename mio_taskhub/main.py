import os
import sys
import uvicorn
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Response, WebSocket
from fastapi.staticfiles import StaticFiles
from mio_taskhub.auth import generate_token, get_token, make_auth_middleware
from mio_taskhub.db import get_session, init_db
from mio_taskhub.api import tasks, agents, runs, plans, board, ideas, discussions, events, nightrun, memory
from mio_taskhub.api.board import board_summary as _board_summary
from mio_taskhub.logging_config import setup_logging
from mio_taskhub.middleware import RequestIDMiddleware
from mio_taskhub.notifications import ws_manager

setup_logging()


@asynccontextmanager
async def lifespan(app):
    from mio_taskhub.wiring import start_background_jobs
    from mio_taskhub.git_sync import start_git_sync_worker, stop_git_sync_worker
    from mio_taskhub.night_runner import start_night_runner, stop_night_runner
    app.state.background = start_background_jobs()
    start_git_sync_worker()
    start_night_runner()
    yield
    jobs = getattr(app.state, "background", None)
    if jobs:
        for job in jobs:
            job.stop()
    stop_git_sync_worker()
    stop_night_runner()


app = FastAPI(
    title="mio-taskhub",
    version="0.1.0",
    description="Multi-agent R&D dispatch system with state machine, task lifecycle, and real-time notifications.",
    lifespan=lifespan,
)
# v3 UX: gzip 响应压缩（仅 > 1KB 才有收益）
# 注意：GZipMiddleware 必须先 add（在最内层），否则会被 BaseHTTPMiddleware 拦截
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(RequestIDMiddleware)
init_db()

app.include_router(tasks.router, prefix="/api/v1", tags=["tasks"])
app.include_router(agents.router, prefix="/api/v1", tags=["agents"])
app.include_router(runs.router, prefix="/api/v1", tags=["runs"])
app.include_router(plans.router, prefix="/api/v1", tags=["plans"])
app.include_router(board.router, prefix="/api/v1", tags=["board"])
app.include_router(ideas.router, prefix="/api/v1", tags=["ideas"])
app.include_router(discussions.router, prefix="/api/v1", tags=["discussions"])
app.include_router(events.router, prefix="/api/v1", tags=["events"])
app.include_router(nightrun.router, prefix="/api/v1", tags=["nightrun"])
app.include_router(memory.router, tags=["memory-gateway"])


@app.get("/api/v1/status", tags=["status"])
def status_alias(agent: str = None, db=Depends(get_session)):
    """调度器与心跳状态别名：复用 board_summary，满足验收中 GET /status 要求。"""
    return _board_summary(agent=agent, db=db)

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
    from mio_taskhub.db import engine
    from sqlalchemy import text
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass
    status = "ok" if db_ok else "degraded"
    return Response(
        content='{"status":"' + status + '","db":"' + ("ok" if db_ok else "error") + '"}',
        media_type="application/json",
        status_code=200 if db_ok else 503,
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
    from mio_taskhub.memory_gateway import get_metrics as _mem_metrics
    body = render_metrics()
    # v2: append memory gateway metrics
    mem = _mem_metrics()
    extra = []
    for key, count in sorted(mem.get("calls_total", {}).items()):
        tool, outcome = key.rsplit(":", 1)
        extra.append('taskhub_memory_calls_total{{tool="{}",outcome="{}"}} {}'.format(tool, outcome, count))
    if extra:
        body = body.rstrip("\n") + "\n# HELP taskhub_memory_calls_total Memory gateway call counts\n# TYPE taskhub_memory_calls_total counter\n" + "\n".join(extra) + "\n"
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
