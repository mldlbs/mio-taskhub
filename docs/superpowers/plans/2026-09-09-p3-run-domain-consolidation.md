# P3: Run Domain Consolidation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all Run lifecycle operations into `api/runs.py` as the single source of truth, eliminating cross-domain coupling where `claim.py` directly creates/queries Run objects.

**Architecture:** Extract Run creation and query functions from `claim.py` into `runs.py`. `claim.py` imports from `runs.py` instead of directly instantiating `Run` model objects. Net effect: cleaner domain boundaries, runs.py becomes the Run API + lifecycle module.

**Tech Stack:** FastAPI (APIRouter), SQLModel/SQLAlchemy (ORM), pytest

---

## Context: Current Run Logic Distribution

| Location | What | Lines |
|---|---|---|
| `api/runs.py` | heartbeat, submit_result endpoints + retry/success/failure helpers | 186 |
| `api/claim.py` | `find_existing_run`, `atomic_claim` (creates Run), `claim_for` | 133 |
| `api/board.py` | Queries active Runs for display | 208 (shared) |
| `api/tasks.py` | `claim_task` endpoint (calls claim_for), lists Runs in task detail | 713 (shared) |

**Problem:** `claim.py` directly imports `Run`, `RunState` and creates `Run(...)` objects. This couples claim logic to the Run data model. All Run operations should flow through `runs.py`.

---

## Task 1: Extract Run lifecycle functions from claim.py to runs.py

**Files:**
- Modify: `mio_taskhub/api/runs.py` (add functions)
- Modify: `mio_taskhub/api/claim.py` (remove Run-related code, import from runs.py)

- [ ] **Step 1: Add Run lifecycle functions to runs.py**

Add these functions to the TOP of `runs.py` (before the existing endpoints):

```python
# ── Run lifecycle ──────────────────────────────────────────────────────

def find_existing_run(db, agent):
    """Idempotent: return existing claimed/running run for agent, or None."""
    return db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()


def create_run(db, agent, task):
    """Create a new Run for the claimed task. Returns the Run object."""
    from mio_taskhub.utils import _now
    run = Run(
        id=str(__import__('uuid').uuid4())[:8],
        task_id=task.id,
        agent_name=agent,
        state=RunState.CLAIMED,
        attempt=task.attempt,
        started_at=_now(),
        last_heartbeat=_now(),
    )
    db.add(run)
    return run
```

- [ ] **Step 2: Update claim.py imports**

Change claim.py imports from:
```python
from mio_taskhub.models import Task, TaskState, TaskStage, Run, RunState, Agent
```
to:
```python
from mio_taskhub.models import Task, TaskState, TaskStage, Agent
from mio_taskhub.api.runs import find_existing_run, create_run
```

- [ ] **Step 3: Update claim.py functions**

In `claim.py`:
- Remove `find_existing_run` function (now in runs.py)
- In `claim_for`: change `find_existing_run(db, agent)` → `find_existing_run(db, agent)` (same name, now imported from runs.py)
- In `atomic_claim`: replace the `Run(...)` construction + `db.add(run)` with `run = create_run(db, agent, task)` + `return run`

Remove the `Run` and `RunState` imports from claim.py (no longer needed directly).

- [ ] **Step 4: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/runs.py mio_taskhub/api/claim.py
git commit -m "refactor(p3): consolidate Run lifecycle in runs.py, decouple from claim.py"
```

---

## Task 2: Verify no direct Run model usage outside runs.py + claim.py

- [ ] **Step 1: Audit Run imports across api/ layer**

Grep for `from mio_taskhub.models import.*Run` across all `api/*.py` files.

Expected matches:
- `runs.py` — ✅ owns Run domain
- `claim.py` — still imports Run indirectly via `create_run` (or not at all if fully decoupled)
- `board.py` — queries Runs for display (acceptable, read-only)
- `tasks.py` — `claim_task` endpoint gets Run from claim_for (acceptable)

If any other api file directly creates `Run(...)` objects, flag it.

- [ ] **Step 2: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

---

## Task 3: Final line count + full verification

- [ ] **Step 1: Count lines**

Run:
```powershell
"runs.py: $((Get-Content 'mio_taskhub/api/runs.py').Count)"
"claim.py: $((Get-Content 'mio_taskhub/api/claim.py').Count)"
```

Expected: runs.py ~210 lines (was 186 + ~25 for new functions), claim.py ~115 lines (was 133 - ~18 removed)

- [ ] **Step 2: Run full test suite**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 3: Verify imports**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -c "from mio_taskhub.api.runs import find_existing_run, create_run; from mio_taskhub.api.claim import claim_for; print('all imports OK')"`
Expected: `all imports OK`

---

## File Summary

| File | Lines (before → after) | Change |
|---|---|---|
| `api/runs.py` | 186 → ~210 | +`find_existing_run`, +`create_run` |
| `api/claim.py` | 133 → ~115 | -`find_existing_run`, -Run construction, +imports from runs.py |

**Net effect:** Run lifecycle consolidated in runs.py. claim.py no longer directly creates or queries Run objects — it delegates to runs.py. Clean domain boundary.
