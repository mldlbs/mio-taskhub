import asyncio
import json as _json
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub import notifications


def test_ws_connect_and_receive_event():
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data.get("type") == "pong"


def test_broadcast_task_event_publishes():
    sent = []

    class FakeWS:
        async def accept(self):
            pass

        async def send_text(self, data):
            sent.append(_json.loads(data))

    async def _connect():
        await notifications.ws_manager.connect(FakeWS())

    asyncio.run(_connect())
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.events import emit_event, broadcast_for_event
    with Session(engine) as s:
        ev = emit_event(s, type="task_created", entity="task", entity_id="abc123")
        s.add(ev); s.commit()
        broadcast_for_event(ev)
    assert sent and sent[0]["type"] == "task_update"
    assert sent[0]["event"]["entity_id"] == "abc123"


def test_create_task_calls_broadcast(monkeypatch):
    import mio_taskhub.api.tasks as tasks_mod

    calls = []

    def _spy(event):
        calls.append(event.entity_id)

    monkeypatch.setattr(tasks_mod, "broadcast_for_event", _spy)
    client = TestClient(app)
    r = client.post("/api/v1/tasks", json={"title": "WS broadcast"})
    assert r.status_code == 200
    assert r.json()["id"] in calls
