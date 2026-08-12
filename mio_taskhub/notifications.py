from fastapi import WebSocket
from typing import Set
import json


class WSManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def broadcast(self, message: dict):
        data = json.dumps(message)
        dead = set()
        for ws in self.active:
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        self.active -= dead


ws_manager = WSManager()
