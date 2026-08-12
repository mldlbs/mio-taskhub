from mio_taskhub.scheduler import Scheduler

def test_scheduler_enqueue_task():
    enqueued = []
    s = Scheduler(get_due_tasks=lambda: [
        {"id": "t1", "schedule_type": "once", "run_at": "2026-01-01T00:00:00Z"}
    ], on_enqueue=lambda tid: enqueued.append(tid))
    s.tick()
    assert "t1" in enqueued
