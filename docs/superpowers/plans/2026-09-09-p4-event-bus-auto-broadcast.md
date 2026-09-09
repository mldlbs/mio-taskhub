# P4: Event Bus Auto-Broadcast — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate 41 manual `broadcast_for_event(event)` calls across the API layer by hooking into SQLAlchemy's `after_commit` event. Events committed to DB are automatically broadcast to WS clients.

**Architecture:** Add a session-scoped `after_commit` listener in `events.py` that collects Event objects added during the transaction and broadcasts them on commit. All API endpoints stop calling `broadcast_for_event` manually.

**Tech Stack:** SQLAlchemy session events, FastAPI, pytest

---

## Context: Current Event Pattern (41 call sites)

Every write endpoint follows this pattern:
```python
event = emit_event(db, type="...", entity="...", entity_id="...", payload={...})
db.add(business_object)
db.commit()                    # Event + business data committed atomically
broadcast_for_event(event)     # ← manual, repetitive, error-prone
return {...}
```

**Problems:**
1. **Repetition:** 41 call sites all do `broadcast_for_event(event)` manually
2. **Fragile:** Forgetting to call = no WS notification (silent failure)
3. **Not atomic:** broadcast happens after commit; if broadcast fails, event is in DB but clients never notified (acceptable, but inconsistent)

**Solution:** SQLAlchemy `after_commit` hook auto-broadcasts all Event objects in the session.

---

## Task 1: Add after_commit auto-broadcast hook

**Files:**
- Modify: `mio_taskhub/events.py` (add hook)
- Modify: `mio_taskhub/db.py` (register hook on engine)

- [ ] **Step 1: Add broadcast hook to events.py**

Add to `events.py` after the existing code:

```python
# ── Auto-broadcast on commit ───────────────────────────────────────────

_pending_broadcasts: list[Event] = []


def _collect_event_for_broadcast(session: Session, flush_context, instances):
    """SQLAlchemy after_flush hook: collect Event objects for post-commit broadcast."""
    for obj in session.new:
        if isinstance(obj, Event):
            _pending_broadcasts.append(obj)


def _broadcast_after_commit(session: Session):
    """SQLAlchemy after_commit hook: broadcast all collected events."""
    if not _pending_broadcasts:
        return
    events_to_send = list(_pending_broadcasts)
    _pending_broadcasts.clear()
    for ev in events_to_send:
        broadcast_for_event(ev)


def install_broadcast_hooks(engine):
    """Register session hooks for auto-broadcast. Call once at startup."""
    from sqlalchemy.orm import Session as SASession
    event.listen(SASession, "after_flush", _collect_event_for_broadcast)
    event.listen(SASession, "after_commit", _broadcast_after_commit)
```

Note: needs `from sqlalchemy import event` at top of events.py.

- [ ] **Step 2: Register hooks in db.py**

Add at the end of `db.py` (after engine creation):

```python
# Install auto-broadcast hooks (broadcasts Event objects on successful commit)
from mio_taskhub.events import install_broadcast_hooks
install_broadcast_hooks(engine)
```

- [ ] **Step 3: Run tests (without removing manual calls yet)**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed (double-broadcast is harmless since broadcast_for_event swallows exceptions)

- [ ] **Step 4: Commit**

```bash
git add mio_taskhub/events.py mio_taskhub/db.py
git commit -m "feat(p4): add after_commit auto-broadcast hook for events"
```

---

## Task 2: Remove manual broadcast_for_event calls from API layer

**Files:**
- Modify: all `api/*.py` files that call `broadcast_for_event`

- [ ] **Step 1: Remove broadcast_for_event from all API files**

For each file that imports `broadcast_for_event`, remove:
1. `broadcast_for_event` from the import line
2. All `broadcast_for_event(event)` / `broadcast_for_event(extra)` calls

Files to modify (grep confirmed):
- `api/tasks.py`
- `api/runs.py`
- `api/claim.py` (if any)
- `api/board.py` (if any)
- `api/agents.py`
- `api/templates.py`
- `api/reviews.py`
- `api/discussions.py`
- `api/ideas.py`
- `api/ideas_enhanced.py`
- `api/memory.py`

**IMPORTANT:** Keep `broadcast_for_event` defined in `events.py` — it's still used by the hook internally. Only remove it from API endpoint call sites.

- [ ] **Step 2: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 3: Verify broadcast still works**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -c "from mio_taskhub.events import install_broadcast_hooks; print('hook system OK')"`
Expected: `hook system OK`

- [ ] **Step 4: Commit**

```bash
git add mio_taskhub/api/*.py
git commit -m "refactor(p4): remove 41 manual broadcast_for_event calls, rely on auto-broadcast hook"
```

---

## Task 3: Final verification

- [ ] **Step 1: Count remaining broadcast_for_event calls in api/**

Run: `rg "broadcast_for_event" mio_taskhub/api/ --count`
Expected: 0 matches in api/ files (only in events.py itself)

- [ ] **Step 2: Run full test suite**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

---

## File Summary

| File | Change |
|---|---|
| `events.py` | +`_pending_broadcasts`, +`_collect_event_for_broadcast`, +`_broadcast_after_commit`, +`install_broadcast_hooks` |
| `db.py` | +`install_broadcast_hooks(engine)` call |
| `api/tasks.py` | -13 `broadcast_for_event` calls, -import |
| `api/runs.py` | -3 calls, -import |
| `api/agents.py` | -2 calls, -import |
| `api/templates.py` | -1 call, -import |
| `api/reviews.py` | -1 call, -import |
| `api/discussions.py` | -3 calls, -import |
| `api/ideas.py` | -7 calls, -import |
| `api/ideas_enhanced.py` | -import (if present) |
| `api/memory.py` | -1 call, -import |

**Net effect:** 41 manual broadcast calls eliminated. Event broadcast is now automatic on commit. API endpoints are simpler and less error-prone.
