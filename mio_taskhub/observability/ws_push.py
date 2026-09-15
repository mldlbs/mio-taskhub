"""WebSocket real-time metrics push.

Periodically pushes metric snapshots and alert updates to connected WebSocket clients.
"""
import asyncio
import json
import logging
import time
import threading
from typing import Optional

logger = logging.getLogger("mio_taskhub.observability.ws_push")

_PUSH_INTERVAL = 5  # seconds
_running = False
_thread: Optional[threading.Thread] = None


def _collect_metrics_snapshot() -> dict:
    """Collect a lightweight metrics snapshot for WS push."""
    from mio_taskhub.observability.metrics import render_metrics
    from mio_taskhub.observability.alerts import get_alert_manager

    lines = render_metrics()
    snap = {"ts": time.time(), "metrics": {}, "alerts": []}

    for line in lines.split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        if "{" in line:
            name = line.split("{")[0]
        else:
            name = line.split(" ")[0]
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            try:
                val = float(parts[1])
                if name not in snap["metrics"]:
                    snap["metrics"][name] = val
                elif isinstance(snap["metrics"][name], list):
                    snap["metrics"][name].append(val)
                else:
                    snap["metrics"][name] = [snap["metrics"][name], val]
            except ValueError:
                pass

    mgr = get_alert_manager()
    if mgr:
        snap["alerts"] = mgr.get_active()

    return snap


def _push_loop():
    global _running
    while _running:
        try:
            from mio_taskhub.events import ws_manager
            if ws_manager.active:
                snap = _collect_metrics_snapshot()
                data = json.dumps({"type": "metrics_snapshot", "data": snap})
                dead = set()
                for ws in list(ws_manager.active):
                    try:
                        asyncio.run(ws.send_text(data))
                    except Exception:
                        dead.add(ws)
                if dead:
                    ws_manager.active -= dead
        except Exception as e:
            logger.debug("WS push error: %s", e)
        time.sleep(_PUSH_INTERVAL)


def start_ws_push():
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_push_loop, daemon=True, name="ws-metrics-push")
    _thread.start()
    logger.info("WS metrics push started (interval=%ds)", _PUSH_INTERVAL)


def stop_ws_push():
    global _running
    _running = False
    logger.info("WS metrics push stopped")
