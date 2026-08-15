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


def test_broadcast_task_update_publishes():
    sent = []

    class FakeWS:
        async def accept(self):
            pass

        async def send_text(self, data):
            sent.append(_json.loads(data))

    async def _connect():
        await notifications.ws_manager.connect(FakeWS())

    asyncio.run(_connect())
    from mio_taskhub.api.tasks import _broadcast_task_update
    _broadcast_task_update("abc123")
    assert sent and sent[0]["type"] == "task_update" and sent[0]["task_id"] == "abc123"


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
