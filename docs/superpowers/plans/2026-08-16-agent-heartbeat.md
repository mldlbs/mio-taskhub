# Agent 心跳与超时离线 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入 agent 主动心跳（upsert 自动注册）+ hub 超时自动离线，给空闲 agent 自动分配建立可靠的存活判定。

**Architecture:** `POST /agents/heartbeat` upsert 刷新 last_heartbeat（不写事件防风暴）；`_mark_stale_agents()` 用 DB 过滤（last_heartbeat < cutoff）把超时 agent 标 OFFLINE；`_scheduler_tick()` 统一封装 stale→release→assign；`_on_timeout` 加固为「agent OFFLINE 即回收其 run」（僵尸 run 防护）。MCP 工具 + wrapper + 引导提供 agent 侧心跳载体。

**Tech Stack:** Python 3.12 / FastAPI / SQLModel / SQLite / pytest

---

### Task 1: `POST /agents/heartbeat`（upsert + 不写事件）

**Files:**
- Modify: `mio_taskhub/api/agents.py`
- Test: `tests/test_agent_heartbeat.py`（新建）

- [ ] **Step 1: Write the failing test** — create `tests/test_agent_heartbeat.py`:

```python
# tests/test_agent_heartbeat.py
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlmodel import Session
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Agent, AgentStatus

client = TestClient(app)


def _agent(name):
    with Session(engine) as s:
        return s.get(Agent, name)


def test_heartbeat_upsert_registers_new_agent():
    r = client.post("/api/v1/agents/heartbeat", json={"name": "hup"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "online"
    a = _agent("hup")
    assert a is not None
    assert a.status == AgentStatus.ONLINE
    assert a.last_heartbeat is not None


def test_heartbeat_refreshes_existing_agent():
    client.post("/api/v1/agents/register", json={"name": "hexist", "agent_type": "t"})
    with Session(engine) as s:
        a = s.get(Agent, "hexist")
        a.status = AgentStatus.OFFLINE
        a.last_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)
        s.add(a); s.commit()
    r = client.post("/api/v1/agents/heartbeat", json={"name": "hexist"})
    assert r.status_code == 200
    a = _agent("hexist")
    assert a.status == AgentStatus.ONLINE
    assert (datetime.now(timezone.utc) - a.last_heartbeat).total_seconds() < 10


def test_heartbeat_does_not_write_event():
    client.post("/api/v1/agents/heartbeat", json={"name": "hevent"})
    ev = client.get("/api/v1/events", params={"after_seq": 0}).json()
    types = {e["type"] for e in ev["events"]}
    assert "agent_heartbeat" not in types


def test_heartbeat_requires_name():
    r = client.post("/api/v1/agents/heartbeat", json={})
    assert r.status_code in (400, 422)
```

- [ ] **Step 2: Run `python -m pytest tests/test_agent_heartbeat.py -q`** — Expected FAIL with `404 Not Found` for `/api/v1/agents/heartbeat`

- [ ] **Step 3: Update agents.py**

Read `mio_taskhub/api/agents.py` fully (it's ~36 lines: register endpoint). Add heartbeat endpoint:

```python
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session
from mio_taskhub.db import get_session
from mio_taskhub.models import Agent, AgentStatus

router = APIRouter(prefix="/agents", tags=["agents"])

@router.post("/register")
def register(body: dict, db: Session = Depends(get_session)):
    # ...（保留现有实现不变）...
    pass  # 现有 register 逻辑原样保留

@router.post("/heartbeat")
def heartbeat(body: dict, db: Session = Depends(get_session)):
    """agent 心跳（upsert 自动注册）：刷新 last_heartbeat + ONLINE。

    不写事件（防心跳事件风暴）；未注册的 name 自动创建。
    """
    name = body.get("name")
    if not name:
        return JSONResponse(status_code=400, content={"error": "name is required"})
    a = db.get(Agent, name)
    if a is None:
        a = Agent(name=name, agent_type="cli", status=AgentStatus.ONLINE,
                  last_heartbeat=datetime.now(timezone.utc))
    else:
        a.status = AgentStatus.ONLINE
        a.last_heartbeat = datetime.now(timezone.utc)
    db.add(a)
    db.commit()
    db.refresh(a)
    return {"name": a.name, "status": "online",
            "last_heartbeat": a.last_heartbeat.isoformat()}
```

IMPORTANT: do NOT emit an `agent_heartbeat` event (spec: heartbeats only update DB, no event).

- [ ] **Step 4: Run `python -m pytest tests/test_agent_heartbeat.py -q`** — Expected PASS (4)

- [ ] **Step 5: Full regression**

Run: `python -m pytest tests/ -q` — Expected PASS（216 + 4）

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/api/agents.py tests/test_agent_heartbeat.py
git commit -m "feat: POST /agents/heartbeat（upsert 自动注册，不写事件）"
```

### Task 2: `_mark_stale_agents` + `_scheduler_tick` + `_on_timeout` 僵尸防护

**Files:**
- Modify: `mio_taskhub/wiring.py`
- Test: `tests/test_agent_heartbeat.py`

- [ ] **Step 1: Write the failing tests (append to test_agent_heartbeat.py)**

```python
import mio_taskhub.wiring as wiring


def _mk_agent(name, status=AgentStatus.ONLINE, hb_age_sec=None):
    with Session(engine) as s:
        a = Agent(name=name, agent_type="t", status=status)
        if hb_age_sec is not None:
            a.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=hb_age_sec)
        else:
            a.last_heartbeat = datetime.now(timezone.utc)
        s.add(a); s.commit()


def _agent_status(name):
    with Session(engine) as s:
        return s.get(Agent, name).status


def test_stale_agent_marked_offline():
    _mk_agent("stale1", hb_age_sec=600)  # 10 分钟未心跳
    wiring._mark_stale_agents()
    assert _agent_status("stale1") == AgentStatus.OFFLINE


def test_fresh_agent_stays_online():
    _mk_agent("fresh1", hb_age_sec=10)
    wiring._mark_stale_agents()
    assert _agent_status("fresh1") == AgentStatus.ONLINE


def test_offline_agent_not_touched():
    _mk_agent("off1", status=AgentStatus.OFFLINE, hb_age_sec=9999)
    wiring._mark_stale_agents()
    assert _agent_status("off1") == AgentStatus.OFFLINE


def test_stale_agent_no_longer_assigned():
    _mk_agent("stale2", hb_age_sec=600)
    client.post("/api/v1/tasks", json={"title": "stale-task", "stage": "ready"})
    wiring._mark_stale_agents()
    wiring._assign_to_idle_agents()
    with Session(engine) as s:
        from mio_taskhub.models import Task, Run, TaskState
        t = s.exec(select(Task).where(Task.title == "stale-task")).first()
        assert t.state == TaskState.QUEUED  # 离线 agent 不再被分配


def test_agent_offline_recycles_run():
    """agent OFFLINE 后其 run 被 _on_timeout 回收（即使 task.timeout_min 很大）。"""
    _mk_agent("zombie", status=AgentStatus.OFFLINE)
    from mio_taskhub.models import Task, TaskState, Run, RunState, TaskStage
    with Session(engine) as s:
        t = Task(title="zombie-task", stage=TaskStage.READY, timeout_min=600)  # 10 分钟超时
        s.add(t); s.commit(); s.refresh(t)
        tid = t.id
    claim = client.post("/api/v1/tasks/claim", params={"agent": "zombie"}).json()
    rid = claim["id"]
    # 人为制造 run 心跳过期（但未到 task.timeout_min）
    with Session(engine) as s:
        run = s.get(Run, rid)
        run.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=200)
        s.add(run); s.commit()
    wiring._on_timeout(rid, tid)
    with Session(engine) as s:
        run = s.get(Run, rid)
        assert run.state == RunState.FINISHED
        task = s.get(Task, tid)
        assert task.state == TaskState.QUEUED


def test_scheduler_tick_runs_all_three():
    _mk_agent("tick-agent", hb_age_sec=600)  # stale
    client.post("/api/v1/tasks", json={"title": "tick-task", "stage": "ready"})
    wiring._scheduler_tick()
    assert _agent_status("tick-agent") == AgentStatus.OFFLINE
```

- [ ] **Step 2: Run `python -m pytest tests/test_agent_heartbeat.py -q`** — Expected FAIL with `AttributeError: module 'mio_taskhub.wiring' has no attribute '_mark_stale_agents'` (and `_scheduler_tick`)

- [ ] **Step 3: Update wiring.py**

Read `mio_taskhub/wiring.py` fully (160 lines). Add `AGENT_TIMEOUT_SECONDS`, `_mark_stale_agents`, `_scheduler_tick`, and harden `_on_timeout`.

Add constant near `DEFAULT_TIMEOUT_SECONDS`:

```python
AGENT_TIMEOUT_SECONDS = 180
```

Add imports if needed: `timedelta` from datetime.

Add `_mark_stale_agents`:

```python
def _mark_stale_agents():
    """把超过 AGENT_TIMEOUT_SECONDS 未心跳的 agent 标记为 OFFLINE。

    只改 agent status，不动 Run（run 由 _on_timeout 回收）。DB 层过滤，不全表遍历。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=AGENT_TIMEOUT_SECONDS)
    with Session(engine) as db:
        stale = db.exec(
            select(Agent).where(
                Agent.status != AgentStatus.OFFLINE,
                Agent.last_heartbeat.is_not(None),
                Agent.last_heartbeat < cutoff,
            )
        ).all()
        for a in stale:
            a.status = AgentStatus.OFFLINE
            event = emit_event(db, type="agent_offline", entity="agent",
                               entity_id=a.name, payload={"reason": "heartbeat_timeout"})
            db.add(a)
            db.commit()
            broadcast_for_event(event)
```

Add `_scheduler_tick` and wire it into `_get_due_tasks`:

```python
def _scheduler_tick():
    _mark_stale_agents()          # ① agent 生命周期
    _release_dependencies()       # ② 依赖放行
    _assign_to_idle_agents()      # ③ 空闲分配


def _get_due_tasks():
    _scheduler_tick()
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

Harden `_on_timeout` to recycle the run when the agent is OFFLINE (zombie run protection), regardless of task.timeout_min:

```python
def _on_timeout(run_id: str, task_id: str):
    with Session(engine) as db:
        run = db.get(Run, run_id)
        task = db.get(Task, task_id)
        if run and run.state in (RunState.CLAIMED, RunState.RUNNING):
            agent = db.get(Agent, run.agent_name)
            agent_offline = agent is None or agent.status == AgentStatus.OFFLINE
            if agent_offline:
                run.state = RunState.FINISHED
                run.result = "agent offline"
                run.finished_at = datetime.now(timezone.utc)
                run.exit_code = 1
                db.add(run)
                if task:
                    if task.attempt >= task.max_retries:
                        task.state = TaskState.FAILED
                    else:
                        task.state = TaskState.QUEUED
                        task.stage = TaskStage.READY
                    db.add(task)
                db.commit()
                return
        # 原有逻辑：run 心跳超时（HeartbeatSweep 触发）
        if run and run.state in (RunState.CLAIMED, RunState.RUNNING):
            run.state = RunState.FINISHED
            run.result = "heartbeat timeout"
            run.finished_at = datetime.now(timezone.utc)
            run.exit_code = 1
            db.add(run)
            if task:
                if task.attempt >= task.max_retries:
                    task.state = TaskState.FAILED
                else:
                    task.state = TaskState.QUEUED
                    task.stage = TaskStage.READY
                db.add(task)
        db.commit()
```

NOTE: `select` and `Agent`/`AgentStatus` are already imported in wiring.py (line 5). `timedelta` needs importing — add `timedelta` to the datetime import on line 1:

```python
from datetime import datetime, timezone, timedelta
```

The existing `test_wiring.py` tests must still pass (they test `_on_timeout` with a claimed run and fresh agent — the agent_offline branch won't trigger since the test agent is ONLINE or the run's agent isn't checked). Read test_wiring.py to verify: `test_timeout_resets_task_to_queued` claims via `w-agent` (registered ONLINE), so `agent_offline=False` → falls to original logic. Good.

- [ ] **Step 4: Run `python -m pytest tests/test_agent_heartbeat.py tests/test_wiring.py -q`** — Expected PASS

- [ ] **Step 5: Full regression**

Run: `python -m pytest tests/ -q` — Expected PASS（220 + 6）

- [ ] **Step 6: Commit**

```bash
git add mio_taskhub/wiring.py tests/test_agent_heartbeat.py
git commit -m "feat: agent 超时离线 + scheduler_tick 封装 + 僵尸 run 回收"
```

### Task 3: MCP `taskhub_agent_heartbeat` + wrapper + 引导

**Files:**
- Modify: `mio_taskhub/mcp_server.py`
- Modify: `agent_wrapper.py`
- Modify: `packaging/setup-agent.ps1`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test (append to test_mcp_server.py)**

```python
def test_agent_heartbeat_tool(mcp_ctx):
    r = _call("taskhub_agent_heartbeat", {"name": "hbmcp"})
    assert r["status"] == "online"
    assert "last_heartbeat" in r
```

- [ ] **Step 2: Run `python -m pytest tests/test_mcp_server.py::test_agent_heartbeat_tool -q`** — Expected FAIL with `ToolError: Unknown tool: taskhub_agent_heartbeat`

- [ ] **Step 3: Update mcp_server.py**

Read `mio_taskhub/mcp_server.py`. Update the `instructions` string (find the dialogue-usage spec near top) to add the heartbeat guidance:

```python
        "- 空闲时周期性调用 taskhub_agent_heartbeat 保持在线，否则超时会被标记离线；未注册时调用会自动注册。"
```

Add the tool after `taskhub_register` (around line 120):

```python
@mcp.tool(name="taskhub_agent_heartbeat", title="Agent 心跳保活", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
async def taskhub_agent_heartbeat(
    name: str = Field(description="当前 agent 名称，需与 register 一致", min_length=1, max_length=64),
) -> str:
    """保持 agent 在线（心跳保活）。

    空闲时周期性调用（建议约 1 分钟一次），避免超过 180s 被标记离线。
    未注册的 name 会自动注册（upsert）。
    """
    data = await _request("POST", "/agents/heartbeat", body={"name": name})
    return _fmt(data)
```

- [ ] **Step 4: Update agent_wrapper.py**

Read `agent_wrapper.py`. Add a `heartbeat-agent` action:

```python
    elif action == "heartbeat-agent":
        r = req("POST", "/agents/heartbeat", {"name": agent})
        print(f"Heartbeat: status={r['status']} last_heartbeat={r['last_heartbeat']}")
```

Update the usage line at top:

```python
# Actions: register | claim | heartbeat | result | heartbeat-agent | list
```

- [ ] **Step 5: Update packaging/setup-agent.ps1**

Read `packaging/setup-agent.ps1`. Find the task-execution spec text (the block injected into AGENTS.md/CLAUDE.md). Add a heartbeat line after the register line:

```markdown
1. taskhub_register：注册为 <agent 名称>（先检查是否已注册）；空闲时每隔约 1 分钟调用 taskhub_agent_heartbeat 保持在线
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_mcp_server.py -q` — Expected PASS（27 + 1 = 28）
Run: `python -m pytest tests/ -q` — Expected PASS（约 227）

- [ ] **Step 7: Commit**

```bash
git add mio_taskhub/mcp_server.py agent_wrapper.py packaging/setup-agent.ps1 tests/test_mcp_server.py
git commit -m "feat: MCP agent_heartbeat 工具 + wrapper + 引导"
```

### Task 4: 回归 + 文档 + 记忆

- [ ] **Step 1: Full backend regression**

Run: `python -m pytest tests/ -q` — Expected PASS

- [ ] **Step 2: 集成验证（可选，隔离实例）**

用临时 DB + 端口 48621 启动隔离实例，实测：register → heartbeat → 超时离线 → 离线后不分配。

- [ ] **Step 3: Update docs**

`MCP_INTEGRATION.md` 工具表加 `taskhub_agent_heartbeat`；「自动分配」节补心跳保活说明。

`docs/memory-workbench-p0-2026-08-15.md` 追加：agent 心跳与超时离线完成。

- [ ] **Step 4: Commit**

```bash
git add MCP_INTEGRATION.md docs/memory-workbench-p0-2026-08-15.md
git commit -m "docs: agent 心跳保活说明"
```
