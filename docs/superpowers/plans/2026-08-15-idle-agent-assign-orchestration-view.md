# 空闲 agent 自动捞取 + 编排视图增强 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 hub 主动分配任务给空闲 agent（Task-first 调度 + 原子领取），并增强前端编排视图（FlowView 拖拽换阶段 + 依赖连线 + 新拓扑视图）。

**Architecture:** 把 claim 核心逻辑抽成 `_claim_for(agent, db)` 纯函数，用条件更新（SQLite 不支持 FOR UPDATE）保证一个 task 只被一个 run 领取；调度器按 Task-first 顺序把 ready 任务分配给空闲 agent。前端新增 `move_to_stage` 接口支持拖拽任意跳转，FlowView 加依赖连线（observer 驱动重绘），新增 TopoView 拓扑视图。

**Tech Stack:** Python 3.12 / FastAPI / SQLModel / SQLite / React 18 / Vite / pytest

---

### Task 1: 抽取 `_claim_for` 纯函数 + claim API 原子化

**Files:**
- Modify: `mio_taskhub/api/tasks.py`
- Test: `tests/test_api.py`, `tests/test_assign.py`（新建）

- [ ] **Step 1: Write the failing tests** — create `tests/test_assign.py`:

```python
# tests/test_assign.py
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, Run, RunState, TaskState, TaskStage
from mio_taskhub.api.tasks import _claim_for

client = TestClient(app)


def _mk(title, stage="ready", agent_type=None, **kw):
    body = {"title": title, "stage": stage}
    if agent_type:
        body["target_agent_type"] = agent_type
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


def _register(name, agent_type="t"):
    return client.post("/api/v1/agents/register", json={"name": name, "agent_type": agent_type}).json()


def _runs_for(task_id):
    with Session(engine) as s:
        return s.exec(select(Run).where(Run.task_id == task_id)).all()


def test_claim_for_idempotent_returns_existing_run():
    _register("a1")
    _mk("t1", stage="ready")
    with Session(engine) as s:
        run = _claim_for("a1", s)
        assert run is not None
        # 第二次调用返回同一个 run
        run2 = _claim_for("a1", s)
        assert run2 is not None and run2.id == run.id


def test_claim_for_none_when_no_task():
    _register("a2")
    with Session(engine) as s:
        assert _claim_for("a2", s) is None


def test_claim_for_respects_agent_type():
    _register("coder1", "coder")
    _mk("ct", stage="ready", agent_type="coder")
    _mk("devt", stage="ready", agent_type="dev")
    with Session(engine) as s:
        run = _claim_for("coder1", s)
        assert run is not None
        task = s.get(Task, run.task_id)
        assert task.target_agent_type == "coder"


def test_claim_for_does_not_create_duplicate_runs_for_task():
    _register("x1")
    _register("x2")
    _mk("dup", stage="ready")
    with Session(engine) as s:
        r1 = _claim_for("x1", s)
        r2 = _claim_for("x2", s)
        runs = s.exec(select(Run).where(Run.task_id == r1.task_id)).all()
        assert len(runs) == 1  # 一个 task 只能一个 run
        assert r2 is None or r2.task_id != r1.task_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_assign.py -q`
Expected: FAIL with `ImportError: cannot import name '_claim_for'`

- [ ] **Step 3: Refactor tasks.py — extract `_claim_for`**

Read `mio_taskhub/api/tasks.py` fully first. Extract the claim logic from `claim_task` (lines 345-399) into a module-level function `_claim_for(agent: str, db: Session)`, using conditional update for atomicity. Place it before `claim_task`:

```python
def _claim_for(agent: str, db: Session):
    """原子领取：返回该 agent 的 Run 或 None。

    先查 agent 已有 claimed/running run（幂等）；否则按优先级+FIFO 找匹配任务，
    用条件更新（WHERE state='queued'）抢占，避免 SQLite 无 FOR UPDATE 下的并发双 run。
    """
    existing = db.exec(
        select(Run).where(Run.agent_name == agent, Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
    ).first()
    if existing:
        return existing
    q = select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
    rows = db.exec(q.order_by(Task.priority.desc(), Task.created_at.asc())).all()
    now = _now()
    candidate = None
    for t in rows:
        if t.schedule_type == "once" and t.run_at:
            run_at = t.run_at
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if run_at > now:
                continue
        candidate = t
        break
    if not candidate:
        return None
    # 条件更新抢占（原子）：只有 state 仍是 queued 才算抢到
    from sqlalchemy import update as sa_update
    res = db.exec(
        sa_update(Task)
        .where(Task.id == candidate.id, Task.state == TaskState.QUEUED)
        .values(state=TaskState.CLAIMED)
    )
    if res.rowcount != 1:
        db.rollback()
        return None  # 已被并发领取，跳过
    task = db.get(Task, candidate.id)
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
    db.add(run)
    return run
```

注意：这个函数**不 commit**——调用方负责 commit + broadcast。`claim_task` 端点改为调它并补事件：

```python
@router.post("/claim")
def claim_task(agent: str = Query(...), agent_type: str = Query(None),
               project: str = Query(None), workspace: str = Query(None),
               files: str = Query(None), db: Session = Depends(get_session)):
    run = _claim_for(agent, db)
    if run is None:
        db.rollback()
        return Response(status_code=204)
    task = db.get(Task, run.task_id)
    if project and not task.project:
        task.project = project
    if workspace and not task.workspace:
        task.workspace = workspace
    if files and not task.files:
        task.files = [f.strip() for f in files.split(",") if f.strip()]
    event = emit_event(db, type="task_claimed", entity="task", entity_id=task.id,
                       run_id=run.id, payload={"agent": agent, "attempt": task.attempt})
    db.add(task)
    db.commit()
    db.refresh(run)
    broadcast_for_event(event)
    return {"id": run.id, "task_id": run.task_id, "state": run.state.value,
            "agent_name": run.agent_name, "attempt": run.attempt}
```

注意：条件更新用了 `sa_update` 直更新 state，之后 `db.get(Task, candidate.id)` 拿回对象时，SQLAlchemy 可能缓存旧值——需 `db.refresh(task)` 或用 `db.get` 后确认。若遇到 `attempt`/`state` 未同步问题，在条件更新后对 task 做 `db.refresh(task)`。保留原有的 context 注入逻辑（project/workspace/files）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_assign.py tests/test_api.py -q`
Expected: PASS（test_assign 4 项 + test_api 回归，含 `test_claim_task_creates_run` 等既有用例）

- [ ] **Step 5: 并发原子性测试**

Append to `tests/test_assign.py`:

```python
def test_concurrent_claim_single_run():
    """模拟两个 agent 并发抢同一任务：条件更新保证只有一个 run。"""
    _register("c1")
    _register("c2")
    _mk("race", stage="ready")
    with Session(engine) as s:
        r1 = _claim_for("c1", s)
        s.commit()
    with Session(engine) as s:
        r2 = _claim_for("c2", s)
        s.commit()
    # c2 抢不到（任务已被 c1 领取），r2 应为 None 或换任务
    runs = _runs_for(r1.task_id)
    assert len(runs) == 1
```

Run: `python -m pytest tests/test_assign.py -q` — Expected PASS (5)

- [ ] **Step 6: Full regression**

Run: `python -m pytest tests/ -q` — Expected PASS (190 + 5)

- [ ] **Step 7: Commit**

```bash
git add mio_taskhub/api/tasks.py tests/test_assign.py
git commit -m "feat: _claim_for 纯函数 + 条件更新原子领取"
```

### Task 2: 调度器 idle agent 自动分配（Task-first）

**Files:**
- Modify: `mio_taskhub/wiring.py`
- Test: `tests/test_assign.py`

- [ ] **Step 1: Write the failing tests (append to test_assign.py)**

```python
import mio_taskhub.wiring as wiring


def _agents():
    from mio_taskhub.models import Agent
    with Session(engine) as s:
        return s.exec(select(Agent)).all()


def _online(name):
    return [a for a in _agents() if a.name == name][0].status.value


def test_assign_to_idle_agent():
    _register("idle1", "coder")
    _mk("at", stage="ready", agent_type="coder")
    wiring._assign_to_idle_agents()
    with Session(engine) as s:
        task = s.exec(select(Task).where(Task.title == "at")).first()
        assert task.state == TaskState.CLAIMED
        assert task.stage == TaskStage.IMPLEMENTING
        run = s.exec(select(Run).where(Run.task_id == task.id)).first()
        assert run is not None and run.agent_name == "idle1"


def test_busy_agent_not_assigned():
    _register("busy1", "coder")
    _mk("b1", stage="ready", agent_type="coder")
    # 先让 busy1 占一个 run
    with Session(engine) as s:
        _claim_for("busy1", s)
        s.commit()
    _mk("b2", stage="ready", agent_type="coder")
    wiring._assign_to_idle_agents()
    with Session(engine) as s:
        t2 = s.exec(select(Task).where(Task.title == "b2")).first()
        assert t2.state == TaskState.QUEUED  # busy agent 不再被分配


def test_task_first_assigns_later_registered_agent():
    _register("late1", "coder")
    _mk("lt", stage="ready", agent_type="coder")
    wiring._assign_to_idle_agents()
    with Session(engine) as s:
        task = s.exec(select(Task).where(Task.title == "lt")).first()
        assert task.state == TaskState.CLAIMED


def test_assign_writes_task_assigned_event():
    _register("ev1", "coder")
    _mk("et", stage="ready", agent_type="coder")
    wiring._assign_to_idle_agents()
    ev = client.get("/api/v1/events", params={"after_seq": 0}).json()
    assigned = [e for e in ev["events"] if e["type"] == "task_assigned"]
    assert assigned, "expected task_assigned event"
    assert "run_id" in assigned[0]["payload"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_assign.py -q`
Expected: FAIL with `AttributeError: module 'mio_taskhub.wiring' has no attribute '_assign_to_idle_agents'`

- [ ] **Step 3: Update wiring.py**

Read `mio_taskhub/wiring.py` fully first. Add `_assign_to_idle_agents` and call it in `_get_due_tasks` after `_release_dependencies`:

```python
from mio_taskhub.api.tasks import _claim_for
from mio_taskhub.models import Agent, AgentStatus


def _assign_to_idle_agents():
    """Task-first 调度：把 ready 待领任务按优先级分配给空闲在线 agent。

    先排任务（priority desc, created_at asc），逐任务找匹配空闲 agent，
    agent 分到任务后即标记忙（一 tick 一个 agent 最多一单）。
    """
    with Session(engine) as db:
        ready = db.exec(
            select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
            .order_by(Task.priority.desc(), Task.created_at.asc())
        ).all()
        now = datetime.now(timezone.utc)
        for t in ready:
            if t.schedule_type == "once" and t.run_at:
                run_at = t.run_at
                if run_at.tzinfo is None:
                    run_at = run_at.replace(tzinfo=timezone.utc)
                if run_at > now:
                    continue
            # 找匹配的空闲 agent
            agents = db.exec(select(Agent).where(Agent.status == AgentStatus.ONLINE)).all()
            target = None
            for a in agents:
                if a.agent_type and t.target_agent_type and a.agent_type != t.target_agent_type:
                    continue
                busy = db.exec(
                    select(Run).where(Run.agent_name == a.name,
                                      Run.state.in_([RunState.CLAIMED, RunState.RUNNING]))
                ).first()
                if busy:
                    continue
                target = a
                break
            if target is None:
                continue
            run = _claim_for(target.name, db)
            if run is None:
                db.rollback()
                continue
            task = db.get(Task, run.task_id)
            event = emit_event(db, type="task_assigned", entity="task", entity_id=task.id,
                               run_id=run.id, payload={"agent": target.name, "reason": "idle_assign"})
            db.add(task)
            db.commit()
            broadcast_for_event(event)
```

在 `_get_due_tasks` 开头加：

```python
def _get_due_tasks():
    _release_dependencies()          # 先放行依赖
    _assign_to_idle_agents()         # 再分配空闲 agent
    now = datetime.now(timezone.utc)
    # ...（其余原逻辑不变）...
```

注意：`_claim_for` 内部做条件更新，`_assign_to_idle_agents` 里分配成功后才 commit。要避免 `task` 对象过期问题——`db.get(Task, run.task_id)` 后 `emit_event` 用它，随后 `db.add(task)` + `commit` 应正常。若 `task` 是条件更新前的旧实例，加 `db.refresh(task)`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_assign.py -q`
Expected: PASS（Task 1 的 5 + 本次 4 = 9）

- [ ] **Step 5: Full regression**

Run: `python -m pytest tests/ -q` — Expected PASS（190 + 9）

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/wiring.py tests/test_assign.py
git commit -m "feat: 调度器 Task-first 空闲 agent 自动分配"
```

### Task 3: `move_to_stage` API（任意跳转 + 产出物校验）

**Files:**
- Modify: `mio_taskhub/api/tasks.py`
- Test: `tests/test_stage_move.py`（新建）

- [ ] **Step 1: Write the failing tests** — create `tests/test_stage_move.py`:

```python
# tests/test_stage_move.py
from fastapi.testclient import TestClient
from mio_taskhub.main import app

client = TestClient(app)


def _mk(title, stage="brainstorming", **kw):
    body = {"title": title, "stage": stage}
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


def test_move_to_any_stage():
    t = _mk("move1", stage="brainstorming")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "review"})
    assert r.status_code == 200
    assert r.json()["stage"] == "review"


def test_move_backwards():
    t = _mk("move2", stage="implementing")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "brainstorming"})
    assert r.status_code == 200
    assert r.json()["stage"] == "brainstorming"


def test_move_to_done_requires_review_result():
    t = _mk("move3", stage="review")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "done"})
    assert r.status_code == 422
    r2 = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                     json={"target_stage": "done", "review_result": "ok"})
    assert r2.status_code == 200
    assert r2.json()["state"] == "completed"


def test_move_to_design_requires_spec_path():
    t = _mk("move4", stage="brainstorming")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "design"})
    assert r.status_code == 422
    r2 = client.post(f"/api/v1/tasks/{t['id']}/stage/move",
                     json={"target_stage": "design", "spec_path": "docs/x.md"})
    assert r2.status_code == 200


def test_move_terminal_stage_blocked():
    t = _mk("move5", stage="done")
    r = client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "review"})
    assert r.status_code == 400


def test_move_writes_event():
    t = _mk("move6", stage="brainstorming")
    client.post(f"/api/v1/tasks/{t['id']}/stage/move", json={"target_stage": "planning"})
    ev = client.get("/api/v1/events", params={"after_seq": 0}).json()
    moved = [e for e in ev["events"] if e["type"] == "task_moved"]
    assert moved and moved[-1]["payload"]["from"] == "brainstorming"
    assert moved[-1]["payload"]["to"] == "planning"


def test_move_404():
    r = client.post("/api/v1/tasks/nope/stage/move", json={"target_stage": "ready"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_stage_move.py -q`
Expected: FAIL with `404` for `/api/v1/tasks/{id}/stage/move`

- [ ] **Step 3: Update tasks.py — add move_to_stage endpoint**

Read `mio_taskhub/api/tasks.py`. Add the endpoint after `advance_stage` (around line 343):

```python
@router.post("/{task_id}/stage/move")
def move_to_stage(task_id: str, body: dict, db: Session = Depends(get_session)):
    """任意跳转到目标阶段（拖拽用）。不校验相邻性，但保留终态保护与产出物校验。"""
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    target = body.get("target_stage")
    if not target:
        raise HTTPException(422, "target_stage is required")
    try:
        dst = TaskStage(target)
    except ValueError:
        raise HTTPException(400, f"invalid target_stage: {target}")
    src = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
    if src in (TaskStage.DONE, TaskStage.CANCELLED):
        raise HTTPException(400, f"cannot move terminal stage {src.value}")
    if dst == TaskStage.CANCELLED and src == TaskStage.DONE:
        raise HTTPException(400, "cannot cancel a done task")
    if dst == TaskStage.DESIGN:
        if not body.get("spec_path"):
            raise HTTPException(422, "design stage requires spec_path")
        t.spec_path = body["spec_path"]
    if dst == TaskStage.PLANNING:
        if not body.get("plan_path"):
            raise HTTPException(422, "planning stage requires plan_path")
        t.plan_path = body["plan_path"]
    if dst == TaskStage.DONE:
        if not body.get("review_result"):
            raise HTTPException(422, "done stage requires review_result")
        t.review_result = body["review_result"]
        t.state = TaskState.COMPLETED
    if dst == TaskStage.CANCELLED:
        t.state = TaskState.CANCELLED
    from_stage = src.value
    t.stage = dst
    event = emit_event(db, type="task_moved", entity="task", entity_id=t.id,
                       payload={"from": from_stage, "to": dst.value})
    db.add(t)
    db.commit()
    db.refresh(t)
    broadcast_for_event(event)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "review_result": t.review_result,
            "state": t.state.value}
```

注意：若 move 到 `implementing`/`review` 等中间阶段且 task 之前是 queued，不需要改 state（保持原状态即可，除非目标为 done/cancelled）。若 move 把任务从 done 挪走（前面已拦截）或从 queued 挪到非 ready 阶段，state 维持现状。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_stage_move.py -q`
Expected: PASS (7)

- [ ] **Step 5: Full regression**

Run: `python -m pytest tests/ -q` — Expected PASS（190 + 7 + 前面 Task 1/2 的测试）

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/api/tasks.py tests/test_stage_move.py
git commit -m "feat: move_to_stage 任意跳转 API（拖拽用，产出物校验+终态保护）"
```

### Task 4: MCP 工具更新（claim 描述 + move_to_stage）

**Files:**
- Modify: `mio_taskhub/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test (append to test_mcp_server.py)**

```python
def test_move_to_stage_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "MV", "stage": "brainstorming"})
    r = _call("taskhub_move_to_stage", {"task_id": created["id"], "target_stage": "review"})
    assert r["stage"] == "review"
    d = _call("taskhub_get_task", {"task_id": created["id"]})
    assert d["stage"] == "review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_server.py::test_move_to_stage_tool -q`
Expected: FAIL with `ToolError: Unknown tool: taskhub_move_to_stage`

- [ ] **Step 3: Update mcp_server.py**

Read `mio_taskhub/mcp_server.py`. Update `taskhub_claim` description (line ~139) to mention hub auto-assignment:

```python
    """按优先级 + FIFO 领取一个排队任务，并返回 Run 上下文（含任务详情）。

    若该 agent 已有进行中的 run，会返回同一个 run（幂等）。hub 调度器可能已
    为你自动分配任务（task_assigned 事件），调用本工具会优先返回已分配 run。
    无可用任务时返回空结果。
    """
```

Add a new tool after `taskhub_advance_stage` (around line 501):

```python
@mcp.tool(name="taskhub_move_to_stage", title="任意移动到目标阶段", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_move_to_stage(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    target_stage: str = Field(description="目标阶段：brainstorming/design/planning/ready/implementing/review/done/cancelled"),
    spec_path: Optional[str] = Field(default=None, description="设计文档路径（目标为 design 时必填）"),
    plan_path: Optional[str] = Field(default=None, description="计划文档路径（目标为 planning 时必填）"),
    review_result: Optional[str] = Field(default=None, description="审查结论（目标为 done 时必填）"),
) -> str:
    """任意跳转到目标阶段（不要求相邻），保留终态保护与产出物校验。

    与 advance_stage 的区别：advance_stage 只允许相邻推进 + 回溯；
    move_to_stage 用于拖拽等自由移动场景。
    """
    body = {"target_stage": target_stage}
    if spec_path is not None: body["spec_path"] = spec_path
    if plan_path is not None: body["plan_path"] = plan_path
    if review_result is not None: body["review_result"] = review_result
    data = await _request("POST", f"/tasks/{task_id}/stage/move", body=body)
    return _fmt(data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: PASS（26 + 1 = 27）

- [ ] **Step 5: Full regression**

Run: `python -m pytest tests/ -q` — Expected PASS

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP taskhub_move_to_stage + claim 描述更新"
```

### Task 5: 前端 api.js moveToStage + FlowView 拖拽换阶段

**Files:**
- Modify: `web/src/api.js`
- Modify: `web/src/components/FlowView.jsx`
- Modify: `web/src/index.css`

- [ ] **Step 1: Update api.js**

Add after `advanceStage`:

```javascript
  moveToStage: (id, body) => req('POST', `/tasks/${id}/stage/move`, body),
```

- [ ] **Step 2: Update FlowView.jsx — drag & drop**

Read `web/src/components/FlowView.jsx` fully first. Add drag state and handlers:

```jsx
  const [draggingId, setDraggingId] = useState(null)

  const onCardDragStart = (e, id) => {
    setDraggingId(id)
    e.dataTransfer.setData('text/plain', id)
    e.dataTransfer.effectAllowed = 'move'
  }

  const onColumnDrop = async (stageId) => {
    if (!draggingId) return
    const task = tasks.find(t => t.id === draggingId)
    setDraggingId(null)
    if (!task || task.stage === stageId) return
    // 产出物缺失时先提示
    const body = { target_stage: stageId }
    if (stageId === 'design') {
      const val = window.prompt(`移动到 design 需提供 Spec 路径：`, task.spec_path || '')
      if (val === null) return
      body.spec_path = val
    } else if (stageId === 'planning') {
      const val = window.prompt(`移动到 planning 需提供 Plan 路径：`, task.plan_path || '')
      if (val === null) return
      body.plan_path = val
    } else if (stageId === 'done') {
      const val = window.prompt(`移动到 done 需提供审查结论：`, task.review_result || '')
      if (val === null) return
      body.review_result = val
    }
    try {
      await onMoveToStage(task.id, body)
    } catch (e) {
      alert('移动失败: ' + (e.message || '阶段不合法'))
    }
  }
```

Add `onMoveToStage` prop. In FlowView signature:

```jsx
export default function FlowView({ tasks, onOpen, onCancel, onAdvance, onMoveToStage }) {
```

In each stage column, add drag handlers. Find the stage node buttons (`flow__step` / `flow-node`) — make them drop targets. The cards need `draggable`:

```jsx
                <div
                  key={s.id}
                  className="flow__step"
                  onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }}
                  onDrop={e => { e.preventDefault(); onColumnDrop(s.id) }}
                >
```

And the card (find `className="flow-card"` div) add:

```jsx
                  draggable
                  onDragStart={e => onCardDragStart(e, t.id)}
```

- [ ] **Step 3: Update App.jsx — pass onMoveToStage**

Read `web/src/App.jsx`. Add a handler and pass it to FlowView:

```jsx
  const moveToStage = async (id, body) => {
    try { await api.moveToStage(id, body); loadTasks() }
    catch (e) { setError('移动阶段失败: ' + (e.message || '阶段不合法')) }
  }
```

And in the FlowView render:

```jsx
            {view === 'flow' && (
              <FlowView tasks={tasks} onOpen={openTask} onCancel={cancelTask}
                        onAdvance={advanceStage} onMoveToStage={moveToStage} />
            )}
```

- [ ] **Step 4: Add CSS for draggable feedback**

Append to `web/src/index.css`:

```css
.flow-card[draggable] { cursor: grab; }
.flow-card[draggable]:active { cursor: grabbing; }
.flow__step.is-dragover { outline: 1px dashed var(--accent, #3ddc97); outline-offset: -2px; }
```

- [ ] **Step 5: Build + regression**

Run: `cd web && npm run build` — exit 0
Run: `python -m pytest tests/ -q` — PASS

- [ ] **Step 6: Commit**

```bash
git add web/src/api.js web/src/components/FlowView.jsx web/src/App.jsx web/src/index.css
git commit -m "feat(web): FlowView 拖拽换阶段（move_to_stage）"
```

### Task 6: FlowView 依赖连线（SVG + observer 重绘）

**Files:**
- Modify: `web/src/components/FlowView.jsx`
- Modify: `web/src/index.css`

- [ ] **Step 1: Implement dependency edges in FlowView**

Read current `web/src/components/FlowView.jsx`. Add an SVG overlay inside the expanded stage panel. The panel shows `activeTasks`; compute edges among them via `depends_on`:

```jsx
  const [edges, setEdges] = useState([])
  const panelRef = useRef(null)

  const computeEdges = useCallback(() => {
    if (!panelRef.current || expanded === null) { setEdges([]); return }
    const panel = panelRef.current
    const ids = activeTasks.map(t => t.id)
    const cards = panel.querySelectorAll('.flow-card')
    const rects = {}
    cards.forEach(c => {
      const id = c.getAttribute('data-task-id')
      if (id) rects[id] = c.getBoundingClientRect()
    })
    const panelRect = panel.getBoundingClientRect()
    const out = []
    activeTasks.forEach(t => {
      ;(t.depends_on || []).forEach(depId => {
        if (!rects[t.id] || !rects[depId] || !ids.includes(depId)) return
        const a = rects[t.id]
        const b = rects[depId]
        out.push({
          id: `${depId}->${t.id}`,
          x1: b.right - panelRect.left, y1: b.top + b.height / 2 - panelRect.top,
          x2: a.left - panelRect.left, y2: a.top + a.height / 2 - panelRect.top,
          hover: false,
        })
      })
    })
    setEdges(out)
  }, [activeTasks, expanded])

  useEffect(() => {
    computeEdges()
    if (!panelRef.current) return
    const ro = new ResizeObserver(() => requestAnimationFrame(computeEdges))
    ro.observe(panelRef.current)
    const mo = new MutationObserver(() => requestAnimationFrame(computeEdges))
    mo.observe(panelRef.current, { childList: true, subtree: true })
    return () => { ro.disconnect(); mo.disconnect() }
  }, [computeEdges, expanded])
```

Add imports: `useRef`, `useCallback`. Add `data-task-id` to each card:

```jsx
                <div key={t.id} className="flow-card" data-task-id={t.id} ...>
```

Add SVG overlay inside the panel (before the cards list):

```jsx
            <svg className="flow__edges" aria-hidden="true">
              {edges.map(e => (
                <line key={e.id} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
                      className="flow__edge" />
              ))}
            </svg>
```

Note: the panel is `flow__panel` — it needs `position: relative` and the SVG `position: absolute; pointer-events: none`. The cards must be above the SVG.

- [ ] **Step 2: Add CSS**

Append to `web/src/index.css`:

```css
.flow__panel { position: relative; }
.flow__edges { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 1; }
.flow__edge { stroke: var(--ink-dim, #6b7280); stroke-width: 1.5; opacity: 0.5; }
.flow-card { position: relative; z-index: 2; }
```

- [ ] **Step 3: Build + verify**

Run: `cd web && npm run build` — exit 0
Run: `python -m pytest tests/ -q` — PASS

- [ ] **Step 4: Commit**

```bash
git add web/src/components/FlowView.jsx web/src/index.css
git commit -m "feat(web): FlowView 依赖连线（SVG + observer 重绘）"
```

### Task 7: TopoView 拓扑视图

**Files:**
- Create: `web/src/components/TopoView.jsx`
- Modify: `web/src/App.jsx`
- Modify: `web/src/components/Rail.jsx`
- Modify: `web/src/index.css`

- [ ] **Step 1: Create TopoView.jsx**

```jsx
import { useMemo } from 'react'
import { prio, fmtDur } from '../constants'

const STAGE_TONE = {
  done: 'ok', cancelled: 'dim', review: 'live',
  implementing: 'live', ready: 'dim', planning: 'dim',
  design: 'dim', brainstorming: 'dim',
}

function kahnLayers(tasks) {
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
  const deps = Object.fromEntries(tasks.map(t => [t.id, t.depends_on || []]))
  const indegree = {}
  const outdegree = {}
  tasks.forEach(t => { indegree[t.id] = 0; outdegree[t.id] = 0 })
  tasks.forEach(t => {
    ;(t.depends_on || []).forEach(d => {
      if (!byId[d]) return
      indegree[t.id] += 1
      outdegree[d] += 1
    })
  })
  const depth = {}
  const layers = []
  const remaining = new Set(tasks.map(t => t.id))
  let frontier = tasks.filter(t => (indegree[t.id] || 0) === 0)
  let d = 0
  while (frontier.length) {
    const next = []
    frontier.forEach(t => { depth[t.id] = d })
    layers.push(frontier)
    frontier.forEach(t => {
      ;(deps[t.id] || []).forEach(dep => {
        if (!byId[dep]) return
        indegree[dep] -= 1
        if (indegree[dep] === 0 && remaining.has(dep)) {
          next.push(byId[dep])
          remaining.delete(dep)
        }
      })
    })
    frontier = next
    d += 1
  }
  return { layers, meta: { depth, indegree, outdegree } }
}

export default function TopoView({ tasks, onOpen }) {
  const { layers, meta } = useMemo(() => kahnLayers(tasks), [tasks])
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
  const isBlocked = (t) =>
    (t.depends_on || []).some(d => {
      const dep = byId[d]
      return dep && (dep.state === 'cancelled' || dep.state === 'failed')
    })

  return (
    <div className="topo">
      <div className="topo__head">
        <h2 className="topo__title">依赖拓扑</h2>
        <span className="topo__count">{tasks.length} 个任务 · {layers.length} 层</span>
      </div>
      {layers.length === 0 && <div className="topo__empty">还没有任务。</div>}
      {layers.map((layer, i) => (
        <div key={i} className="topo__layer">
          <div className="topo__layer-tag">L{i + 1}</div>
          <div className="topo__layer-nodes">
            {layer.map(t => {
              const p = prio(t.priority)
              const blocked = isBlocked(t)
              const tone = blocked ? 'danger' : (STAGE_TONE[t.stage] || 'dim')
              return (
                <button key={t.id} className={`topo-node topo-node--${tone}`}
                  role="button" tabIndex={0}
                  aria-label={`任务 ${t.title}，阶段 ${t.stage}。回车查看详情`}
                  onClick={() => onOpen && onOpen(t)}
                  onKeyDown={e => { if (e.key === 'Enter' && onOpen) onOpen(t) }}>
                  <div className="topo-node__title">{t.title}</div>
                  <div className="topo-node__meta">
                    <span className={`chip${p.p >= 3 ? ' chip--p3' : ''}${p.p === 2 ? ' chip--p2' : ''}`}>{p.label}</span>
                    <span className="topo-node__stage">{t.stage}</span>
                  </div>
                  <div className="topo-node__stats">
                    <span>{fmtDur(t.est_duration_min)}</span>
                    <span>{blocked ? '阻塞' : `↓${meta.outdegree[t.id] || 0} ↑${meta.indegree[t.id] || 0}`}</span>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Update App.jsx**

Import TopoView and add view case:

```jsx
import TopoView from './components/TopoView'
...
            {view === 'topo' && (
              <TopoView tasks={tasks} onOpen={openTask} />
            )}
```

- [ ] **Step 3: Update Rail.jsx — add 拓扑 entry**

Add to VIEWS array (after `flow`):

```jsx
  { id: 'topo', label: '拓扑', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="5" cy="5" r="2" />
      <circle cx="19" cy="5" r="2" />
      <circle cx="12" cy="19" r="2" />
      <path d="M7 6.5 17 5M6 7l4 10M18 7l-4 10" />
    </svg>
  )},
```

- [ ] **Step 4: Add CSS**

Append to `web/src/index.css`:

```css
.topo { padding: 24px; display: flex; flex-direction: column; gap: 16px; overflow: auto; }
.topo__head { display: flex; align-items: baseline; gap: 12px; }
.topo__title { font-family: var(--font-display, inherit); margin: 0; }
.topo__count { color: var(--ink-dim, #6b7280); font-size: 12px; }
.topo__empty { color: var(--ink-dim, #6b7280); padding: 40px 0; text-align: center; }
.topo__layer { display: flex; gap: 12px; align-items: flex-start; }
.topo__layer-tag { font-family: var(--font-mono, monospace); font-size: 11px; color: var(--ink-dim, #6b7280); padding-top: 10px; min-width: 32px; }
.topo__layer-nodes { display: flex; gap: 12px; flex-wrap: wrap; flex: 1; }
.topo-node { text-align: left; background: var(--bg-soft, #181b20); border: 1px solid var(--border, #2a2e35); border-radius: 10px; padding: 10px 12px; min-width: 180px; max-width: 240px; cursor: pointer; }
.topo-node--ok { border-color: var(--ok, #3ddc97); }
.topo-node--danger { border-color: var(--danger, #ff5c5c); }
.topo-node--live { border-color: var(--accent, #3ddc97); }
.topo-node__title { font-weight: 600; margin-bottom: 6px; }
.topo-node__meta { display: flex; gap: 6px; align-items: center; }
.topo-node__stage { font-size: 11px; color: var(--ink-dim, #6b7280); }
.topo-node__stats { display: flex; justify-content: space-between; font-size: 11px; color: var(--ink-dim, #6b7280); margin-top: 6px; }
```

- [ ] **Step 5: Build + regression**

Run: `cd web && npm run build` — exit 0
Run: `python -m pytest tests/ -q` — PASS

- [ ] **Step 6: Commit**

```bash
git add web/src/components/TopoView.jsx web/src/App.jsx web/src/components/Rail.jsx web/src/index.css
git commit -m "feat(web): TopoView 依赖拓扑视图"
```

### Task 8: 回归 + 文档 + 记忆

- [ ] **Step 1: Full backend regression**

Run: `python -m pytest tests/ -q` — Expected PASS（约 200+）

- [ ] **Step 2: Frontend build**

Run: `cd web && npm run build` — exit 0

- [ ] **Step 3: Update MCP_INTEGRATION.md**

在工具表加 `taskhub_move_to_stage`；在「自动捞取」相关说明补一句：hub 调度器会自动把任务分配给空闲在线 agent，agent 调用 `taskhub_claim` 可优先拿到已分配 run。

- [ ] **Step 4: Update memory docs**

`docs/memory-workbench-p0-2026-08-15.md` 追加：idle agent 自动捞取 + 编排视图增强完成记录。

- [ ] **Step 5: Commit**

```bash
git add MCP_INTEGRATION.md docs/memory-workbench-p0-2026-08-15.md
git commit -m "docs: idle agent 自动捞取 + 编排视图增强说明"
```
