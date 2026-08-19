# tests/test_idea_review_scanner.py
from sqlmodel import Session, select
from mio_taskhub.db import engine
from mio_taskhub.models import Idea, Task, TaskKind
from mio_taskhub import idea_review


def _seed_idea(**kw):
    title = kw.pop("title", "评审我")
    created_at = kw.pop("created_at", None)
    with Session(engine) as s:
        i = Idea(title=title)
        if created_at is not None:
            i.created_at = created_at
        s.add(i); s.commit(); s.refresh(i)
        return i.id


def _task_kind_for(idea_id):
    with Session(engine) as s:
        t = s.exec(select(Task).where(Task.idea_id == idea_id,
                                      Task.task_kind == TaskKind.REVIEW)).first()
        return t


def test_due_empty_when_none_match():
    iid = _seed_idea()
    assert idea_review._due_idea_ids() == []        # 默认冷却未到


def test_due_after_initial_delay(monkeypatch):
    monkeypatch.setattr(idea_review, "INITIAL_DELAY_MIN", 0)
    iid = _seed_idea()
    assert idea_review._due_idea_ids() == [iid]


def test_due_skips_inflight(monkeypatch):
    monkeypatch.setattr(idea_review, "INITIAL_DELAY_MIN", 0)
    iid = _seed_idea()
    idea_review._enqueue(iid)
    assert idea_review._due_idea_ids() == []        # 已有在途评审任务


def test_enqueue_creates_review_task():
    iid = _seed_idea(title="自动评审")
    idea_review._enqueue(iid)
    t = _task_kind_for(iid)
    assert t is not None
    assert t.task_kind == TaskKind.REVIEW
    assert t.target_agent_type == "idea-reviewer"
    assert t.title == "「自动评审」想法评审"
    assert t.stage.value == "ready"


def test_due_after_interval(monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(idea_review, "INITIAL_DELAY_MIN", 0)
    monkeypatch.setattr(idea_review, "INTERVAL_MIN", 1440)
    iid = _seed_idea(created_at=datetime.now(timezone.utc) - timedelta(days=2))
    # 模拟评审过：设 last_reviewed_at 为 1 小时前（间隔未到 → 不 due）
    with Session(engine) as s:
        i = s.get(Idea, iid)
        i.last_reviewed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.add(i); s.commit()
    assert idea_review._due_idea_ids() == []
    # 距上次已超间隔 → due
    with Session(engine) as s:
        i = s.get(Idea, iid)
        i.last_reviewed_at = datetime.now(timezone.utc) - timedelta(days=2)
        s.add(i); s.commit()
    assert idea_review._due_idea_ids() == [iid]


def test_scanner_get_due_uses_enabled(monkeypatch):
    monkeypatch.setattr(idea_review, "ENABLED", False)
    assert idea_review.IdeaReviewScanner()._get_due() == []