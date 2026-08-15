# 编排引擎 + 事件订阅 + Idea 拆解 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 depends_on 多依赖 DAG 编排（依赖满足自动放行）、统一事件日志（seq 增量订阅 + MCP 工具）、Idea→Task 批量拆解，并清理测试残留数据。

**Architecture:** 新增 `status.py`（统一终态/依赖判据纯函数）与 `events.py`（emit_event 写事件表 + broadcast_for_event 统一 WS 广播）。调度器 tick 内新增依赖放行。`Task.depends_on` 升级为 JSON 数组，`Task.idea_id`/`Event.entity` 等迁移在 `db._migrate_stage_column` 扩展。前端 FlowView 加依赖角标、TaskDetail 加依赖区块、IdeasView 加拆解表单。

**Tech Stack:** Python 3.12 / FastAPI / SQLModel / SQLite / React 18 / Vite / pytest

---

### Task 1: status.py（统一状态判据纯函数）

**Files:**
- Create: `mio_taskhub/status.py`
- Test: `tests/test_status.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_status.py
import pytest
from mio_taskhub.status import is_terminal, dependency_satisfied, task_deps, normalize_depends
from mio_taskhub.models import Task, TaskStage, TaskState


def _mk(state="queued", stage="ready"):
    t = Task(title="t")
    t.state = TaskState(state) if isinstance(state, str) else state
    if isinstance(stage, str):
        t.stage = stage
    return t


def test_is_terminal_via_state():
    assert is_terminal(_mk(state="completed"))
    assert is_terminal(_mk(state="cancelled"))
    assert is_terminal(_mk(state="failed"))
    assert is_terminal(_mk(state="blocked_failed"))
    assert not is_terminal(_mk(state="queued"))


def test_is_terminal_via_stage():
    assert is_terminal(_mk(state="queued", stage="done"))
    assert is_terminal(_mk(state="queued", stage="cancelled"))
    assert not is_terminal(_mk(state="queued", stage="ready"))


def test_dependency_satisfied():
    assert dependency_satisfied(_mk(state="completed"))
    assert dependency_satisfied(_mk(state="queued", stage="done"))
    assert not dependency_satisfied(_mk(state="queued", stage="ready"))
    assert not dependency_satisfied(_mk(state="cancelled"))
    assert not dependency_satisfied(_mk(state="failed"))


def test_task_deps_normalizes_str_and_none():
    t1 = _mk(); t1.depends_on = "abc"          # 旧单值字符串
    assert task_deps(t1) == ["abc"]
    t2 = _mk(); t2.depends_on = None
    assert task_deps(t2) == []
    t3 = _mk(); t3.depends_on = ["a", "b"]
    assert task_deps(t3) == ["a", "b"]


def test_normalize_depends_table():
    assert normalize_depends(None) == []
    assert normalize_depends("") == []
    assert normalize_depends("  ") == []
    assert normalize_depends("abc") == ["abc"]
    assert normalize_depends('["a","b"]') == ["a", "b"]
    assert normalize_depends("[") == []          # 非法 JSON
    assert normalize_depends("{x") == []
    assert normalize_depends(["a"]) == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_status.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'mio_taskhub.status'`

- [ ] **Step 3: Write implementation**

```python
# mio_taskhub/status.py
"""统一的任务状态判据（终态 / 依赖满足），供 DAG、Board、Scheduler、Planner、前端联动使用。"""
from __future__ import annotations
import json
import logging
from typing import Any

logger = logging.getLogger("mio_taskhub.status")

# state 维度上的终态集合（stage=done/cancelled 也视为终态，见 is_terminal）
TERMINAL_STATES = {"completed", "cancelled", "failed", "blocked_failed"}


def _stage_str(v) -> str:
    if v is None:
        return ""
    return v.value if not isinstance(v, str) else v


def is_terminal(task) -> bool:
    """任务不可再被调度/放行（终态）。state 或 stage 任一为终态即 True。"""
    s = task.state.value if hasattr(task.state, "value") else task.state
    st = _stage_str(task.stage)
    return s in TERMINAL_STATES or st in ("done", "cancelled")


def dependency_satisfied(task) -> bool:
    """作为前置依赖时是否算满足：state=completed 或 stage=done。"""
    s = task.state.value if hasattr(task.state, "value") else task.state
    return s == "completed" or _stage_str(task.stage) == "done"


def normalize_depends(value: Any) -> list:
    """把 depends_on 的任意旧值/新值归一化为列表。

    - None / 空白字符串 → []
    - 非 JSON 单值字符串（旧库 VARCHAR 列）→ [value]
    - 合法 JSON 数组字符串 → 解析为列表
    - 非法 JSON → [] + warning（不阻塞）
    - 已是 list → 原样（清掉空白项）
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [x for x in value if x]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                arr = json.loads(s)
                return [x for x in arr if x] if isinstance(arr, list) else []
            except ValueError:
                logger.warning("depends_on 非法 JSON，已置空: %r", value)
                return []
        return [s]
    return []


def task_deps(task) -> list:
    """读取任务依赖列表（兼容旧字符串/None/列表）。"""
    return normalize_depends(getattr(task, "depends_on", None))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_status.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/status.py tests/test_status.py
git commit -m "feat: status.py 统一终态/依赖满足/依赖归一化纯函数"
```

### Task 2: events.py（统一事件写入 + 广播入口）

**Files:**
- Create: `mio_taskhub/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**

```python
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
        assert e.id is not None       # 自增 seq
        s.add(e)                       # emit_event 不 commit，由调用方提交
        s.commit()
        assert e.id >= 1


def test_event_payload_roundtrip():
    with Session(engine) as s:
        e = emit_event(s, type="heartbeat", entity="run", entity_id="r1",
                       payload={"progress": 50})
        s.add(e); s.commit()
        got = s.get(Event, e.id)
        d = event_to_dict(got)
        assert d["payload"]["progress"] == 50
        assert d["entity"] == "run" and d["entity_id"] == "r1"


def test_seq_monotonic():
    with Session(engine) as s:
        a = emit_event(s, type="a", entity="task", entity_id="1")
        b = emit_event(s, type="b", entity="task", entity_id="2")
        s.add_all([a, b]); s.commit()
    assert b.id > a.id


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_events.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'mio_taskhub.events'`

- [ ] **Step 3: Write implementation**

```python
# mio_taskhub/events.py
"""统一事件日志：emit_event 写 Event 表（与业务同事务），broadcast_for_event 统一 WS 广播。

约定：写操作端点先 emit_event(db, ...) 拿到 Event 对象，随业务 db.commit()，
提交后调用 broadcast_for_event(event) 触发 WS 推送。
"""
from __future__ import annotations
import asyncio
import json
from typing import Optional
from sqlmodel import Session
from mio_taskhub.models import Event
from mio_taskhub.notifications import ws_manager

# entity → WS 消息 type
MESSAGE_TYPES = {
    "task": "task_update",
    "idea": "idea_update",
    "discussion": "discussion_update",
}


def emit_event(db: Session, type: str, entity: str = "", entity_id: str = "",
               run_id: str = "", payload: Optional[dict] = None) -> Event:
    """构造并 db.add 一条事件（不 commit，与业务同事务提交）。返回 Event 对象（含自增 seq）。"""
    e = Event(
        type=type,
        entity=entity,
        entity_id=entity_id,
        run_id=run_id,
        payload=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
    )
    db.add(e)
    return e


def event_to_dict(e: Event) -> dict:
    return {
        "seq": e.id,
        "type": e.type,
        "entity": e.entity,
        "entity_id": e.entity_id,
        "run_id": e.run_id or "",
        "payload": json.loads(e.payload) if e.payload else None,
        "at": e.at.isoformat(),
    }


def broadcast_for_event(event: Event):
    """按 entity 映射 WS 消息类型并广播。异步隔离，异常静默。"""
    msg_type = MESSAGE_TYPES.get(event.entity, "event_update")
    try:
        asyncio.run(ws_manager.broadcast({"type": msg_type, "event": event_to_dict(event)}))
    except Exception:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_events.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/events.py tests/test_events.py
git commit -m "feat: events.py 统一事件写入与 WS 广播入口"
```

### Task 3: 模型与迁移（depends_on 数组、idea_id、Event 扩展）

**Files:**
- Modify: `mio_taskhub/models.py`
- Modify: `mio_taskhub/db.py`
- Test: `tests/test_db.py`, `tests/test_models.py`

- [ ] **Step 1: Write the failing test (append to test_db.py)**

```python
def test_migrate_depends_on_and_idea_id():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE task (id VARCHAR PRIMARY KEY, title VARCHAR, state VARCHAR, "
                "depends_on VARCHAR)"
            ))
            conn.execute(text(
                "INSERT INTO task (id, title, state, depends_on) VALUES "
                "('t1','a','queued','parent'), ('t2','b','queued','[\"x\",\"y\"]'), "
                "('t3','c','queued',NULL), ('t4','d','queued','[')"
            ))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("task")}
            assert "idea_id" in cols
            rows = {r[0]: r[1] for r in conn.execute(text("SELECT id, depends_on FROM task"))}
            assert rows["t1"] == '["parent"]'
            assert rows["t2"] == '["x","y"]'
            assert rows["t3"] == "[]"
            assert rows["t4"] == "[]"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_migrate_event_entity_columns():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE event (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR, "
                "type VARCHAR, payload VARCHAR, at VARCHAR)"
            ))
            conn.execute(text("INSERT INTO event (run_id, type) VALUES ('r1', 'heartbeat')"))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("event")}
            assert "entity" in cols and "entity_id" in cols
            row = conn.execute(text("SELECT entity, entity_id FROM event WHERE run_id='r1'")).fetchone()
            assert row[0] == "run" and row[1] == "r1"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py -q`
Expected: FAIL on the two new tests (`no such column: idea_id` / `no such column: entity`)

- [ ] **Step 3: Update models.py**

`Task.depends_on`（第 97 行）改为 JSON 数组；`Task` 末尾加 `idea_id`；`Event`（第 179-184 行）加 `entity`/`entity_id`、`run_id` 允许空：

```python
from sqlalchemy import Column, JSON

class Task(SQLModel, table=True):
    # ...（保留原有字段）...
    depends_on: list = Field(default_factory=list, sa_column=Column(JSON))
    # ...（其余字段不变）...
    idea_id: str = Field(default="", index=True)   # 拆解来源 idea
    created_at: datetime = Field(default_factory=_now)
```

```python
class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)   # 自增 seq
    type: str
    entity: str = Field(default="", index=True)
    entity_id: str = Field(default="", index=True)
    run_id: str = Field(default="", index=True)                 # 兼容旧字段
    payload: Optional[str] = None
    at: datetime = Field(default_factory=_now)
```

- [ ] **Step 4: Update db.py migration**

在 `_migrate_stage_column` 的 task 部分追加 idea_id 与 depends_on 归一化；在 discussion 部分之后追加 event 表迁移：

```python
        if "idea_id" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN idea_id VARCHAR NOT NULL DEFAULT ''"))
        # depends_on 归一化（旧 VARCHAR 单值 → JSON 数组文本）
        from mio_taskhub.status import normalize_depends
        import json as _json
        dep_rows = conn.execute(text("SELECT id, depends_on FROM task")).fetchall()
        for _id, _val in dep_rows:
            norm = normalize_depends(_val)
            if _val != _json.dumps(norm):
                conn.execute(text("UPDATE task SET depends_on=? WHERE id=?"),
                             (_json.dumps(norm), _id))
```

```python
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "event" in tables:
            ecols = {c["name"] for c in inspect(conn).get_columns("event")}
            if "entity" not in ecols:
                conn.execute(text("ALTER TABLE event ADD COLUMN entity VARCHAR NOT NULL DEFAULT ''"))
            if "entity_id" not in ecols:
                conn.execute(text("ALTER TABLE event ADD COLUMN entity_id VARCHAR NOT NULL DEFAULT ''"))
            conn.execute(text(
                "UPDATE event SET entity='run', entity_id=run_id WHERE entity='' AND run_id IS NOT NULL AND run_id != ''"
            ))
```

- [ ] **Step 5: Run all existing + new tests**

Run: `python -m pytest tests/test_db.py tests/test_models.py tests/test_status.py -q`
Expected: PASS（含新增 2 个迁移测试）

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/models.py mio_taskhub/db.py tests/test_db.py
git commit -m "feat: Task.depends_on 数组化 + idea_id / Event.entity 迁移"
```

### Task 4: 事件查询接口 GET /api/v1/events

**Files:**
- Create: `mio_taskhub/api/events.py`
- Modify: `mio_taskhub/main.py`
- Test: `tests/test_events_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_events_api.py
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from sqlmodel import Session
from mio_taskhub.db import engine
from mio_taskhub.events import emit_event

client = TestClient(app)


def _seed(n=3):
    with Session(engine) as s:
        for i in range(n):
            s.add(emit_event(s, type=f"t{i}", entity="task", entity_id=f"id{i}",
                             payload={"i": i}))
        s.commit()


def test_empty_db():
    r = client.get("/api/v1/events")
    assert r.status_code == 200
    data = r.json()
    assert data["events"] == [] and data["next_seq"] == 0


def test_latest_without_after_seq():
    _seed()
    r = client.get("/api/v1/events")
    data = r.json()
    assert len(data["events"]) == 3
    assert data["next_seq"] == data["events"][-1]["seq"]


def test_incremental_after_seq():
    _seed(5)
    r1 = client.get("/api/v1/events").json()
    last = r1["next_seq"]
    _seed(2)
    r2 = client.get("/api/v1/events", params={"after_seq": last}).json()
    assert len(r2["events"]) == 2
    assert all(e["seq"] > last for e in r2["events"])


def test_after_seq_zero_returns_all():
    _seed(3)
    r = client.get("/api/v1/events", params={"after_seq": 0}).json()
    assert len(r["events"]) == 3
    assert r["events"][0]["seq"] >= 1


def test_payload_parsed_to_dict():
    _seed(1)
    r = client.get("/api/v1/events").json()
    assert r["events"][0]["payload"] == {"i": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_events_api.py -q`
Expected: FAIL with `404 Not Found` for `/api/v1/events`

- [ ] **Step 3: Write implementation**

```python
# mio_taskhub/api/events.py
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Event
from mio_taskhub.events import event_to_dict

router = APIRouter(prefix="/events", tags=["events"])

DEFAULT_LIMIT = 200


@router.get("")
def list_events(after_seq: int = Query(None), limit: int = Query(DEFAULT_LIMIT, ge=1, le=1000),
                db: Session = Depends(get_session)):
    """事件订阅：after_seq 不传返回最近 limit 条；=0 返回全部；=N 返回 seq>N 的增量。"""
    q = select(Event)
    if after_seq is not None and after_seq > 0:
        q = q.where(Event.id > after_seq)
    rows = db.exec(q.order_by(Event.id.asc()).limit(limit)).all()
    events = [event_to_dict(e) for e in rows]
    return {
        "events": events,
        "next_seq": events[-1]["seq"] if events else 0,
    }
```

在 `main.py` 中注册：

```python
from mio_taskhub.api import tasks, agents, runs, plans, board, ideas, discussions, events
# ...（现有各 router 之后）...
app.include_router(events.router, prefix="/api/v1", tags=["events"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_events_api.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/events.py mio_taskhub/main.py tests/test_events_api.py
git commit -m "feat: GET /api/v1/events seq 增量订阅"
```

### Task 5: planner（detect_cycle + 拓扑排序列表化）

**Files:**
- Modify: `mio_taskhub/planner.py`
- Modify: `mio_taskhub/api/plans.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: Write the failing test (append to test_planner.py)**

```python
from mio_taskhub.planner import detect_cycle

def test_detect_cycle_none():
    deps = {"a": [], "b": ["a"], "c": ["a", "b"]}
    assert detect_cycle(deps) == []

def test_detect_cycle_simple():
    deps = {"a": ["b"], "b": ["a"]}
    path = detect_cycle(deps)
    assert path, "expected a cycle"
    assert path[0] == path[-1]

def test_detect_cycle_chain():
    deps = {"a": [], "b": ["a"], "c": ["b"], "a": ["c"]}
    assert detect_cycle(deps) != []

def test_multi_dep_ordering():
    # parent done → children；child2 依赖 parent + child1
    tasks = [
        {"id": "p", "title": "p", "est_duration_min": 30, "priority": 0, "depends_on": []},
        {"id": "c1", "title": "c1", "est_duration_min": 30, "priority": 0, "depends_on": ["p"]},
        {"id": "c2", "title": "c2", "est_duration_min": 30, "priority": 0, "depends_on": ["p", "c1"]},
    ]
    plan = generate_night_plan(tasks, window_start=time(22, 0), window_end=time(7, 0))
    ids = [i.task_id for i in plan.items]
    assert ids.index("p") < ids.index("c1") < ids.index("c2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_planner.py -q`
Expected: FAIL with `ImportError: cannot import name 'detect_cycle'`

- [ ] **Step 3: Update planner.py**

`_topological_sort` 的依赖读取改为列表归一化，并新增 `detect_cycle`：

```python
from mio_taskhub.status import normalize_depends

def _deps_of(t: dict) -> list:
    return normalize_depends(t.get("depends_on"))

def _topological_sort(tasks: List[dict]) -> List[dict]:
    task_map = {t["id"]: t for t in tasks}
    in_degree = {t["id"]: 0 for t in tasks}
    children: Dict[str, List[str]] = {t["id"]: [] for t in tasks}
    for t in tasks:
        for d in _deps_of(t):
            if d in task_map:          # 缺失依赖忽略
                in_degree[t["id"]] += 1
                children[d].append(t["id"])
    queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
    queue = deque(sorted(queue, key=lambda tid: -task_map[tid].get("priority", 0)))
    result = []
    while queue:
        tid = queue.popleft()
        result.append(task_map[tid])
        for child_id in sorted(children[tid], key=lambda c: -task_map[c].get("priority", 0)):
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                queue.append(child_id)
    return result


def detect_cycle(deps: Dict[str, List[str]]) -> List[str]:
    """返回一条环路径 [a, b, a]；无环返回 []。deps: {task_id: [dep_ids]}。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in deps}
    stack: List[str] = []

    def dfs(u: str) -> List[str]:
        color[u] = GRAY
        stack.append(u)
        for v in deps.get(u, []):
            if v not in color:
                continue
            if color[v] == GRAY:
                return stack[stack.index(v):] + [v]
            if color[v] == WHITE:
                cyc = dfs(v)
                if cyc:
                    return cyc
        stack.pop()
        color[u] = BLACK
        return []

    for node in deps:
        if color[node] == WHITE:
            cyc = dfs(node)
            if cyc:
                return cyc
    return []
```

- [ ] **Step 4: Update api/plans.py to pass list deps**

`plans.py` 第 33 行 `"depends_on": t.depends_on` 改为归一化列表：

```python
from mio_taskhub.status import normalize_depends
        pool.append({
            "id": t.id, "title": t.title, "est_duration_min": t.est_duration_min,
            "priority": t.priority, "depends_on": normalize_depends(t.depends_on),
        })
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_planner.py -q`
Expected: PASS (8 passed: 原有 4 + 新增 4)

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/planner.py mio_taskhub/api/plans.py tests/test_planner.py
git commit -m "feat: detect_cycle 纯函数 + 拓扑排序多依赖列表化"
```

### Task 6: 调度器依赖自动放行

**Files:**
- Modify: `mio_taskhub/wiring.py`
- Modify: `mio_taskhub/scheduler.py`（可选：on_tick 钩子）
- Modify: `mio_taskhub/main.py`
- Test: `tests/test_dag.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dag.py
from datetime import datetime, timezone
from sqlmodel import Session, select
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage, TaskState
import mio_taskhub.wiring as wiring

client = TestClient(app)


def _mk(title, stage="ready", deps=None, **kw):
    body = {"title": title, "stage": stage}
    if deps is not None:
        body["depends_on"] = deps
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


def _release():
    wiring._release_dependencies()


def _stage(tid):
    with Session(engine) as s:
        return s.get(Task, tid).stage


def test_all_deps_met_releases_to_ready():
    parent = _mk("p", stage="done")
    child = _mk("c", stage="planning", deps=[parent["id"]])
    _release()
    assert _stage(child["id"]) == TaskStage.READY


def test_partial_deps_not_released():
    a = _mk("a", stage="done")
    b = _mk("b", stage="brainstorming")
    c = _mk("c", stage="planning", deps=[a["id"], b["id"]])
    _release()
    assert _stage(c["id"]) == TaskStage.PLANNING


def test_done_means_satisfied():
    a = _mk("a", stage="done")
    b = _mk("b", stage="planning", deps=[a["id"]])
    _release()
    assert _stage(b["id"]) == TaskStage.READY


def test_cancelled_dependency_blocks_and_alerts():
    a = _mk("a", stage="brainstorming")
    client.post(f"/api/v1/tasks/{a['id']}/stage", json={"target_stage": "cancelled"})
    b = _mk("b", stage="planning", deps=[a["id"]])
    _release()
    assert _stage(b["id"]) == TaskStage.PLANNING


def test_release_skips_terminal_and_ready():
    a = _mk("a", stage="done")
    ready = _mk("r", stage="ready", deps=[a["id"]])
    _release()
    assert _stage(ready["id"]) == TaskStage.READY  # 已 ready 不变


def test_release_writes_event():
    a = _mk("a", stage="done")
    b = _mk("b", stage="planning", deps=[a["id"]])
    _release()
    r = client.get("/api/v1/events", params={"after_seq": 0}).json()
    assert any(e["type"] == "task_released" and e["entity_id"] == b["id"] for e in r["events"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dag.py -q`
Expected: FAIL with `AttributeError: module 'mio_taskhub.wiring' has no attribute '_release_dependencies'`

- [ ] **Step 3: Update wiring.py**

新增 `_release_dependencies`（从 `_get_due_tasks` 前置调用），并扩展 `start_background_jobs`：

```python
from mio_taskhub.status import is_terminal, dependency_satisfied, task_deps
from mio_taskhub.events import emit_event, broadcast_for_event
from mio_taskhub.notifications import ws_manager


def _release_dependencies():
    """调度器 tick：把依赖全部满足的任务自动放行到 READY。

    放行范围：state 非终态、stage ∈ {brainstorming, design, planning}、depends_on 非空。
    前置全部 dependency_satisfied → stage=READY + 事件 + 广播。
    前置存在 cancelled/failed（不可放行）→ 不动（告警由 board.summary 生成）。
    """
    with Session(engine) as db:
        tasks = db.exec(select(Task)).all()
        for t in tasks:
            deps = task_deps(t)
            if not deps:
                continue
            stage_v = t.stage.value if not isinstance(t.stage, str) else t.stage
            if is_terminal(t) or stage_v not in ("brainstorming", "design", "planning"):
                continue
            prereqs = [db.get(Task, d) for d in deps if d]
            if prereqs and all(dependency_satisfied(p) for p in prereqs if p is not None):
                t.stage = TaskStage.READY
                event = emit_event(db, type="task_released", entity="task",
                                   entity_id=t.id, payload={"reason": "deps_met"})
                db.add(t)
                db.commit()
                broadcast_for_event(event)
```

在 `_get_due_tasks` 开头调用放行（保持单一调度入口）：

```python
def _get_due_tasks():
    _release_dependencies()   # 先放行依赖
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        tasks = db.exec(select(Task).where(Task.state == TaskState.QUEUED)).all()
        due = []
        for t in tasks:
            run_at = t.run_at
            if run_at is not None and run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if t.schedule_type == "cron" or (run_at and run_at <= now):
                due.append({"id": t.id})
        return due
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_dag.py tests/test_wiring.py -q`
Expected: PASS（test_dag 6 项 + wiring 回归）

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/wiring.py tests/test_dag.py
git commit -m "feat: 调度器依赖满足自动放行 READY"
```

### Task 7: tasks API（depends_on 归一化 + 环检测 + 字段输出）

**Files:**
- Modify: `mio_taskhub/api/tasks.py`
- Test: `tests/test_dag.py`, `tests/test_api.py`

- [ ] **Step 1: Write the failing tests (append to test_dag.py)**

```python
def test_create_task_with_deps_returns_array():
    r = _mk("d", stage="ready", deps=["abc"])
    assert r["depends_on"] == ["abc"]


def test_create_task_cyclic_dependency_rejected():
    a = _mk("a", stage="ready")
    b = _mk("b", stage="ready", deps=[a["id"]])
    r = client.patch(f"/api/v1/tasks/{a['id']}", json={"depends_on": [b["id"]]})
    assert r.status_code == 422
    assert "cyclic" in r.json()["detail"]


def test_update_depends_on_stores_list():
    a = _mk("a", stage="ready")
    b = _mk("b", stage="ready")
    r = client.patch(f"/api/v1/tasks/{a['id']}", json={"depends_on": [b["id"]]})
    assert r.status_code == 200
    d = client.get(f"/api/v1/tasks/{a['id']}").json()
    assert d["depends_on"] == [b["id"]]
    assert d["idea_id"] == ""


def test_legacy_string_depends_readable():
    # 直接建一个 depends_on 为字符串的任务模拟旧数据（模型层面已归一化）
    with Session(engine) as s:
        t = Task(title="legacy")
        t.depends_on = ["x"]
        s.add(t); s.commit()
        tid = t.id
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["depends_on"] == ["x"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dag.py -q`
Expected: FAIL（`create_task` 返回无 `depends_on`；环检测未实现）

- [ ] **Step 3: Update tasks.py**

引入 status/events，`create_task` 归一化 + 环检测 + 事件；`update_task` 支持改依赖 + 环检测；`list_tasks`/`_task_detail` 输出 `depends_on`/`idea_id`：

```python
from mio_taskhub.status import normalize_depends, task_deps
from mio_taskhub.events import emit_event, broadcast_for_event
from mio_taskhub.planner import detect_cycle


def _graph_with(task, db) -> dict:
    """构建 {id: [dep_ids]} 依赖图（含给定 task 的新值）。"""
    graph = {}
    for t in db.exec(select(Task)).all():
        graph[t.id] = task_deps(t)
    graph[task.id] = task_deps(task)
    return graph


def _check_cycle(task, db):
    cyc = detect_cycle(_graph_with(task, db))
    if cyc:
        raise HTTPException(422, f"cyclic dependency: {' → '.join(cyc)}")
```

`create_task` 末尾：

```python
    deps = normalize_depends(body.get("depends_on"))
    t = Task(
        # ...（原有参数，替换 depends_on 行）...
        depends_on=deps,
        # ...其余不变...
    )
    db.add(t)
    _check_cycle(t, db)                       # 成环则抛 422（未 commit，自动回滚）
    event = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                       payload={"title": t.title, "stage": t.stage.value})
    db.commit()
    db.refresh(t)
    broadcast_for_event(event)
    return {
        "id": t.id, "title": t.title, "state": t.state.value,
        "priority": t.priority, "created_at": t.created_at.isoformat(),
        "depends_on": list(t.depends_on or []), "idea_id": t.idea_id,
    }
```

`update_task` 中 `depends_on` 处理：

```python
    if "depends_on" in body:
        t.depends_on = normalize_depends(body["depends_on"])
        _check_cycle(t, db)
```

并在 update 末尾替换广播为事件：

```python
    db.add(t)
    event = emit_event(db, type="task_updated", entity="task", entity_id=t.id)
    db.commit()
    db.refresh(t)
    broadcast_for_event(event)
    return _task_detail(t, db)
```

`_task_detail` 返回对象加两字段：

```python
        "depends_on": list(t.depends_on or []),
        "idea_id": t.idea_id,
```

`list_tasks` 精简字段加：

```python
        {"id": r.id, "title": r.title, "state": r.state.value, "stage": r.stage.value,
         "priority": r.priority, "target_agent_type": r.target_agent_type,
         "depends_on": list(r.depends_on or []), "idea_id": r.idea_id}
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_dag.py tests/test_api.py tests/test_mcp_server.py -q`
Expected: PASS（test_api 中依赖 `depends_on` 的既有断言不受影响）

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/tasks.py tests/test_dag.py
git commit -m "feat: tasks API depends_on 数组/环检测/事件/idea_id 输出"
```

### Task 8: board.summary 依赖阻塞告警

**Files:**
- Modify: `mio_taskhub/api/board.py`
- Test: `tests/test_board_summary.py`

- [ ] **Step 1: Write the failing test (append to test_board_summary.py)**

```python
def test_blocked_dependency_alert():
    parent = _mk("bp", stage="cancelled")
    child = _mk("bc", stage="planning", depends_on=[parent["id"]])
    data = client.get("/api/v1/board/summary").json()
    assert any("依赖阻塞" in a["message"] and child["id"] in a["message"] for a in data["alerts"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_board_summary.py::test_blocked_dependency_alert -q`
Expected: FAIL（无依赖阻塞告警）

- [ ] **Step 3: Update board.py**

在「告警：超截止时间」之后追加依赖阻塞检测：

```python
from mio_taskhub.status import is_terminal, task_deps

    # 告警：依赖阻塞（前置已 cancelled/failed，不可能放行）
    for t in tasks:
        deps = task_deps(t)
        if not deps:
            continue
        stage_v = _stage(t.stage)
        if stage_v in ("done", "cancelled", "review", "implementing"):
            continue
        prereqs = [db.get(Task, d) for d in deps if d]
        blocked = [p for p in prereqs if p is not None and is_terminal(p)
                   and p.state.value != "completed" and _stage(p.stage) != "done"]
        if blocked:
            alerts.append({
                "level": "warning",
                "message": f"任务「{t.title}」依赖阻塞（前置「{blocked[0].title}」已取消/失败），无法放行",
            })
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_board_summary.py -q`
Expected: PASS（原 10 + 新 1）

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/board.py tests/test_board_summary.py
git commit -m "feat: board.summary 依赖阻塞告警"
```

### Task 9: 全量事件埋点（写操作接入 emit_event）

**Files:**
- Modify: `mio_taskhub/api/tasks.py`, `mio_taskhub/api/runs.py`, `mio_taskhub/api/ideas.py`, `mio_taskhub/api/discussions.py`, `mio_taskhub/api/agents.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test (append to test_events.py)**

```python
def test_write_operations_emit_events():
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    c = TestClient(app)
    tid = c.post("/api/v1/tasks", json={"title": "E"}).json()["id"]
    c.post("/api/v1/agents/register", json={"name": "a", "agent_type": "t"})
    claim = c.post("/api/v1/tasks/claim", params={"agent": "a"}).json()
    c.post(f"/api/v1/runs/{claim['id']}/heartbeat", json={"progress": 50})
    c.post(f"/api/v1/runs/{claim['id']}/result", json={"success": True, "result": "ok"})
    iid = c.post("/api/v1/ideas", json={"title": "idea"}).json()["id"]
    c.post("/api/v1/discussions", json={"idea_id": iid, "topic": "t", "conclusions": "c"})
    r = c.get("/api/v1/events", params={"after_seq": 0}).json()
    types = {e["type"] for e in r["events"]}
    assert {"task_created", "task_claimed", "heartbeat", "task_result",
            "idea_created", "discussion_created"} <= types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_events.py::test_write_operations_emit_events -q`
Expected: FAIL（各写操作未 emit）

- [ ] **Step 3: Wire events into each write endpoint**

统一模式：在 `db.commit()` 前 `db.add(emit_event(...))`，提交后 `broadcast_for_event(event)`。逐个端点替换（删除旧 `_broadcast_*` 内联调用）：

**runs.py**（heartbeat / result）：

```python
from mio_taskhub.events import emit_event, broadcast_for_event

@router.post("/{run_id}/heartbeat")
def heartbeat(run_id: str, body: dict = None, db: Session = Depends(get_session)):
    body = body or {}
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404)
    run.state = RunState.RUNNING
    run.last_heartbeat = _now()
    if "progress" in body:
        run.progress = body["progress"]
    if "checkpoint" in body:
        run.checkpoint = body["checkpoint"]
    task = db.get(Task, run.task_id)
    if task and task.state == TaskState.CLAIMED:
        task.state = TaskState.RUNNING
    event = emit_event(db, type="heartbeat", entity="run", entity_id=run.id,
                       run_id=run.id, payload={"progress": run.progress})
    db.add(run)
    if task:
        db.add(task)
    db.commit()
    db.refresh(run)
    broadcast_for_event(event)
    return {"id": run.id, "state": run.state.value, "progress": run.progress}

@router.post("/{run_id}/result")
def submit_result(run_id: str, body: dict, db: Session = Depends(get_session)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404)
    success = body.get("success", True)
    run.result = body.get("result", "")
    run.exit_code = body.get("exit_code", 0 if success else 1)
    run.finished_at = _now()
    run.state = RunState.FINISHED
    run.progress = 100
    task = db.get(Task, run.task_id)
    task_state = None
    if task:
        if success:
            task.state = TaskState.COMPLETED
            task.stage = TaskStage.REVIEW
            task_state = "completed"
        else:
            if task.attempt < task.max_retries:
                task.state = TaskState.RETRYING
                task_state = "retrying"
            else:
                task.state = TaskState.FAILED
                task_state = "failed"
        db.add(task)
    event = emit_event(db, type="task_result", entity="task", entity_id=run.task_id,
                       run_id=run.id, payload={"success": success, "state": task_state})
    db.add(run)
    db.commit()
    db.refresh(run)
    broadcast_for_event(event)
    return {"id": run.id, "state": run.state.value, "result": run.result}
```

注意：`emit_event` 需要 `run_id` 参数 —— 扩展 `events.py::emit_event` 签名（可选）：

```python
def emit_event(db: Session, type: str, entity: str = "", entity_id: str = "",
               run_id: str = "", payload: Optional[dict] = None) -> Event:
    e = Event(type=type, entity=entity, entity_id=entity_id, run_id=run_id,
              payload=json.dumps(payload, ensure_ascii=False) if payload is not None else None)
    db.add(e)
    return e
```

**tasks.py** 其余端点（subtasks/gitref/history/discussions/advance_stage/cancel_task/claim）统一替换为事件模式。示例（claim）：

```python
@router.post("/claim")
def claim_task(agent: str = Query(...), agent_type: str = Query(None),
               project: str = Query(None), workspace: str = Query(None),
               files: str = Query(None), db: Session = Depends(get_session)):
    existing = db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()
    if existing:
        return {"id": existing.id, "task_id": existing.task_id, "state": existing.state.value,
                "agent_name": existing.agent_name}
    q = select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
    if agent_type:
        q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
    rows = db.exec(q.order_by(Task.priority.desc(), Task.created_at.asc())).all()
    now = _now()
    task = None
    for t in rows:
        if t.schedule_type == "once" and t.run_at:
            run_at = t.run_at
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if run_at > now:
                continue
        task = t
        break
    if not task:
        return Response(status_code=204)
    if project and not task.project:
        task.project = project
    if workspace and not task.workspace:
        task.workspace = workspace
    if files and not task.files:
        task.files = [f.strip() for f in files.split(",") if f.strip()]
    run = Run(
        id=str(uuid.uuid4())[:8],
        task_id=task.id,
        agent_name=agent,
        state=RunState.CLAIMED,
        attempt=task.attempt + 1,
        started_at=_now(),
        last_heartbeat=_now(),
    )
    task.state = TaskState.CLAIMED
    task.attempt += 1
    task.stage = TaskStage.IMPLEMENTING
    event = emit_event(db, type="task_claimed", entity="task", entity_id=task.id,
                       run_id=run.id, payload={"agent": agent, "attempt": task.attempt})
    db.add(run)
    db.add(task)
    db.commit()
    db.refresh(run)
    broadcast_for_event(event)
    return {"id": run.id, "task_id": run.task_id, "state": run.state.value,
            "agent_name": run.agent_name, "attempt": run.attempt}
```

**ideas.py**（create / update / set_status）：

```python
from mio_taskhub.events import emit_event, broadcast_for_event
# 将每处 _broadcast_idea(i.id) 替换为：
#     event = emit_event(db, type="idea_created"/"idea_updated"/"idea_status",
#                        entity="idea", entity_id=i.id, payload={...})
#     在 db.commit() 后 broadcast_for_event(event)
```

**discussions.py**（create / add_message / close）：每个端点 emit `discussion_created`/`discussion_message`/`discussion_closed`，entity="discussion"；同时保留对关联 task 的广播。

**agents.py**（register）：emit `agent_registered`，entity="agent"。

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ -q`
Expected: PASS（140 原有 + 新增全部）

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/runs.py mio_taskhub/api/tasks.py mio_taskhub/api/ideas.py mio_taskhub/api/discussions.py mio_taskhub/api/agents.py mio_taskhub/events.py tests/test_events.py
git commit -m "feat: 全量写操作事件埋点（emit_event 统一接入）"
```

### Task 10: Idea → Task 批量拆解

**Files:**
- Modify: `mio_taskhub/api/ideas.py`
- Modify: `mio_taskhub/models.py`（Task.idea_id 已加，无需再动）
- Test: `tests/test_breakdown.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_breakdown.py
import asyncio
import httpx
from mio_taskhub.main import app


def _with_client(coro):
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await coro(c)
    return asyncio.run(_inner())


def test_breakdown_creates_tasks_and_resolves_refs():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "拆我"})).json()["id"]
        r = await c.post(f"/api/v1/ideas/{iid}/breakdown", json={
            "tasks": [
                {"title": "写 spec", "ref": "t1", "depends_on": []},
                {"title": "写 plan", "ref": "t2", "depends_on": ["t1"]},
            ]
        })
        assert r.status_code == 200
        data = r.json()
        assert data["idea"]["status"] == "broken_down"
        assert len(data["tasks"]) == 2
        t2 = next(x for x in data["tasks"] if x["ref"] == "t2")
        t1 = next(x for x in data["tasks"] if x["ref"] == "t1")
        assert t2["depends_on"] == [t1["id"]]
        # 任务详情带 idea_id
        d = await c.get(f"/api/v1/tasks/{t1['id']}")
        assert d.json()["idea_id"] == iid
        # idea 详情返回关联任务
        det = await c.get(f"/api/v1/ideas/{iid}")
        assert len(det.json()["tasks"]) == 2
    _with_client(k)


def test_breakdown_idempotent_409():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "x"})).json()["id"]
        body = {"tasks": [{"title": "a", "depends_on": []}]}
        assert (await c.post(f"/api/v1/ideas/{iid}/breakdown", json=body)).status_code == 200
        assert (await c.post(f"/api/v1/ideas/{iid}/breakdown", json=body)).status_code == 409
    _with_client(k)


def test_breakdown_unknown_ref_422():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "x"})).json()["id"]
        r = await c.post(f"/api/v1/ideas/{iid}/breakdown", json={
            "tasks": [{"title": "a", "depends_on": ["nope"]}]
        })
        assert r.status_code == 422
    _with_client(k)


def test_breakdown_cycle_422_rollback():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "x"})).json()["id"]
        r = await c.post(f"/api/v1/ideas/{iid}/breakdown", json={
            "tasks": [
                {"title": "a", "ref": "a", "depends_on": ["b"]},
                {"title": "b", "ref": "b", "depends_on": ["a"]},
            ]
        })
        assert r.status_code == 422
        # 回滚：无残留任务，idea 仍 formed
        det = await c.get(f"/api/v1/ideas/{iid}")
        assert det.json()["status"] == "new"
        tasks = await c.get("/api/v1/tasks")
        assert all("a" not in t["title"] and "b" not in t["title"] for t in tasks.json())
    _with_client(k)


def test_breakdown_404():
    async def k(c):
        r = await c.post("/api/v1/ideas/nope/breakdown", json={"tasks": [{"title": "a"}]})
        assert r.status_code == 404
    _with_client(k)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_breakdown.py -q`
Expected: FAIL with `404` for `/api/v1/ideas/{id}/breakdown`

- [ ] **Step 3: Update ideas.py**

在 `set_idea_status` 之后追加拆解端点：

```python
from mio_taskhub.status import normalize_depends
from mio_taskhub.planner import detect_cycle
from mio_taskhub.events import emit_event, broadcast_for_event
from mio_taskhub.models import Task, TaskStage


@router.post("/{idea_id}/breakdown")
def breakdown_idea(idea_id: str, body: dict, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    if i.status == IdeaStatus.BROKEN_DOWN:
        raise HTTPException(409, "already broken down")
    items = body.get("tasks", [])
    if not items:
        raise HTTPException(422, "tasks is required")
    # 校验 ref 唯一
    refs = [it.get("ref") or "" for it in items]
    if len(set(refs)) != len(refs):
        raise HTTPException(422, "duplicate ref")
    created = []
    ref2id = {}
    try:
        for it in items:
            title = (it.get("title") or "").strip()
            if not title:
                raise HTTPException(422, "task title is required")
            stage_val = it.get("stage", "brainstorming")
            try:
                stage = TaskStage(stage_val)
            except ValueError:
                raise HTTPException(400, f"invalid stage: {stage_val}")
            t = Task(
                title=title,
                description=it.get("description", ""),
                target_agent_type=it.get("target_agent_type"),
                priority=it.get("priority", 0),
                est_duration_min=it.get("est_duration_min", 30),
                max_retries=it.get("max_retries", 3),
                acceptance_criteria=it.get("acceptance_criteria", ""),
                depends_on=normalize_depends(it.get("depends_on")),
                idea_id=idea_id,
                stage=stage,
            )
            db.add(t)
            created.append(t)
        db.flush()  # 获得自增 id 与 ref2id
        ref2id = {it.get("ref"): t.id for it, t in zip(items, created) if it.get("ref")}
        # 解析 depends_on：ref → real id；未知 ref/id → 422
        for it, t in zip(items, created):
            resolved = []
            for dep in normalize_depends(it.get("depends_on")):
                real = ref2id.get(dep, dep)
                if real not in [x.id for x in created] and db.get(Task, real) is None:
                    raise HTTPException(422, f"unknown dependency ref: {dep}")
                resolved.append(real)
            t.depends_on = resolved
        # 环检测
        graph = {t.id: list(t.depends_on or []) for t in created}
        cyc = detect_cycle(graph)
        if cyc:
            raise HTTPException(422, f"cyclic dependency: {' → '.join(cyc)}")
        i.status = IdeaStatus.BROKEN_DOWN
        i.updated_at = _now()
        idea_event = emit_event(db, type="idea_broken_down", entity="idea", entity_id=idea_id,
                                payload={"action": "broken_down",
                                         "task_ids": [t.id for t in created]})
        task_events = [emit_event(db, type="task_created", entity="task", entity_id=t.id,
                                  payload={"title": t.title, "stage": t.stage.value})
                       for t in created]
        db.add(i)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    broadcast_for_event(idea_event)
    for ev in task_events:
        broadcast_for_event(ev)
    return {
        "idea": _idea_json(db.get(Idea, idea_id)),
        "tasks": [{"id": t.id, "title": t.title, "ref": it.get("ref", ""),
                   "depends_on": list(t.depends_on or [])}
                  for it, t in zip(items, created)],
    }
```

在 `get_idea` 的返回里追加关联任务：

```python
    tasks = db.exec(select(Task).where(Task.idea_id == idea_id).order_by(Task.created_at)).all()
    out["tasks"] = [{"id": t.id, "title": t.title, "stage": t.stage.value,
                     "state": t.state.value} for t in tasks]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_breakdown.py tests/test_ideas_api.py -q`
Expected: PASS（新增 5 + idea 回归）

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/ideas.py tests/test_breakdown.py
git commit -m "feat: Idea→Task 批量拆解（ref 依赖/单事务/幂等）"
```

### Task 11: MCP 工具（poll_events + breakdown_idea）

**Files:**
- Modify: `mio_taskhub/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests (append to test_mcp_server.py)**

```python
def test_poll_events_tool(mcp_ctx):
    _call("taskhub_create_task", {"title": "PE"})
    r = _call("taskhub_poll_events", {"seq": 0})
    assert "next_seq" in r
    assert any(e["type"] == "task_created" for e in r["events"])


def test_poll_events_incremental(mcp_ctx):
    r1 = _call("taskhub_poll_events", {"seq": 0})
    _call("taskhub_create_task", {"title": "PE2"})
    r2 = _call("taskhub_poll_events", {"seq": r1["next_seq"]})
    assert r2["events"]
    assert all(e["seq"] > r1["next_seq"] for e in r2["events"])


def test_breakdown_idea_tool(mcp_ctx):
    created = _call("taskhub_add_idea", {"title": "BI"})
    iid = created["id"]
    r = _call("taskhub_breakdown_idea", {
        "idea_id": iid,
        "tasks": [{"title": "a", "ref": "a", "depends_on": []},
                  {"title": "b", "ref": "b", "depends_on": ["a"]}],
    })
    assert r["idea"]["status"] == "broken_down"
    assert len(r["tasks"]) == 2
    b = next(t for t in r["tasks"] if t["ref"] == "b")
    a = next(t for t in r["tasks"] if t["ref"] == "a")
    assert b["depends_on"] == [a["id"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: FAIL with `Unknown tool: taskhub_poll_events`（或工具不存在）

- [ ] **Step 3: Update mcp_server.py**

在 `taskhub_close_discussion` 之后追加两个工具：

```python
@mcp.tool(name="taskhub_poll_events", title="增量订阅全局事件", annotations={
    "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
async def taskhub_poll_events(
    seq: int = Field(default=0, description="上次消费的 seq；0 表示从头订阅全部；不传则返回最近 200 条"),
) -> str:
    """增量订阅全局变更事件（建任务、领取、心跳、完成、阶段推进、想法、讨论等都会产生）。

    调用后记录返回的 next_seq，下次以其为 seq 即可拿到增量。心跳事件量大，可按需忽略 type=heartbeat。
    """
    params = {"after_seq": seq} if seq else None
    data = await _request("GET", "/events", params=params)
    return _fmt(data)


@mcp.tool(name="taskhub_breakdown_idea", title="把想法拆解为任务集", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_breakdown_idea(
    idea_id: str = Field(description="想法唯一标识", min_length=1),
    tasks: list = Field(description="任务列表，每项含 title/ref/depends_on/priority 等字段"),
) -> str:
    """把一个已成形想法拆解为多个任务，子任务用 ref 互相引用依赖（如 depends_on: [\"t1\"]）。

    成功后想法状态置 broken_down，任务通过 idea_id 关联回该想法。
    """
    body = {"tasks": tasks}
    data = await _request("POST", f"/ideas/{idea_id}/breakdown", body=body)
    return _fmt(data)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: PASS（原有 24 + 新 3）

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP taskhub_poll_events + taskhub_breakdown_idea"
```

### Task 12: 前端（FlowView 依赖角标 + TaskDetail 依赖区块 + IdeasView 拆解表单）

**Files:**
- Modify: `web/src/api.js`
- Modify: `web/src/components/FlowView.jsx`
- Modify: `web/src/components/TaskDetail.jsx`
- Modify: `web/src/components/IdeasView.jsx`
- Test: `web/src/index.css`（少量样式）

- [ ] **Step 1: Update api.js**

```javascript
  listDiscussions: (refType, refId) => req('GET', `/discussions?ref_type=${refType}&ref_id=${refId}`),
  replyDiscussion: (id, body) => req('POST', `/discussions/${id}/messages`, body),
  closeDiscussion: (id, body) => req('POST', `/discussions/${id}/close`, body),
  breakdownIdea: (id, body) => req('POST', `/ideas/${id}/breakdown`, body),
}
```

- [ ] **Step 2: Update FlowView.jsx（依赖角标）**

新增依赖状态工具函数（文件顶部）：

```javascript
const depState = (t, tasks) => {
  const deps = t.depends_on || []
  if (!deps.length) return null
  const map = Object.fromEntries(tasks.map(x => [x.id, x]))
  const blocked = deps.some(d => {
    const p = map[d]
    return p && (p.state === 'cancelled' || p.state === 'failed')
  })
  const done = deps.every(d => {
    const p = map[d]
    return p && (p.state === 'completed' || p.stage === 'done')
  })
  return { count: deps.length, done, blocked }
}
```

在卡片 meta 区（第 117 行 `<span className={...}>{p.label}</span>` 之后）追加角标：

```jsx
                    {(() => {
                      const ds = depState(t, tasks)
                      if (!ds) return null
                      return (
                        <span
                          className={`chip dep-chip${ds.done ? ' dep-chip--ok' : ''}${ds.blocked ? ' dep-chip--blocked' : ''}`}
                          title={ds.blocked ? '前置任务已取消/失败，无法放行' : (ds.done ? '前置任务已完成' : '等待前置任务')}
                        >⛓ {ds.count}</span>
                      )
                    })()}
```

在 index.css 追加：

```css
.dep-chip--ok { border-color: var(--ok, #3ddc97); color: var(--ok, #3ddc97); }
.dep-chip--blocked { border-color: var(--danger, #ff5c5c); color: var(--danger, #ff5c5c); }
```

- [ ] **Step 3: Update TaskDetail.jsx（依赖区块）**

在详情抽屉合适位置（如子任务区块之前）插入：

```jsx
      <div className="detail-sec">
        <h4>依赖</h4>
        {(task.depends_on || []).length === 0 ? (
          <p className="detail-muted">无前置任务</p>
        ) : (
          <ul className="dep-list">
            {task.depends_on.map(did => {
              const dep = (tasks || []).find(x => x.id === did)
              return (
                <li key={did}>
                  <span>{dep ? dep.title : did}</span>
                  <span className="tag">{dep ? (dep.state === 'completed' || dep.stage === 'done' ? '已完成' : dep.state) : '未知'}</span>
                </li>
              )
            })}
          </ul>
        )}
      </div>
```

（`tasks` prop 已由 App 传入 TaskDetail，见 App.jsx 第 228 行。）

- [ ] **Step 4: Update IdeasView.jsx（拆解表单）**

在 detail 的 actions 区，status 为 `formed` 时显示拆解按钮；详情下方显示拆解表单：

```jsx
  const [breaking, setBreaking] = useState(false)
  const [breakRows, setBreakRows] = useState([{ ref: 't1', title: '', deps: '' }])

  const addBreakRow = () =>
    setBreakRows(r => [...r, { ref: `t${r.length + 1}`, title: '', deps: '' }])

  const submitBreakdown = async () => {
    const rows = breakRows.filter(r => r.title.trim())
    if (!rows.length) return
    const tasks = rows.map(r => ({
      title: r.title.trim(),
      ref: r.ref || undefined,
      depends_on: r.deps.split(',').map(s => s.trim()).filter(Boolean),
    }))
    try {
      await api.breakdownIdea(detail.id, { tasks })
      setBreaking(false)
      setBreakRows([{ ref: 't1', title: '', deps: '' }])
      await reloadDetail()
      onReload()
    } catch (e) { fail(e) }
  }
```

actions 区（第 145-151 行）追加：

```jsx
                {detail.status === 'formed' && (
                  <button className="btn btn--accent" onClick={() => setBreaking(b => !b)}>
                    {breaking ? '取消拆解' : '→ 拆解为任务'}
                  </button>
                )}
```

detail 区末尾（discussions 之后）追加拆解表单：

```jsx
              {breaking && (
                <div className="idea-detail__break">
                  <div className="idea-detail__disc-head"><span>拆解为任务（可填依赖 ref）</span></div>
                  {breakRows.map((r, idx) => (
                    <div key={idx} className="break-row">
                      <input className="inp break-row__ref" placeholder="ref" value={r.ref} readOnly />
                      <input className="inp" placeholder="任务标题" value={r.title}
                             onChange={e => setBreakRows(rows => rows.map((x, i) => i === idx ? { ...x, title: e.target.value } : x))} />
                      <input className="inp break-row__deps" placeholder="依赖(逗号分隔)" value={r.deps}
                             onChange={e => setBreakRows(rows => rows.map((x, i) => i === idx ? { ...x, deps: e.target.value } : x))} />
                    </div>
                  ))}
                  <div className="break-actions">
                    <button className="btn btn--ghost" onClick={addBreakRow}>+ 加一行</button>
                    <button className="btn btn--primary" onClick={submitBreakdown}
                            disabled={!breakRows.some(r => r.title.trim())}>提交拆解</button>
                  </div>
                </div>
              )}
```

- [ ] **Step 5: Build frontend**

Run: `cd web && npm run build`
Expected: exit 0，`web/dist/index.html` 生成

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS（140 原有 + 全部新增）

- [ ] **Step 7: Commit**

```bash
git add web/src/api.js web/src/components/FlowView.jsx web/src/components/TaskDetail.jsx web/src/components/IdeasView.jsx web/src/index.css
git commit -m "feat(web): FlowView 依赖角标 + TaskDetail 依赖区块 + IdeasView 拆解表单"
```

### Task 13: 清理测试残留数据（执行 be15742f）

**Files:**
- 无（操作生产库 `~/.mio_taskhub/taskhub.db` 与临时文件）

- [ ] **Step 1: 确认清单**

从看板导出 cancelled 任务，与 spec 白名单比对（644082d3 / a785891f / 81e976fc / 38b588a9 / f48f2c29 / c4a97a5c / c3a236e7）。确认无真实任务混入。

- [ ] **Step 2: 软删残留任务**

对白名单中每个 ID 调用 `DELETE /api/v1/tasks/{id}`（已是 cancelled 的保持终态）。参考命令：

```bash
curl -X DELETE "http://127.0.0.1:48620/api/v1/tasks/644082d3"
```

逐个执行 7 个 ID（或通过 Python 脚本循环）。

- [ ] **Step 3: 清理临时 DB**

```powershell
Get-ChildItem -Path $HOME,$env:TEMP,$PWD -Filter "mio_taskhub_embed_test.db" -ErrorAction SilentlyContinue | Remove-Item -Force
```

确认未误删 `taskhub.db`。

- [ ] **Step 4: 验证**

```bash
curl "http://127.0.0.1:48620/api/v1/board/summary"
```

确认 cancelled 计数下降、ready/done 真实任务（如 `1eb934d6` 生命周期E2E-v2）仍在。

- [ ] **Step 5: 回写任务记录**

在 `be15742f` 的 discussion 追加执行记录（结论：已清理，列出处理 ID）。

- [ ] **Step 6: 提交任务结果**

调用 `taskhub_submit_result`，success=true，result 摘要清理内容。

### Task 14: 全量回归 + 打包验证

- [ ] **Step 1: Run full backend tests**

Run: `python -m pytest tests/ -q`
Expected: PASS（全部）

- [ ] **Step 2: Build frontend**

Run: `cd web && npm run build`
Expected: exit 0

- [ ] **Step 3: 手工验证（可选，有 hub 时）**

- 浏览器打开 `http://127.0.0.1:48620/` → Flow 视图看依赖角标
- 创建有依赖任务，手动完成前置 → 观察调度器 30s 内放行
- `curl "http://127.0.0.1:48620/api/v1/events?after_seq=0"` 看到事件序列

- [ ] **Step 4: 更新文档（README / MCP_INTEGRATION）**

在 `MCP_INTEGRATION.md` 工具表追加 `taskhub_poll_events` / `taskhub_breakdown_idea`；`README.md` 功能列表追加编排/事件/拆解。

- [ ] **Step 5: Commit**

```bash
git add MCP_INTEGRATION.md README.md
git commit -m "docs: 编排/事件订阅/Idea拆解 使用说明"
```
