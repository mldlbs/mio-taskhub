# -*- coding: utf-8 -*-
"""T0.6 / F17：task_cancelled 事件 payload 必须非空且含 reason 枚举 + 可选 detail。

历史缺陷：cancel_task 的 emit_event 从未传 payload → 生产库 145 条事件 payload 全 null。
"""
import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Event

client = TestClient(app)


def _create(title="cancel-reason-probe"):
    r = client.post("/api/v1/tasks", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _latest_cancel_payload(task_id):
    with Session(engine) as s:
        ev = s.exec(
            select(Event).where(Event.type == "task_cancelled",
                                 Event.entity_id == task_id).order_by(Event.id.desc())
        ).first()
        if ev is None:
            return None
        return json.loads(ev.payload) if ev.payload else None


def test_cancel_default_reason_payload():
    tid = _create()
    r = client.delete(f"/api/v1/tasks/{tid}?confirm=true")
    assert r.status_code == 200, r.text
    assert r.json()["reason"] == "manual"
    payload = _latest_cancel_payload(tid)
    assert payload is not None, "payload must not be null"
    assert payload["reason"] == "manual"
    assert "detail" not in payload


def test_cancel_with_reason_and_detail():
    tid = _create()
    r = client.delete(f"/api/v1/tasks/{tid}", params={
        "confirm": "true", "reason": "duplicate", "detail": "与 23e5e280 重复建卡",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reason"] == "duplicate"
    payload = _latest_cancel_payload(tid)
    assert payload["reason"] == "duplicate"
    assert payload["detail"] == "与 23e5e280 重复建卡"


def test_cancel_invalid_reason_rejected():
    tid = _create()
    r = client.delete(f"/api/v1/tasks/{tid}?confirm=true&reason=bogus")
    assert r.status_code == 400
    assert "invalid reason" in r.json()["detail"]
    # 非法 reason 不产生事件（400 先于 emit）
    payload = _latest_cancel_payload(tid)
    assert payload is None


def test_cancel_all_enum_values_accepted():
    for reason in ("manual", "duplicate", "obsolete", "superseded", "policy", "other"):
        tid = _create(title=f"cancel-enum-{reason}")
        r = client.delete(f"/api/v1/tasks/{tid}?confirm=true&reason={reason}")
        assert r.status_code == 200, (reason, r.text)
        assert _latest_cancel_payload(tid)["reason"] == reason
