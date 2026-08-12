from fastapi.testclient import TestClient
from mio_taskhub.main import app


def test_ws_connect_and_receive_event():
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data.get("type") == "pong"
