# tests/test_update_ws.py
"""更新 WS 事件：broadcast_json 与 service.on_event 接线。"""


def test_broadcast_json_records_via_ws_manager(monkeypatch):
    from mio_taskhub import events
    sent = []
    async def fake_broadcast(msg):
        sent.append(msg)
    monkeypatch.setattr(events.ws_manager, "broadcast", fake_broadcast)
    events.broadcast_json({"type": "update_status", "kind": "update_available"})
    assert sent and sent[0]["type"] == "update_status"


def test_broadcast_json_never_raises(monkeypatch):
    from mio_taskhub import events
    async def boom(msg):
        raise RuntimeError("no clients")
    monkeypatch.setattr(events.ws_manager, "broadcast", boom)
    events.broadcast_json({"type": "update_status"})   # 不得抛出


def test_get_service_wires_on_event(monkeypatch):
    from mio_taskhub.update import service as svc_mod
    monkeypatch.setattr(svc_mod, "_SERVICE", None)
    monkeypatch.setenv("MIO_UPDATE_DISABLED", "1")
    s = svc_mod.get_service()
    assert s._on_event is not None
    monkeypatch.setattr(svc_mod, "_SERVICE", None)


def test_update_event_bridge_publishes(monkeypatch):
    from mio_taskhub.update import service as svc_mod
    from mio_taskhub import events
    sent = []
    async def fake_broadcast(msg):
        sent.append(msg)
    monkeypatch.setattr(events.ws_manager, "broadcast", fake_broadcast)
    svc_mod._on_update_event({"kind": "update_available", "status": {"latest": "0.4.0"}})
    assert sent and sent[0]["type"] == "update_status"
    assert sent[0]["status"]["latest"] == "0.4.0"
