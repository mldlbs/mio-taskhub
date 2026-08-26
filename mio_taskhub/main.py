import os
import sys
import uvicorn
from fastapi import Depends, FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from mio_taskhub.auth import generate_token, get_token, make_auth_middleware
from mio_taskhub.db import get_session, init_db
from mio_taskhub.api import tasks, agents, runs, plans, board, ideas, discussions, events, nightrun
from mio_taskhub.api.board import board_summary as _board_summary
from mio_taskhub.notifications import ws_manager

app = FastAPI(title="mio-taskhub", version="0.1.0")
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

@app.on_event("startup")
def start_scheduler():
    from mio_taskhub.wiring import start_background_jobs
    app.state.background = start_background_jobs()

@app.on_event("startup")
def start_git_sync():
    from mio_taskhub.git_sync import start_git_sync_worker
    start_git_sync_worker()

@app.on_event("startup")
def start_night_runner():
    from mio_taskhub.night_runner import start_night_runner
    start_night_runner()

@app.on_event("shutdown")
def stop_background_jobs():
    jobs = getattr(app.state, "background", None)
    if jobs:
        for job in jobs:
            job.stop()

@app.on_event("shutdown")
def stop_git_sync():
    from mio_taskhub.git_sync import stop_git_sync_worker
    stop_git_sync_worker()

@app.on_event("shutdown")
def stop_night_runner():
    from mio_taskhub.night_runner import stop_night_runner
    stop_night_runner()


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
