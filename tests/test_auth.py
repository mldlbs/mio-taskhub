import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from mio_taskhub.main import app

client = TestClient(app)


@pytest.fixture()
def auth_on():
    app.state.auth_token = "test-secret"
    yield
    app.state.auth_token = ""


def test_auth_disabled_by_default():
    assert app.state.auth_token == ""
    r = client.get("/api/v1/tasks")
    assert r.status_code == 200


def test_api_requires_auth(auth_on):
    r = client.get("/api/v1/tasks")
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthorized"}


def test_api_accepts_token(auth_on):
    r = client.get("/api/v1/tasks", headers={"Authorization": "Bearer test-secret"})
    assert r.status_code == 200


def test_api_rejects_bad_token(auth_on):
    r = client.get("/api/v1/tasks", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_web_static_not_protected(auth_on):
    r = client.get("/")
    assert r.status_code in (200, 404)
    assert r.status_code != 401


def test_docs_not_protected(auth_on):
    r = client.get("/docs")
    assert r.status_code == 200


def test_ws_requires_token(auth_on):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()


def test_ws_accepts_query_token(auth_on):
    with client.websocket_connect("/ws?token=test-secret") as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data.get("type") == "pong"


def test_ws_accepts_auth_header(auth_on):
    with client.websocket_connect("/ws", headers={"Authorization": "Bearer test-secret"}) as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data.get("type") == "pong"
