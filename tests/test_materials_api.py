# -*- coding: utf-8 -*-
"""P3：素材来源健康只读端点。"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app

client = TestClient(app)


def test_sources_health_endpoint():
    r = client.get("/api/v1/materials/sources")
    assert r.status_code == 200
    payload = r.json()
    assert "items" in payload
    keys = {item["key"] for item in payload["items"]}
    assert "hn" in keys
    assert "hub.console" in keys
    hn = next(item for item in payload["items"] if item["key"] == "hn")
    assert hn["mode"] == "active"
    assert "raw_total" in hn
