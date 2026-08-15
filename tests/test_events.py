# tests/test_events.py
import asyncio
import json
import pytest
from sqlmodel import Session, select
from mio_taskhub.db import engine
from mio_taskhub.models import Event
from mio_taskhub.events import emit_event, event_to_dict, broadcast_for_event


def test_emit_event_returns_object_with_seq():
    with Session(engine) as s:
        e = emit_event(s, type="task_created", entity="task", entity_id="t1",
                       payload={"title": "x"})
        assert isinstance(e, Event)
        s.add(e)                       # emit_event 不 commit，由调用方提交
        s.commit()
        assert e.id is not None        # seq 在提交后赋值
        assert e.id >= 1


def test_event_payload_roundtrip():
    with Session(engine) as s:
        e = emit_event(s, type="heartbeat", entity="run", entity_id="r1",
                       run_id="r1", payload={"progress": 50})
        s.add(e); s.commit()
        got = s.get(Event, e.id)
        d = event_to_dict(got)
        assert d["payload"]["progress"] == 50
        assert d["entity"] == "run" and d["entity_id"] == "r1"
        assert d["run_id"] == "r1"


def test_seq_monotonic():
    with Session(engine) as s:
        a = emit_event(s, type="a", entity="task", entity_id="1")
        b = emit_event(s, type="b", entity="task", entity_id="2")
        s.add_all([a, b]); s.commit()
        assert b.id > a.id            # 在 session 块内断言，避免 DetachedInstanceError


def test_emit_event_no_commit():
    with Session(engine) as s:
        emit_event(s, type="x", entity="task", entity_id="1")
        # 未 commit 时表中无行（禁 autoflush，避免 select 触发隐式 flush）
        with s.no_autoflush:
            rows = s.exec(select(Event)).all()
        assert rows == []


def test_event_to_dict_no_payload():
    with Session(engine) as s:
        e = emit_event(s, type="x", entity="idea", entity_id="i1")
        s.add(e); s.commit()
        d = event_to_dict(s.get(Event, e.id))
        assert d["payload"] is None
        assert d["seq"] == e.id
