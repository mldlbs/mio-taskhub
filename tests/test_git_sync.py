from datetime import datetime, timedelta

from sqlmodel import Session, select

from mio_taskhub.db import engine, init_db
from mio_taskhub.ops.git_sync import cleanup_old_outbox_events
from mio_taskhub.models import OutboxEvent, OutboxStatus


def _seed_event(age_days: int, status: OutboxStatus = OutboxStatus.SYNCED):
    with Session(engine) as db:
        event = OutboxEvent(
            event_type="evolve-to-adr",
            aggregate_id="test-idea",
            payload={},
            status=status,
            retry_count=0,
            processed_at=datetime.now() - timedelta(days=age_days),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event.id


def test_cleanup_removes_old_events():
    init_db()
    old_id = _seed_event(31, OutboxStatus.SYNCED)
    _seed_event(31, OutboxStatus.FAILED)
    recent_id = _seed_event(1, OutboxStatus.SYNCED)

    cleanup_old_outbox_events()

    with Session(engine) as db:
        old = db.get(OutboxEvent, old_id)
        assert old is None
        recent = db.get(OutboxEvent, recent_id)
        assert recent is not None


def test_cleanup_preserves_recent_events():
    init_db()
    recent_id = _seed_event(1, OutboxStatus.SYNCED)
    pending_id = _seed_event(1, OutboxStatus.PENDING)

    cleanup_old_outbox_events()

    with Session(engine) as db:
        assert db.get(OutboxEvent, recent_id) is not None
        assert db.get(OutboxEvent, pending_id) is not None


def test_cleanup_preserves_pending_events():
    init_db()
    pending_id = _seed_event(60, OutboxStatus.PENDING)

    cleanup_old_outbox_events()

    with Session(engine) as db:
        assert db.get(OutboxEvent, pending_id) is not None
