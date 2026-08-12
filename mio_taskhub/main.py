import os
import sys
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from mio_taskhub.db import init_db
from mio_taskhub.api import tasks, agents, runs, plans
from mio_taskhub.notifications import ws_manager

app = FastAPI(title="mio-taskhub", version="0.1.0")
init_db()

app.include_router(tasks.router, prefix="/api/v1", tags=["tasks"])
app.include_router(agents.router, prefix="/api/v1", tags=["agents"])
app.include_router(runs.router, prefix="/api/v1", tags=["runs"])
app.include_router(plans.router, prefix="/api/v1", tags=["plans"])

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
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

@app.on_event("startup")
def start_scheduler():
    from mio_taskhub.wiring import start_background_jobs
    app.state.background = start_background_jobs()

@app.on_event("shutdown")
def stop_background_jobs():
    jobs = getattr(app.state, "background", None)
    if jobs:
        for job in jobs:
            job.stop()


def run():
    uvicorn.run("mio_taskhub.main:app", host="0.0.0.0", port=8080, reload=False)


if __name__ == "__main__":
    run()
