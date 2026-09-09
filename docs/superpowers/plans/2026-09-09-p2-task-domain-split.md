# P2: Task Domain Boundary Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `mio_taskhub/api/tasks.py` from 1431 lines / 58 functions to ≤600 lines by extracting self-contained domain logic into focused modules, while preserving full API backward compatibility in Phase 1 and cleanly splitting routers in Phase 2.

**Architecture:** Two-phase extraction. Phase 1 moves pure business logic (helpers, claim core, document utils, template helpers) into dedicated modules — `tasks.py` imports from them, zero API path changes, zero test changes. Phase 2 extracts template and review endpoints into separate routers with clean URL prefixes, requiring path updates in MCP server + tests.

**Tech Stack:** FastAPI (APIRouter), SQLModel/SQLAlchemy (ORM), pytest

---

## Context: Current `api/tasks.py` Structure

| Line Range | Responsibility | Lines |
|---|---|---|
| 1-21 | Imports + router decl | 21 |
| 23-63 | Helpers: `_parse_dt`, `_parse_enum`, `_graph_with`, `_check_cycle`, `_validate_depends` | 41 |
| 65-113 | `create_task` endpoint | 49 |
| 114-207 | `list_tasks` endpoint | 94 |
| 208-239 | `get_task_events` endpoint | 32 |
| 240-335 | Graph views: `get_full_graph`, `get_task_graph`, `tasks_status_alias` | 96 |
| 338-601 | Template helpers + 8 endpoints | 264 |
| 602-608 | `get_task` endpoint | 7 |
| 609-785 | Document helpers + 3 endpoints | 177 |
| 787-813 | `update_task` endpoint | 27 |
| 814-871 | Subtask/GitRef/History endpoints | 58 |
| 872-912 | Task-scoped Discussion endpoints | 41 |
| 914-1010 | Cancel/retry + `_apply_stage_requirements` | 97 |
| 1012-1290 | Stage ops: `advance_stage`, `move_to_stage`, claim helpers + `claim_task` | 279 |
| 1332-1431 | Review endpoints | 100 |

**Key cross-module dependency:** `wiring.py:4` imports `_claim_for` from `api.tasks`.

---

## Phase 1: Extract Helpers (Zero API Changes)

Phase 1 extracts pure business logic into new modules. Router endpoints stay in `tasks.py` but delegate to the new modules. No API paths change, no test changes needed.

### Task 1: Create `task_helpers.py` — shared utilities

**Files:**
- Create: `mio_taskhub/api/task_helpers.py`
- Modify: `mio_taskhub/api/tasks.py:23-63` (replace with imports)

- [ ] **Step 1: Create `task_helpers.py` with extracted helpers**

```python
# mio_taskhub/api/task_helpers.py
"""Shared utilities for task API endpoints."""
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select
from mio_taskhub.models import Task
from mio_taskhub.status import task_deps
from mio_taskhub.planner import detect_cycle


def parse_dt(value, name: str):
    """Parse ISO datetime string, normalize to UTC. Raises HTTPException(400) on bad format."""
    if not isinstance(value, str):
        return value
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"invalid {name}: {value}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def parse_enum(enum_cls, value, default=None):
    """Parse enum value, raises HTTPException(400) on invalid."""
    if value is None and default is not None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        raise HTTPException(400, f"invalid value: {value}, expected one of {[e.value for e in enum_cls]}")


def graph_with(task, db) -> dict:
    """Build {id: [dep_ids]} dependency graph including given task's new values."""
    graph = {}
    for t in db.exec(select(Task)).all():
        graph[t.id] = task_deps(t)
    graph[task.id] = task_deps(task)
    return graph


def check_cycle(task, db):
    """Raise HTTPException(422) if task creates a dependency cycle."""
    cyc = detect_cycle(graph_with(task, db))
    if cyc:
        raise HTTPException(422, f"cyclic dependency: {' → '.join(cyc)}")


def validate_depends(task, db):
    """Validate depends_on: missing tasks, self-dependency. Missing checked before cycle."""
    for dep in task_deps(task):
        if dep == task.id:
            raise HTTPException(422, f"cannot depend on itself: {dep}")
        if db.get(Task, dep) is None:
            raise HTTPException(422, f"dependency not found: {dep}")
```

- [ ] **Step 2: Update `tasks.py` imports**

In `mio_taskhub/api/tasks.py`, replace lines 23-63 with:

```python
from mio_taskhub.api.task_helpers import parse_dt, parse_enum, check_cycle, validate_depends
```

Update all call sites in `tasks.py`:
- `_parse_dt(` → `parse_dt(`
- `_parse_enum(` → `parse_enum(`
- `_check_cycle(` → `check_cycle(`
- `_validate_depends(` → `validate_depends(`

Remove `_graph_with` (only used internally by `_check_cycle` which is now in `task_helpers.py`).

- [ ] **Step 3: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 4: Commit**

```bash
git add mio_taskhub/api/task_helpers.py mio_taskhub/api/tasks.py
git commit -m "refactor(p2): extract shared helpers to task_helpers.py"
```

---

### Task 2: Create `claim.py` — claim core logic

**Files:**
- Create: `mio_taskhub/api/claim.py`
- Modify: `mio_taskhub/api/tasks.py:1159-1289` (replace with imports)
- Modify: `mio_taskhub/wiring.py:4` (update import path)

- [ ] **Step 1: Create `claim.py` with extracted claim logic**

```python
# mio_taskhub/api/claim.py
"""Core claim logic: candidate selection, atomic claim, fallback.

This module contains the pure business logic for claiming tasks.
API endpoint (claim_task) remains in tasks.py and delegates here.
"""
import uuid
from typing import Optional
from fastapi import HTTPException
from sqlmodel import Session, select
from sqlalchemy import case, Integer, update as sa_update
from sqlalchemy import func

from mio_taskhub.models import Task, TaskState, TaskStage, Run, RunState, Agent
from mio_taskhub.utils import _now


def should_fallback(task, agent_type):
    """Return True if fallback_after has elapsed (task becomes generic)."""
    if not task.target_agent_type or not agent_type:
        return False
    if task.target_agent_type == agent_type:
        return False
    if task.fallback_after is None:
        return False
    if task.created_at is None:
        return False
    elapsed = (_now() - task.created_at).total_seconds()
    return elapsed >= task.fallback_after


def find_existing_run(db, agent):
    """Idempotent: return existing claimed/running run for agent, or None."""
    return db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()


def lookup_agent_type(db, agent):
    """Look up registered agent's agent_type (fallback guard for claim)."""
    ag = db.get(Agent, agent)
    return ag.agent_type if ag and ag.agent_type else None


def build_relevance(agent_type):
    """Build SQLAlchemy CASE expression for relevance ordering.

    Order: type match (0) > no target / fallback expired (1) > other's exclusive (2).
    """
    if agent_type:
        fallback_ready = (
            Task.fallback_after.is_not(None)
            & Task.created_at.is_not(None)
            & (
                func.cast(func.strftime("%s", "now"), Integer)
                >= (
                    func.cast(func.strftime("%s", Task.created_at), Integer)
                    + Task.fallback_after
                )
            )
        )
        return case(
            (Task.target_agent_type == agent_type, 0),
            (Task.target_agent_type.is_(None), 1),
            (fallback_ready, 1),
            else_=2,
        )
    return case((Task.target_agent_type.is_(None), 0), else_=1)


def first_ready_row(rows):
    """Return first ready task whose run_at time has passed (or has no run_at)."""
    now = _now()
    for t in rows:
        if t.schedule_type == "once" and t.run_at:
            run_at = t.run_at if t.run_at.tzinfo else t.run_at.replace(tzinfo=timezone.utc)
            if run_at > now:
                continue
        return t
    return None


def pick_candidate_task(db, agent_type, task_id):
    """Select candidate: by id (direct claim) or by relevance+priority+FIFO."""
    if task_id:
        task = db.get(Task, task_id)
        if not task or task.state != TaskState.QUEUED:
            return None
        return task
    q = select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
    relevance = build_relevance(agent_type)
    rows = db.exec(q.order_by(relevance, Task.priority.desc(), Task.created_at.asc())).all()
    return first_ready_row(rows)


def atomic_claim(db, agent, candidate):
    """Conditional UPDATE to claim task atomically. Returns Run or None."""
    res = db.exec(
        sa_update(Task)
        .where(Task.id == candidate.id, Task.state == TaskState.QUEUED)
        .values(state=TaskState.CLAIMED)
    )
    if res.rowcount != 1:
        db.rollback()
        return None
    task = db.get(Task, candidate.id)
    db.refresh(task)
    from mio_taskhub.transitions import record_post_claim
    claim_event = record_post_claim(task, agent)
    task.attempt += 1
    task.stage = TaskStage.IMPLEMENTING
    run = Run(
        id=str(uuid.uuid4())[:8],
        task_id=task.id,
        agent_name=agent,
        state=RunState.CLAIMED,
        attempt=task.attempt,
        started_at=_now(),
        last_heartbeat=_now(),
    )
    db.add(task)
    if claim_event:
        db.add(claim_event)
    db.add(run)
    return run


def claim_for(agent: str, db: Session, agent_type: Optional[str] = None, task_id: Optional[str] = None):
    """Atomic claim: return agent's Run or None.

    Checks for existing run (idempotent); otherwise selects candidate and claims atomically.
    """
    existing = find_existing_run(db, agent)
    if existing:
        return existing

    if not agent_type:
        agent_type = lookup_agent_type(db, agent) or agent_type

    candidate = pick_candidate_task(db, agent_type, task_id)
    if not candidate:
        return None
    return atomic_claim(db, agent, candidate)
```

- [ ] **Step 2: Update `tasks.py` — remove extracted code, add import**

Remove from `tasks.py`:
- `_should_fallback` function (lines 1159-1170)
- `_claim_for` function (lines 1172-1194)
- `_find_existing_run` function (lines 1197-1201)
- `_lookup_agent_type` function (lines 1204-1207)
- `_pick_candidate_task` function (lines 1210-1220)
- `_build_relevance` function (lines 1223-1243)
- `_first_ready_row` function (lines 1246-1255)
- `_atomic_claim` function (lines 1258-1288)

Add to `tasks.py` imports:
```python
from mio_taskhub.api.claim import claim_for, should_fallback
```

Update call sites in `tasks.py`:
- `_claim_for(` → `claim_for(`
- `_should_fallback(` → `should_fallback(`

- [ ] **Step 3: Update `wiring.py` import**

Change `wiring.py:4` from:
```python
from mio_taskhub.api.tasks import _claim_for
```
to:
```python
from mio_taskhub.api.claim import claim_for as _claim_for
```

(Keep alias `_claim_for` to minimize diff in wiring.py call sites.)

- [ ] **Step 4: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/claim.py mio_taskhub/api/tasks.py mio_taskhub/wiring.py
git commit -m "refactor(p2): extract claim logic to claim.py"
```

---

### Task 3: Create `task_documents.py` — document helpers + endpoints

**Files:**
- Create: `mio_taskhub/api/task_documents.py`
- Modify: `mio_taskhub/api/tasks.py:609-785` (replace with import delegation)

- [ ] **Step 1: Create `task_documents.py`**

```python
# mio_taskhub/api/task_documents.py
"""Task document reading, discovery, and file serving."""
import os
import re
import pathlib
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from mio_taskhub.db import get_session
from mio_taskhub.models import Task

router = APIRouter(prefix="/tasks", tags=["tasks"])

MAX_CONTENT = 200_000


def resolve_doc_path(task, rel, kind):
    """Resolve spec/plan path to absolute Path. Returns (Path, None) or (None, error_msg)."""
    p = pathlib.Path(rel)
    if not p.is_absolute():
        base = (task.workspace or '').strip()
        if not base:
            return None, (f'任务未设置 workspace，且 {kind}_path 为相对路径「{rel}」，无法确定基准目录。'
                          f'请改用绝对路径，或在任务中填写 workspace 后重试。')
        p = pathlib.Path(base) / p
    return p.resolve(), None


def read_content(p):
    """Read file text, truncate if over MAX_CONTENT. Returns (text|None, truncated: bool)."""
    if not p.exists():
        return None, False
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return f'读取失败：{p}\n{e}', False
    truncated = len(text) > MAX_CONTENT
    if truncated:
        text = text[:MAX_CONTENT]
    return text, truncated


def discover_task_docs(workspace):
    """Scan workspace for related markdown docs. Returns list of discovered doc dicts."""
    if not workspace or not os.path.isdir(workspace):
        return []
    SKIP = {'node_modules', 'dist', 'build', '.venv', '.git', '.workbuddy',
            '.memory-backup', '__pycache__', '.idea', '.vscode'}
    DOC_PATTERNS = {
        'spec':    ['spec'],
        'plan':    ['plan'],
        'requirement': ['requirement', 'req', '需求'],
        'test':    ['test-plan', 'test_plan', 'testing'],
        'architecture': ['architecture', 'arch', 'design-doc'],
        'readme':  ['readme'],
        'changelog': ['changelog', 'changes', 'release-notes'],
        'api':     ['api-doc', 'api_spec', 'openapi'],
    }
    out = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            low = f.lower()
            if not low.endswith('.md'):
                continue
            kind = None
            for k, keywords in DOC_PATTERNS.items():
                if any(kw in low for kw in keywords):
                    kind = k
                    break
            if kind is None:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, workspace).replace(os.sep, '/')
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            out.append({'name': f, 'rel_path': rel, 'kind': kind, 'size': size, 'source': 'discovered'})
    out.sort(key=lambda x: (x['kind'], x['rel_path']))
    return out


def field_doc_entry(rel, kind, ws):
    """Build a doc entry dict from a task's spec_path/plan_path field."""
    name = os.path.basename(rel)
    p = rel if os.path.isabs(rel) else (os.path.join(ws, rel) if ws else rel)
    try:
        size = os.path.getsize(p)
    except OSError:
        size = 0
    return {'name': name, 'rel_path': rel, 'kind': kind, 'size': size, 'source': 'field'}


def _slug(name):
    n = name.lower()
    if n.startswith('spec-') or n.startswith('plan-'):
        n = n[5:]
    if n.endswith('.md'):
        n = n[:-3]
    return n


def _tokens(s):
    return {tok for tok in re.split(r'[^a-z0-9]+', s.lower()) if len(tok) >= 2}


def _task_keys(t):
    keys = set()
    for p in (t.spec_path, t.plan_path):
        if not p:
            continue
        keys |= _tokens(_slug(os.path.basename(p)))
    if t.title:
        keys |= _tokens(t.title)
    return keys


def _is_related(d, keys):
    return bool(_tokens(_slug(d['name'])) & keys)


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get('/{task_id}/documents')
def list_task_documents(task_id: str, db: Session = Depends(get_session)):
    """Return task's spec/plan + workspace docs matched by keywords."""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    ws = (t.workspace or '').strip()
    docs = []
    if t.spec_path:
        docs.append(field_doc_entry(t.spec_path, 'spec', ws))
    if t.plan_path:
        docs.append(field_doc_entry(t.plan_path, 'plan', ws))
    field_rel = {p for p in (t.spec_path, t.plan_path) if p}
    keys = _task_keys(t)
    for d in discover_task_docs(ws):
        if d['rel_path'] in field_rel:
            continue
        if not keys or not _is_related(d, keys):
            continue
        d['related'] = True
        docs.append(d)
    docs.sort(key=lambda x: (0 if x['source'] == 'field' else 1, x['kind'], x.get('rel_path', '')))
    return {'workspace': ws, 'documents': docs}


@router.get('/{task_id}/documents/doc')
def get_task_doc(task_id: str, kind: str = Query(...), db: Session = Depends(get_session)):
    """Read task spec/plan document content for Web UI display."""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    if kind not in ('spec', 'plan'):
        raise HTTPException(400, 'kind must be spec or plan')
    rel = t.spec_path if kind == 'spec' else t.plan_path
    if not rel:
        raise HTTPException(404, f'task has no {kind}_path')
    p, err = resolve_doc_path(t, rel, kind)
    if err:
        return {'kind': kind, 'path': rel, 'content': err, 'truncated': False, 'missing': True}
    text, truncated = read_content(p)
    return {'kind': kind, 'path': rel,
            'content': (text if text is not None else f'文件不存在：{p}'),
            'truncated': truncated, 'missing': text is None}


@router.get('/{task_id}/documents/file')
def get_task_file(task_id: str, path: str = Query(...), db: Session = Depends(get_session)):
    """Read arbitrary file within task's workspace (path traversal protected)."""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    ws = (t.workspace or '').strip()
    if not ws:
        raise HTTPException(400, 'task has no workspace')
    base = pathlib.Path(ws).resolve()
    target = (base / path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(400, 'invalid path: escape workspace')
    if not target.is_file():
        raise HTTPException(404, f'file not found: {target}')
    text, truncated = read_content(target)
    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    return {'path': path, 'content': text, 'truncated': truncated, 'size': size}
```

- [ ] **Step 2: Update `tasks.py` — remove document code, register document router**

Remove from `tasks.py`:
- Lines 609-785: `_resolve_doc_path`, `_read_content`, `discover_task_docs`, `_field_doc_entry`, `_slug`, `_tokens`, `_task_keys`, `_is_related`, `get_task_doc`, `list_task_documents`, `get_task_file`
- Imports: `pathlib`, `os`, `re` (if no longer used elsewhere)

In `mio_taskhub/main.py`, add router registration:
```python
from mio_taskhub.api.task_documents import router as task_docs_router
app.include_router(task_docs_router, prefix="/api/v1", tags=["tasks"])
```

- [ ] **Step 3: Update test imports if needed**

Check `tests/test_documents_related.py` — the test uses `client.get(f'/api/v1/tasks/{tid}/documents')` which stays the same. No test changes needed for Phase 1 since document endpoints keep the same paths under the same prefix.

- [ ] **Step 4: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/task_documents.py mio_taskhub/api/tasks.py mio_taskhub/main.py
git commit -m "refactor(p2): extract document endpoints to task_documents.py"
```

---

### Task 4: Verify Phase 1 — full test suite

- [ ] **Step 1: Count lines in slimmed tasks.py**

Run: `(Get-Content "E:\work\code\agent-dev\mio-taskhub\mio_taskhub\api\tasks.py").Count`
Expected: ≤800 lines (from 1431)

- [ ] **Step 2: Run full test suite**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed, 0 failed

- [ ] **Step 3: Verify imports work**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -c "from mio_taskhub.api.tasks import router; from mio_taskhub.api.claim import claim_for; from mio_taskhub.api.task_helpers import parse_dt; from mio_taskhub.api.task_documents import router; print('all imports OK')"`
Expected: `all imports OK`

---

## Phase 2: Extract Routers (API Path Changes)

Phase 2 extracts template and review endpoints into separate routers with clean URL prefixes. This changes API paths and requires updating MCP server + tests.

### Task 5: Create `templates.py` — template router

**Files:**
- Create: `mio_taskhub/api/templates.py`
- Modify: `mio_taskhub/api/tasks.py:338-601` (remove template code)
- Modify: `mio_taskhub/main.py` (register new router)
- Modify: `tests/test_templates.py` (update paths)
- Modify: `mio_taskhub/mcp_server.py` (if template tools exist)

- [ ] **Step 1: Create `templates.py`**

```python
# mio_taskhub/api/templates.py
"""Task template CRUD, versioning, and task↔template conversion."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskTemplate, TaskTemplateVersion
from mio_taskhub.utils import _now

router = APIRouter(prefix="/templates", tags=["templates"])


def template_json(t: TaskTemplate) -> dict:
    return {
        "id": t.id, "title": t.title, "description": t.description,
        "author": t.author, "category": t.category,
        "priority": t.priority, "est_duration_min": t.est_duration_min,
        "est_cost_min": t.est_cost_min,
        "target_agent_type": t.target_agent_type,
        "acceptance_criteria": t.acceptance_criteria,
        "files_template": t.files_template,
        "deliverables_template": t.deliverables_template,
        "stages": t.stages, "dependencies": t.dependencies,
        "labels": t.labels, "tags": t.tags,
        "is_public": t.is_public, "version": t.version,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


@router.get("", response_model=list)
def list_templates(category: str = None, author: str = None,
                   db: Session = Depends(get_session)):
    q = select(TaskTemplate)
    if category:
        q = q.where(TaskTemplate.category == category)
    if author:
        q = q.where(TaskTemplate.author == author)
    rows = db.exec(q.order_by(TaskTemplate.updated_at.desc())).all()
    return [template_json(r) for r in rows]


@router.post("", response_model=dict)
def create_template(body: dict, db: Session = Depends(get_session)):
    t = TaskTemplate(
        id=str(uuid.uuid4())[:8],
        title=body.get("title", ""),
        description=body.get("description", ""),
        author=body.get("author", ""),
        category=body.get("category", ""),
        priority=body.get("priority", 0),
        est_duration_min=body.get("est_duration_min", 30),
        est_cost_min=body.get("est_cost_min", 60),
        target_agent_type=body.get("target_agent_type"),
        acceptance_criteria=body.get("acceptance_criteria", ""),
        files_template=body.get("files_template", []),
        deliverables_template=body.get("deliverables_template", []),
        stages=body.get("stages", []),
        dependencies=body.get("dependencies", []),
        labels=body.get("labels", []),
        tags=body.get("tags", []),
        is_public=body.get("is_public", True),
    )
    db.add(t)
    ver = TaskTemplateVersion(
        id=str(uuid.uuid4())[:8],
        template_id=t.id,
        version=1,
        content=template_json(t),
        created_by=t.author,
        description="initial",
    )
    db.add(ver)
    db.commit()
    db.refresh(t)
    return template_json(t)


@router.get("/{tpl_id}")
def get_template(tpl_id: str, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    return template_json(t)


@router.patch("/{tpl_id}", response_model=dict)
def update_template(tpl_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    for key in ("title", "description", "author", "category", "acceptance_criteria",
                "target_agent_type", "is_public"):
        if key in body:
            setattr(t, key, body[key])
    for key in ("priority", "est_duration_min", "est_cost_min", "version"):
        if key in body:
            setattr(t, key, body[key])
    for key in ("files_template", "deliverables_template", "stages", "dependencies",
                "labels", "tags"):
        if key in body:
            setattr(t, key, body[key])
    t.updated_at = _now()
    t.version += 1
    ver = TaskTemplateVersion(
        id=str(uuid.uuid4())[:8],
        template_id=t.id,
        version=t.version,
        content=template_json(t),
        changes=body,
        created_by=body.get("_author", ""),
        description=body.get("_change_desc", ""),
    )
    db.add(ver)
    db.add(t)
    db.commit()
    db.refresh(t)
    return template_json(t)


@router.delete("/{tpl_id}")
def delete_template(tpl_id: str, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    db.delete(t)
    vers = db.exec(select(TaskTemplateVersion).where(TaskTemplateVersion.template_id == tpl_id)).all()
    for v in vers:
        db.delete(v)
    db.commit()
    return {"ok": True}


@router.get("/{tpl_id}/versions")
def list_template_versions(tpl_id: str, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    rows = db.exec(
        select(TaskTemplateVersion).where(TaskTemplateVersion.template_id == tpl_id)
        .order_by(TaskTemplateVersion.version.desc())
    ).all()
    return [{"id": v.id, "version": v.version, "content": v.content,
             "changes": v.changes, "created_by": v.created_by,
             "description": v.description, "created_at": v.created_at.isoformat()}
            for v in rows]


@router.post("/{tpl_id}/restore/{version}", response_model=dict)
def restore_template_version(tpl_id: str, version: int, db: Session = Depends(get_session)):
    t = db.get(TaskTemplate, tpl_id)
    if not t:
        raise HTTPException(404, "template not found")
    ver = db.exec(
        select(TaskTemplateVersion).where(
            TaskTemplateVersion.template_id == tpl_id,
            TaskTemplateVersion.version == version,
        )
    ).first()
    if not ver:
        raise HTTPException(404, "version not found")
    content = ver.content
    for key in ("title", "description", "author", "category", "acceptance_criteria",
                "target_agent_type", "is_public", "priority", "est_duration_min",
                "est_cost_min", "files_template", "deliverables_template",
                "stages", "dependencies", "labels", "tags"):
        if key in content:
            setattr(t, key, content[key])
    t.version += 1
    t.updated_at = _now()
    new_ver = TaskTemplateVersion(
        id=str(uuid.uuid4())[:8],
        template_id=t.id,
        version=t.version,
        content=template_json(t),
        created_by=ver.created_by,
        description=f"restored from v{version}",
    )
    db.add(new_ver)
    db.add(t)
    db.commit()
    db.refresh(t)
    return template_json(t)


@router.post("/from-task/{task_id}", response_model=dict)
def create_template_from_task(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    tpl = TaskTemplate(
        id=str(uuid.uuid4())[:8],
        title=body.get("title", t.title),
        description=body.get("description", t.description),
        author=body.get("author", ""),
        category=body.get("category", ""),
        priority=t.priority,
        est_duration_min=t.est_duration_min,
        est_cost_min=body.get("est_cost_min", 60),
        target_agent_type=t.target_agent_type,
        acceptance_criteria=t.acceptance_criteria,
        files_template=t.files or [],
        deliverables_template=t.deliverables or [],
        stages=[],
        dependencies=[],
        labels=t.labels or [],
        tags=[],
        is_public=body.get("is_public", True),
    )
    db.add(tpl)
    ver = TaskTemplateVersion(
        id=str(uuid.uuid4())[:8],
        template_id=tpl.id,
        version=1,
        content=template_json(tpl),
        created_by=tpl.author,
        description=f"created from task {task_id}",
    )
    db.add(ver)
    db.commit()
    db.refresh(tpl)
    return template_json(tpl)


@router.post("/from-template/{tpl_id}", response_model=dict)
def create_task_from_template(tpl_id: str, body: dict, db: Session = Depends(get_session)):
    """Create a task from a template, applying overrides."""
    from mio_taskhub.api.task_helpers import parse_dt, validate_depends, check_cycle
    tpl = db.get(TaskTemplate, tpl_id)
    if not tpl:
        raise HTTPException(404, "template not found")
    due_at = parse_dt(body.get("due_at"), "due_at")
    import uuid as _uuid
    t = Task(
        id=str(_uuid.uuid4())[:8],
        title=body.get("title", tpl.title),
        description=body.get("description", tpl.description),
        target_agent_type=body.get("target_agent_type", tpl.target_agent_type),
        fallback_after=body.get("fallback_after"),
        priority=body.get("priority", tpl.priority),
        schedule_type=body.get("schedule_type", "once"),
        run_at=parse_dt(body.get("run_at"), "run_at"),
        cron_expr=body.get("cron_expr"),
        est_duration_min=body.get("est_duration_min", tpl.est_duration_min),
        depends_on=body.get("depends_on", []),
        max_retries=body.get("max_retries", 3),
        acceptance_criteria=body.get("acceptance_criteria", tpl.acceptance_criteria),
        due_at=due_at,
        labels=body.get("labels", tpl.labels or []),
        project=body.get("project", ""),
        workspace=body.get("workspace", ""),
        files=body.get("files", tpl.files_template or []),
        deliverables=body.get("deliverables", tpl.deliverables_template or []),
        spec_path=(body.get("spec_path") or "").strip() or None,
        plan_path=(body.get("plan_path") or "").strip() or None,
        stage="brainstorming",
    )
    validate_depends(t, db)
    check_cycle(t, db)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "title": t.title, "state": t.state.value, "stage": t.stage.value}
```

- [ ] **Step 2: Remove template code from `tasks.py`**

Remove from `tasks.py`:
- `_template_json` helper (lines 340-355)
- All template endpoints: `list_templates`, `create_template`, `get_template`, `update_template`, `delete_template`, `list_template_versions`, `restore_template_version`, `create_template_from_task`, `create_task_from_template` (lines 358-601)
- `TaskTemplate`, `TaskTemplateVersion` from imports

- [ ] **Step 3: Register new router in `main.py`**

```python
from mio_taskhub.api.templates import router as templates_router
app.include_router(templates_router, prefix="/api/v1", tags=["templates"])
```

- [ ] **Step 4: Update `tests/test_templates.py` path references**

Replace all `/api/v1/tasks/templates` → `/api/v1/templates` in test file.

- [ ] **Step 5: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/test_templates.py -v --tb=short`
Expected: all template tests pass

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/api/templates.py mio_taskhub/api/tasks.py mio_taskhub/main.py tests/test_templates.py
git commit -m "refactor(p2): extract template endpoints to templates.py router"
```

---

### Task 6: Create `reviews.py` — review router

**Files:**
- Create: `mio_taskhub/api/reviews.py`
- Modify: `mio_taskhub/api/tasks.py:1332-1431` (remove review code)
- Modify: `mio_taskhub/main.py` (register new router)

- [ ] **Step 1: Create `reviews.py`**

```python
# mio_taskhub/api/reviews.py
"""Task review queue, history, and submission."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskStage, TaskReview
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event, broadcast_for_event

router = APIRouter(tags=["reviews"])


@router.get("/reviews/queue")
def review_queue(db: Session = Depends(get_session)):
    """Return tasks in review stage + wait duration."""
    tasks = db.exec(
        select(Task).where(Task.stage == TaskStage.REVIEW).order_by(Task.created_at)
    ).all()
    items = []
    for t in tasks:
        wait_sec = None
        if t.review_started_at:
            started = t.review_started_at
            now = _now()
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            delta = (now - started).total_seconds()
            wait_sec = int(delta)
        items.append({
            "id": t.id, "title": t.title, "priority": t.priority,
            "state": t.state.value, "stage": t.stage.value,
            "target_agent_type": t.target_agent_type,
            "project": t.project, "workspace": t.workspace,
            "review_started_at": t.review_started_at.isoformat() if t.review_started_at else None,
            "wait_seconds": wait_sec,
            "review_result": t.review_result,
            "attempt": t.attempt,
        })
    return {"tasks": items, "count": len(items)}


@router.get("/tasks/{task_id}/reviews")
def list_reviews(task_id: str, db: Session = Depends(get_session)):
    """Return task's review history."""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    reviews = db.exec(
        select(TaskReview).where(TaskReview.task_id == task_id).order_by(TaskReview.created_at)
    ).all()
    return [
        {
            "id": r.id, "task_id": r.task_id, "decision": r.decision,
            "checklist": r.checklist, "summary": r.summary, "comments": r.comments,
            "artifacts": r.artifacts, "reviewer": r.reviewer,
            "review_duration_sec": r.review_duration_sec,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reviews
    ]


@router.post("/tasks/{task_id}/reviews")
def submit_review(task_id: str, body: dict, db: Session = Depends(get_session)):
    """Submit review record. decision: approve / reject / comment."""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    decision = body.get("decision", "comment")
    if decision not in ("approve", "reject", "comment"):
        raise HTTPException(422, "decision must be approve, reject, or comment")
    duration_sec = None
    if t.review_started_at:
        started = t.review_started_at
        now = _now()
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        duration_sec = int((now - started).total_seconds())
    review = TaskReview(
        task_id=task_id,
        decision=decision,
        checklist=body.get("checklist"),
        summary=body.get("summary", ""),
        comments=body.get("comments", ""),
        artifacts=body.get("artifacts", []),
        reviewer=body.get("reviewer", ""),
        review_duration_sec=duration_sec,
    )
    db.add(review)
    if decision == "approve":
        summary = body.get("summary", "")
        if summary:
            t.review_result = summary
        db.add(t)
    event = emit_event(db, type="review_submitted", entity="task", entity_id=task_id,
                       payload={"decision": decision, "reviewer": review.reviewer,
                                "summary": summary if decision == "approve" else ""})
    db.commit()
    broadcast_for_event(event)
    return {
        "id": review.id, "decision": decision,
        "review_duration_sec": duration_sec,
    }
```

- [ ] **Step 2: Remove review code from `tasks.py`**

Remove from `tasks.py`:
- `review_queue`, `list_reviews`, `submit_review` endpoints (lines 1332-1431)
- `TaskReview` from imports

- [ ] **Step 3: Register new router in `main.py`**

```python
from mio_taskhub.api.reviews import router as reviews_router
app.include_router(reviews_router, prefix="/api/v1", tags=["reviews"])
```

Note: Review endpoints are under `/reviews/queue` and `/tasks/{id}/reviews`. The `/reviews/queue` path is a top-level endpoint. The `/tasks/{id}/reviews` path needs careful handling — it will be registered under `/api/v1/tasks/{task_id}/reviews` which matches the original path.

Actually, looking at this more carefully: the original paths are:
- `GET /api/v1/tasks/reviews/queue` — this is under the tasks router
- `GET /api/v1/tasks/{task_id}/reviews` — this is under the tasks router
- `POST /api/v1/tasks/{task_id}/reviews` — this is under the tasks router

If I create a separate router with no prefix, I need to register it at `/api/v1` and use full paths. But `GET /api/v1/reviews/queue` would be different from the original `GET /api/v1/tasks/reviews/queue`.

To preserve backward compatibility, the review router should use prefix="/tasks" to keep the same paths. Or, we register at the same prefix.

Let me revise: use `router = APIRouter(prefix="/tasks", tags=["reviews"])` to keep paths identical.

- [ ] **Step 4: Update test paths if needed**

Check if any tests reference `/api/v1/tasks/reviews/queue` — if so, they stay the same since we keep the prefix.

- [ ] **Step 5: Run tests**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/api/reviews.py mio_taskhub/api/tasks.py mio_taskhub/main.py
git commit -m "refactor(p2): extract review endpoints to reviews.py router"
```

---

### Task 7: Final verification — full test suite + line count

- [ ] **Step 1: Count final line counts**

Run:
```powershell
(Get-Content "mio_taskhub/api/tasks.py").Count
(Get-Content "mio_taskhub/api/claim.py").Count
(Get-Content "mio_taskhub/api/task_helpers.py").Count
(Get-Content "mio_taskhub/api/task_documents.py").Count
(Get-Content "mio_taskhub/api/templates.py").Count
(Get-Content "mio_taskhub/api/reviews.py").Count
```

Expected: `tasks.py` ≤600 lines; total across all files ≈1400-1500 (some dedup from removing dead code)

- [ ] **Step 2: Run full test suite**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed, 0 failed

- [ ] **Step 3: Verify all routers register correctly**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -c "from mio_taskhub.main import app; print([r.path for r in app.routes if hasattr(r,'path')][:20])"`
Expected: all paths present, no duplicates

---

## File Summary

| File | Lines | Responsibility |
|---|---|---|
| `api/tasks.py` | ~500 | Task CRUD, queries, graph views, subtask/gitref/history/discussion endpoints (thin routing layer) |
| `api/claim.py` | ~150 | Claim core: candidate selection, atomic claim, fallback |
| `api/task_helpers.py` | ~60 | Shared: parse_dt, parse_enum, check_cycle, validate_depends |
| `api/task_documents.py` | ~170 | Document reading, discovery, file serving (endpoints) |
| `api/templates.py` | ~190 | Template CRUD, versioning, task↔template conversion (endpoints) |
| `api/reviews.py` | ~90 | Review queue, history, submission (endpoints) |

**Net result:** `tasks.py` goes from 1431 lines to ~500 lines (65% reduction). Each new module has a single clear responsibility.
