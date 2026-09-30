# -*- coding: utf-8 -*-
"""事件一致性模型测试（task c32a2145 / P1-3）。

验证「事务内持久化 + 提交后尽力广播」语义：
① 提交 → 事件落库 + 广播一次；② 回滚 → 不落库不广播；③ 广播失败 → 不影响 commit；
④ 逐事件隔离（一个失败不影响其余）；⑤ 广播路径不会把异常冒泡出 commit()。
"""
import uuid

from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.models import Event, Task
import mio_taskhub.events as EV


def _task(title="evt"):
    return Task(id=str(uuid.uuid4())[:8], title=title)


def test_commit_persists_and_broadcasts_once(monkeypatch):
    sent = []
    monkeypatch.setattr(EV, "broadcast_for_event", lambda e: sent.append(e.id))
    with Session(engine) as db:
        EV.emit_event(db, type="unit_ok", entity="task", entity_id="x")
        db.add(_task())
        db.commit()
        n = len(db.exec(select(Event).where(Event.type == "unit_ok")).all())
    assert n == 1
    assert len(sent) == 1


def test_rollback_persists_nothing(monkeypatch):
    sent = []
    monkeypatch.setattr(EV, "broadcast_for_event", lambda e: sent.append(e.id))
    try:
        with Session(engine) as db:
            EV.emit_event(db, type="unit_rb", entity="task", entity_id="y")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with Session(engine) as db:
        assert len(db.exec(select(Event).where(Event.type == "unit_rb")).all()) == 0
    assert sent == []


def test_broadcast_failure_does_not_affect_commit(monkeypatch):
    def boom(e):
        raise RuntimeError("ws down")
    monkeypatch.setattr(EV, "broadcast_for_event", boom)
    with Session(engine) as db:
        EV.emit_event(db, type="unit_boom", entity="task", entity_id="z")
        db.add(_task("after-boom"))
        db.commit()  # 不得抛异常
        saved = len(db.exec(select(Task).where(Task.title == "after-boom")).all())
        evs = len(db.exec(select(Event).where(Event.type == "unit_boom")).all())
    assert saved == 1
    assert evs == 1


def test_broadcast_per_event_isolation(monkeypatch):
    """一个事件广播失败，不影响同批其余事件的广播。"""
    sent = []

    def flaky(e):
        if e.entity_id == "bad":
            raise RuntimeError("boom")
        sent.append(e.entity_id)

    monkeypatch.setattr(EV, "broadcast_for_event", flaky)
    with Session(engine) as db:
        EV.emit_event(db, type="unit_iso", entity="task", entity_id="bad")
        EV.emit_event(db, type="unit_iso", entity="task", entity_id="good")
        db.commit()  # 不得抛异常
    assert "good" in sent, "前半事件广播失败不应阻断后续事件"


def test_real_broadcast_never_raises_out_of_commit(monkeypatch):
    """即使底层 ws 抛异常，真实 broadcast_for_event 也不让 commit 抛出。"""
    async def boom(self, msg):
        raise RuntimeError("ws down")
    monkeypatch.setattr(EV.ws_manager, "broadcast", boom.__get__(EV.ws_manager, type(EV.ws_manager)))
    with Session(engine) as db:
        EV.emit_event(db, type="unit_real", entity="task", entity_id="q")
        db.add(_task("real"))
        db.commit()  # 不得抛异常
        assert len(db.exec(select(Event).where(Event.type == "unit_real")).all()) == 1


def test_malformed_payload_does_not_break_commit(monkeypatch):
    """payload 非法 JSON 也不应让 commit 抛出（广播路径已吞异常）。"""
    async def noop(self, msg):
        return None
    monkeypatch.setattr(EV.ws_manager, "broadcast", noop.__get__(EV.ws_manager, type(EV.ws_manager)))
    with Session(engine) as db:
        db.add(Event(type="unit_bad", entity="task", entity_id="bad", payload="{not json"))
        db.add(_task("malformed"))
        db.commit()
        assert len(db.exec(select(Task).where(Task.title == "malformed")).all()) == 1
