# 研发流程生命周期（superpowers 工作流可视化）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 mio-taskhub 增加完整研发流程生命周期：需求理解→设计→计划→可执行→执行→审查→完成，通过新增 `stage` 字段与现有 `state` 分离实现，配套 API、MCP、Web UI 流程视图。

**Architecture:** 方案 B（stage 分离）。新增 `TaskStage` 枚举 + `Task.stage/spec_path/plan_path/review_result` 字段。新增 `POST /tasks/{id}/stage` 推进接口（含产出物强制校验）。claim 只领取 stage==READY 任务。submit_result 成功时 stage→REVIEW。DB 迁移旧任务 stage 置 READY。Web UI 新增「流程」泳道视图 + 详情抽屉阶段推进。

**Tech Stack:** Python 3.10+, FastAPI, SQLModel/SQLite, MCP Python SDK (FastMCP), React/Vite。

**Spec:** `docs/superpowers/specs/2026-08-13-dev-lifecycle-stages-design.md`

---

## 文件结构

| 文件 | 职责 | 改动 |
|---|---|---|
| `mio_taskhub/models.py` | 数据模型 | Task 加 stage/spec_path/plan_path/review_result + TaskStage 枚举 |
| `mio_taskhub/db.py` | DB 初始化/迁移 | ALTER TABLE 加 stage 列 + 旧行置 ready |
| `mio_taskhub/api/tasks.py` | 任务 API | stage 推进接口 + create/claim/get/list 联动 |
| `mio_taskhub/api/runs.py` | 执行 API | submit_result 成功 → stage REVIEW |
| `mio_taskhub/mcp_server.py` | MCP 工具 | 新增 advance_stage + 各工具带 stage |
| `web/src/App.jsx` | 前端 | 新增「流程」视图入口 |
| `web/src/components/FlowView.jsx` | 前端 | 流程泳道组件（新建） |
| `web/src/components/TaskDetail.jsx` | 前端 | 阶段进度条 + 推进按钮 + 产出物展示 |
| `web/src/api.js` | 前端 API | advanceStage 方法 |
| `tests/test_stage.py` | 测试 | 新建：stage 迁移/产出物/claim 过滤 |
| `tests/test_api.py` | 测试 | 修改：create 默认 brainstorming |
| `tests/test_mcp_server.py` | 测试 | 修改：advance_stage |
| `tests/test_db.py` | 测试 | 修改：迁移断言 |

---

### Task 1: 数据模型扩展（stage + 产出物字段）

**Files:**
- Modify: `mio_taskhub/models.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_models.py` 末尾追加：

```python
def test_task_stage_and_artifacts():
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskStage
    with Session(engine) as s:
        t = Task(title="stage-test", stage=TaskStage.DESIGN,
                 spec_path="docs/x.md", plan_path="docs/y.md", review_result="ok")
        s.add(t); s.commit(); s.refresh(t)
        assert t.stage == TaskStage.DESIGN
        assert t.spec_path == "docs/x.md" and t.plan_path == "docs/y.md"
        assert t.review_result == "ok"

def test_task_default_stage_is_ready():
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskStage
    with Session(engine) as s:
        t = Task(title="default-stage")
        s.add(t); s.commit(); s.refresh(t)
        assert t.stage == TaskStage.READY

def test_stage_transition_table():
    from mio_taskhub.models import TaskStage
    assert TaskStage.can_advance(TaskStage.BRAINSTORMING, TaskStage.DESIGN)
    assert TaskStage.can_advance(TaskStage.DESIGN, TaskStage.PLANNING)
    assert TaskStage.can_advance(TaskStage.PLANNING, TaskStage.READY)
    assert TaskStage.can_advance(TaskStage.READY, TaskStage.IMPLEMENTING)
    assert TaskStage.can_advance(TaskStage.IMPLEMENTING, TaskStage.REVIEW)
    assert TaskStage.can_advance(TaskStage.REVIEW, TaskStage.DONE)
    assert not TaskStage.can_advance(TaskStage.BRAINSTORMING, TaskStage.PLANNING)
    assert not TaskStage.can_advance(TaskStage.DONE, TaskStage.READY)
    # any -> cancelled
    assert TaskStage.can_advance(TaskStage.BRAINSTORMING, TaskStage.CANCELLED)
    assert TaskStage.can_advance(TaskStage.REVIEW, TaskStage.CANCELLED)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_models.py::test_task_stage_and_artifacts tests/test_models.py::test_task_default_stage_is_ready tests/test_models.py::test_stage_transition_table -v`
Expected: FAIL（TaskStage 不存在 / 字段不存在）

- [ ] **Step 3: 实现模型**

在 `mio_taskhub/models.py` 的 `TaskState` 之后新增：

```python
class TaskStage(str, enum.Enum):
    BRAINSTORMING = "brainstorming"
    DESIGN = "design"
    PLANNING = "planning"
    READY = "ready"
    IMPLEMENTING = "implementing"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"

    @classmethod
    def can_advance(cls, src: "TaskStage", dst: "TaskStage") -> bool:
        if dst == cls.CANCELLED:
            return src != cls.CANCELLED and src != cls.DONE
        valid = {
            cls.BRAINSTORMING: {cls.DESIGN},
            cls.DESIGN: {cls.PLANNING},
            cls.PLANNING: {cls.READY},
            cls.READY: {cls.IMPLEMENTING},
            cls.IMPLEMENTING: {cls.REVIEW},
            cls.REVIEW: {cls.DONE},
        }
        return dst in valid.get(src, set())
```

在 `Task` 类 `created_at` 之前新增字段：

```python
    stage: TaskStage = TaskStage.READY
    spec_path: str = ""
    plan_path: str = ""
    review_result: str = ""
```

> 模型默认 READY（向后兼容旧任务）；create_task 显式设 BRAINSTORMING。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_models.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/models.py tests/test_models.py
git commit -m "feat: TaskStage 枚举与 stage 产出物字段"
```

---

### Task 2: DB 迁移（旧表加 stage 列并置 ready）

**Files:**
- Modify: `mio_taskhub/db.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_db.py` 追加：

```python
def test_stage_column_added_to_existing_table():
    from sqlalchemy import inspect, text
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, TaskStage
    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("task")}
    assert "stage" in cols
    with Session(engine) as s:
        t = s.exec(__import__("sqlmodel").select(Task)).first()
        if t is not None:
            assert t.stage in (TaskStage.READY, TaskStage.BRAINSTORMING)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_db.py -q`
Expected: 若模型已加字段，create_all 会建新列（测试环境每次重建），此处应 PASS。若 FAIL，检查迁移逻辑。

> 说明：测试环境 conftest 每次 drop_all + create_all，新列自动存在。此测试主要覆盖「列存在」契约，真实迁移逻辑见 Step 3 的 `_migrate_stage_column`。

- [ ] **Step 3: 实现迁移**

在 `mio_taskhub/db.py` 中：

```python
from sqlalchemy import inspect, text

def _migrate_stage_column():
    """旧 task 表无 stage 列时添加，并把已有行置为 ready。"""
    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("task")}
        if "stage" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'ready'"))
        if "spec_path" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN spec_path VARCHAR NOT NULL DEFAULT ''"))
        if "plan_path" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN plan_path VARCHAR NOT NULL DEFAULT ''"))
        if "review_result" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN review_result VARCHAR NOT NULL DEFAULT ''"))
        conn.commit()

def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_stage_column()
```

> 注意：`ALTER TABLE ... DEFAULT 'ready'` 会把已有行填为 ready（迁移目标）。SQLite 支持带默认值的 ADD COLUMN。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_db.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/db.py tests/test_db.py
git commit -m "feat: 旧表 stage 列迁移（已有任务置 ready）"
```

---

### Task 3: stage 推进接口 + get/list 联动

**Files:**
- Modify: `mio_taskhub/api/tasks.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_stage.py` 创建：

```python
from fastapi.testclient import TestClient
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage
from sqlmodel import Session

client = TestClient(app)

def _mk(title="t", stage="brainstorming"):
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage))
        s.add(t); s.commit(); s.refresh(t)
        return t.id

def test_advance_to_design_requires_spec_path():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design"})
    assert r.status_code == 422  # 缺 spec_path

def test_advance_to_design_with_spec():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "design", "spec_path": "docs/s.md"})
    assert r.status_code == 200
    d = r.json()
    assert d["stage"] == "design" and d["spec_path"] == "docs/s.md"

def test_advance_to_planning_requires_plan_path():
    tid = _mk(stage="design")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "planning"})
    assert r.status_code == 422

def test_advance_to_planning_with_plan():
    tid = _mk(stage="design")
    r = client.post(f"/api/v1/tasks/{tid}/stage",
                    json={"target_stage": "planning", "plan_path": "docs/p.md"})
    assert r.status_code == 200
    assert r.json()["stage"] == "planning"

def test_advance_to_done_requires_review_result():
    tid = _mk(stage="review")
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "done"})
    assert r.status_code == 422

def test_advance_illegal_transition():
    tid = _mk()  # brainstorming
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "planning"})
    assert r.status_code == 400  # 非法迁移

def test_advance_brainstorming_requires_discussion():
    tid = _mk()
    r = client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design", "spec_path": "s.md"})
    assert r.status_code == 422  # 无 discussion 记录不能进 design

def test_get_task_returns_stage_fields():
    tid = _mk(stage="planning")
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "planning"
    assert "spec_path" in d and "plan_path" in d and "review_result" in d

def test_list_tasks_filter_by_stage():
    _mk(stage="design")
    _mk(stage="ready")
    r = client.get("/api/v1/tasks", params={"stage": "design"})
    data = r.json()
    assert all(t["stage"] == "design" for t in data)
    assert len(data) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_stage.py -q`
Expected: FAIL（stage 接口不存在 / 字段缺失）

- [ ] **Step 3: 实现**

在 `mio_taskhub/api/tasks.py` 顶部 import 加 `TaskStage`：

```python
from mio_taskhub.models import (Task, TaskState, TaskStage, Run, RunState, Subtask,
                                SubtaskStatus, GitRef, RefType, HistoryEvent,
                                Discussion, DiscussionMessage)
```

新增 stage 推进路由（放在 `claim` 之前）：

```python
@router.post("/{task_id}/stage")
def advance_stage(task_id: str, body: dict, db: Session = Depends(get_session)):
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
    src = TaskStage(t.stage) if isinstance(t.stage, str) else t.stage
    if not TaskStage.can_advance(src, dst):
        raise HTTPException(400, f"cannot advance from {src.value} to {dst.value}")
    if dst == TaskStage.DESIGN:
        if not body.get("spec_path"):
            raise HTTPException(422, "design stage requires spec_path")
        discussions = db.exec(select(Discussion).where(Discussion.task_id == task_id)).all()
        if not discussions:
            raise HTTPException(422, "design stage requires at least one discussion record")
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
    t.stage = dst
    db.add(t)
    db.commit()
    db.refresh(t)
    _broadcast_task_update(task_id)
    return {"id": t.id, "stage": t.stage.value, "spec_path": t.spec_path,
            "plan_path": t.plan_path, "review_result": t.review_result,
            "state": t.state.value}
```

在 `_task_detail` 返回 dict 追加（`deliverables` 之后）：

```python
        "stage": t.stage.value if not isinstance(t.stage, str) else t.stage,
        "spec_path": t.spec_path,
        "plan_path": t.plan_path,
        "review_result": t.review_result,
```

在 `list_tasks` 加 stage 过滤参数：

```python
def list_tasks(state: str = None, agent_type: str = None, stage: str = None,
               db: Session = Depends(get_session)):
    q = select(Task)
    if state:
        q = q.where(Task.state == TaskState(state))
    if stage:
        q = q.where(Task.stage == TaskStage(stage))
    if agent_type:
        q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
    rows = db.exec(q).all()
    return [
        {"id": r.id, "title": r.title, "state": r.state.value, "stage": r.stage.value,
         "priority": r.priority, "target_agent_type": r.target_agent_type}
        for r in rows
    ]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_stage.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/tasks.py tests/test_stage.py
git commit -m "feat: stage 推进接口与产出物强制校验"
```

---

### Task 4: create/claim/submit_result 联动 stage

**Files:**
- Modify: `mio_taskhub/api/tasks.py`
- Modify: `mio_taskhub/api/runs.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_stage.py` 追加：

```python
def test_create_task_defaults_brainstorming():
    r = client.post("/api/v1/tasks", json={"title": "new-task"})
    d = r.json()
    tid = d["id"]
    detail = client.get(f"/api/v1/tasks/{tid}").json()
    assert detail["stage"] == "brainstorming"

def test_claim_only_picks_ready():
    _mk(title="brain", stage="brainstorming")
    _mk(title="ready", stage="ready")
    r = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent"})
    assert r.status_code == 200
    # 应领到 ready 任务
    d = client.get(f"/api/v1/tasks/{r.json()['task_id']}").json()
    assert d["stage"] == "ready"

def test_claim_returns_204_when_no_ready():
    _mk(title="only-brain", stage="brainstorming")
    r = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent2"})
    assert r.status_code == 204

def test_claim_moves_ready_to_implementing():
    _mk(title="to-impl", stage="ready")
    r = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent3"})
    tid = r.json()["task_id"]
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "implementing"
    assert d["state"] == "claimed"

def test_submit_result_moves_to_review():
    _mk(title="finish", stage="ready")
    rid = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent4"}).json()["id"]
    tid = client.get("/api/v1/runs/" + rid).json()["task_id"]
    r = client.post(f"/api/v1/runs/{rid}/result", json={"success": True, "result": "ok"})
    assert r.status_code == 200
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "review"
    assert d["state"] == "completed"
```

> 注意：`GET /api/v1/runs/{rid}` 不存在（runs 无 GET 接口）。改为从 claim 响应直接拿 task_id。修正测试：

```python
def test_submit_result_moves_to_review():
    _mk(title="finish", stage="ready")
    claim = client.post("/api/v1/tasks/claim", params={"agent": "stage-agent4"}).json()
    rid, tid = claim["id"], claim["task_id"]
    r = client.post(f"/api/v1/runs/{rid}/result", json={"success": True, "result": "ok"})
    assert r.status_code == 200
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["stage"] == "review"
    assert d["state"] == "completed"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_stage.py -q`
Expected: 新用例 FAIL（create 不是 brainstorming / claim 不按 stage 过滤 / submit 不转 review）

- [ ] **Step 3: 实现**

在 `mio_taskhub/api/tasks.py` create_task 的 Task 构造加 `stage=TaskStage.BRAINSTORMING`：

```python
    t = Task(
        ...
        stage=TaskStage.BRAINSTORMING,
        ...
    )
```

在 `claim_task` 的查询加 stage 过滤——把查询改为只取 READY：

```python
    q = select(Task).where(Task.state == TaskState.QUEUED, Task.stage == TaskStage.READY)
```

在 claim 领取成功后，设置 stage → IMPLEMENTING：

```python
    task.state = TaskState.CLAIMED
    task.stage = TaskStage.IMPLEMENTING
    task.attempt += 1
```

在 `mio_taskhub/api/runs.py` submit_result 成功分支，task 加 stage 推进：

```python
        if success:
            task.state = TaskState.COMPLETED
            task.stage = TaskStage.REVIEW
```

> 需要在 runs.py 顶部 import `TaskStage`。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_stage.py tests/test_api.py tests/test_integration.py -q`
Expected: PASS（注意 test_api.py 既有 claim 测试用无 stage 任务——它们通过 `client.post("/api/v1/tasks")` 创建，现在默认 brainstorming，会导致既有 claim 测试失败！）

> **兼容性处理**：既有测试如 `test_claim_task_creates_run` 用 create_task 后直接 claim，现在任务 stage=brainstorming 无法被领取。有两种方案：
> 1. 修改既有测试：创建后先推进到 ready 再 claim（改测试）
> 2. create_task 提供可选参数 `stage`，默认 brainstorming，但既有测试可通过 body 传 stage=ready
>
> 选择**方案 1**：修改受影响的既有测试（test_api.py / test_integration.py），在 create 后调用 stage 推进到 ready 再 claim。新增一个测试辅助：
>
> ```python
> def _make_ready(title="t"):
>     tid = client.post("/api/v1/tasks", json={"title": title}).json()["id"]
>     client.post(f"/api/v1/tasks/{tid}/stage", json={"target_stage": "design", "spec_path": "s.md"})
>     # 需要 discussion？design 需要 discussion。为简化，直接 PATCH stage 绕开？
> ```
>
> 问题：design 阶段强制要求 discussion，测试辅助要构造 discussion。**更实用方案**：给 create_task 增加可选 `stage` body 参数（默认 brainstorming），测试和高级用户可直接传 `"stage": "ready"` 跳过理解/设计阶段创建可执行任务。这符合 YAGNI（不强制所有任务走理解流程，任务创建者可选择跳过）。
>
> 修正 create_task 实现：
>
> ```python
>     stage_val = body.get("stage", "brainstorming")
>     try:
>         stage = TaskStage(stage_val)
>     except ValueError:
>         raise HTTPException(400, f"invalid stage: {stage_val}")
>     ...
>     t = Task(..., stage=stage, ...)
> ```
>
> 既有测试改为 create 时传 `"stage": "ready"`（或在需要时传）。修改 test_api.py / test_integration.py 中所有 claim 相关测试的 create 调用，加 `"stage": "ready"`。

- [ ] **Step 4b: 更新既有测试**

在 `tests/test_api.py` 中，所有 `client.post("/api/v1/tasks", json={"title": ...})` 后紧接 claim 的地方，改为 create 时传 stage ready。例如：

```python
def test_claim_task_creates_run():
    client.post("/api/v1/tasks", json={"title": "Claim me", "stage": "ready"})
    ...
```

用 `grep -n "json={\"title\"" tests/test_api.py tests/test_integration.py tests/test_plans_api.py` 找出所有受影响调用，逐一加 `"stage": "ready"`。plans 测试用 queued 任务，stage 默认 brainstorming 不影响（plans 只按 state 过滤），但为一致也可加。

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add mio_taskhub/api/tasks.py mio_taskhub/api/runs.py tests/test_api.py tests/test_integration.py tests/test_stage.py
git commit -m "feat: create/claim/submit 联动 stage（默认理解→设计→计划→可执行流程）"
```

---

### Task 5: MCP advance_stage 工具 + 各工具带 stage

**Files:**
- Modify: `mio_taskhub/mcp_server.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_mcp_server.py` 追加：

```python
def test_advance_stage_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "Stage", "stage": "brainstorming"})
    tid = created["id"]
    # brainstorming -> design needs spec + discussion
    d = _call("taskhub_add_discussion", {"task_id": tid, "topic": "理解", "agent": "mcp-agent",
                                         "summary": "s", "conclusions": "c"})
    assert d["status"] == "closed"
    r = _call("taskhub_advance_stage", {"task_id": tid, "target_stage": "design",
                                        "spec_path": "docs/s.md"})
    assert r["stage"] == "design"
    r2 = _call("taskhub_advance_stage", {"task_id": tid, "target_stage": "planning",
                                         "plan_path": "docs/p.md"})
    assert r2["stage"] == "planning"
    r3 = _call("taskhub_advance_stage", {"task_id": tid, "target_stage": "ready"})
    assert r3["stage"] == "ready"

def test_claim_with_stage_ready(mcp_ctx):
    _call("taskhub_create_task", {"title": "Ctx", "stage": "ready"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent"})
    assert claim["task"]["stage"] == "ready"
```

> 注意 create_task 需要支持 `stage` 参数。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: FAIL（未知工具 / create 无 stage 参数）

- [ ] **Step 3: 实现**

在 `mio_taskhub/mcp_server.py`：

(a) `taskhub_create_task` 增加参数：

```python
    stage: str = Field(default="brainstorming", description="研发阶段：brainstorming/design/planning/ready/...")
```

body 增加 `"stage": stage`。

(b) `taskhub_claim` 的 get_task 返回已含 stage（API 已返回）。

(c) 新增工具（放在 `taskhub_advance_stage` 名，`taskhub_cancel_task` 之前）：

```python
@mcp.tool(name="taskhub_advance_stage", title="推进研发阶段", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_advance_stage(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    target_stage: str = Field(description="目标阶段：design/planning/ready/review/done/cancelled"),
    spec_path: Optional[str] = Field(default=None, description="设计文档路径（进 design 必填）"),
    plan_path: Optional[str] = Field(default=None, description="计划文档路径（进 planning 必填）"),
    review_result: Optional[str] = Field(default=None, description="审查结论（进 done 必填）"),
) -> str:
    """推进任务到下一研发阶段，需带对应产出物。

    brainstorming→design 需 spec_path 且任务下有讨论记录；
    design→planning 需 plan_path；review→done 需 review_result。
    """
    body = {"target_stage": target_stage}
    if spec_path is not None: body["spec_path"] = spec_path
    if plan_path is not None: body["plan_path"] = plan_path
    if review_result is not None: body["review_result"] = review_result
    data = await _request("POST", f"/tasks/{task_id}/stage", body=body)
    return _fmt(data)
```

(d) `taskhub_submit_result` 返回已含 state，stage 由 API 联动（无需改）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP advance_stage 工具与 create 带 stage"
```

---

### Task 6: Web UI — API 封装 + 流程视图

**Files:**
- Modify: `web/src/api.js`
- Create: `web/src/components/FlowView.jsx`
- Modify: `web/src/App.jsx`

- [ ] **Step 1: api.js 加方法**

```js
advanceStage: (id, body) => req('POST', `/tasks/${id}/stage`, body),
```

`listTasks` 已支持 query——加 stage 过滤支持（改 `listTasks` 签名或新增）：

```js
listTasks: (params) => req('GET', '/tasks' + (params ? '?' + new URLSearchParams(params).toString() : '')),
```

> 兼容：App.jsx 调用 `api.listTasks()` 无参数仍工作。

- [ ] **Step 2: 新建 FlowView.jsx**

创建 `web/src/components/FlowView.jsx`，流程泳道组件：

```jsx
import { useState } from 'react'
import { prio, fmtDur, agMono, agColor } from '../constants'

const STAGES = [
  { id: 'brainstorming', label: '需求理解', en: 'BRAINSTORM' },
  { id: 'design',        label: '设计',     en: 'DESIGN' },
  { id: 'planning',      label: '计划',     en: 'PLANNING' },
  { id: 'ready',         label: '待执行',   en: 'READY' },
  { id: 'implementing',  label: '执行中',   en: 'IMPL' },
  { id: 'review',        label: '审查',     en: 'REVIEW' },
  { id: 'done',          label: '完成',     en: 'DONE' },
]
const EXCLUDE = ['cancelled']

export default function FlowView({ tasks, onOpen, onCancel, onAdvance }) {
  const [advancing, setAdvancing] = useState(null) // {task, target}
  const [artifact, setArtifact] = useState('')

  const byStage = Object.fromEntries(STAGES.map(s => [
    s.id, tasks.filter(t => t.stage === s.id),
  ]))

  const artifactField = (target) =>
    target === 'design' ? { key: 'spec_path', ph: 'docs/superpowers/specs/xxx.md' } :
    target === 'planning' ? { key: 'plan_path', ph: 'docs/superpowers/plans/xxx.md' } :
    target === 'done' ? { key: 'review_result', ph: '审查结论…' } : null

  const nextStage = (stage) => {
    const i = STAGES.findIndex(s => s.id === stage)
    return i >= 0 && i < STAGES.length - 1 ? STAGES[i + 1].id : null
  }

  const confirmAdvance = async () => {
    if (!advancing) return
    const { task, target } = advancing
    const af = artifactField(target)
    const body = { target_stage: target }
    if (af) {
      if (!artifact.trim()) { alert('请填写产出物'); return }
      body[af.key] = artifact.trim()
    }
    try {
      await onAdvance(task.id, body)
      setAdvancing(null); setArtifact('')
    } catch (e) { alert('推进失败: ' + e.message) }
  }

  return (
    <div className="flow">
      <div className="flow__track">
        {STAGES.map(stage => (
          <section key={stage.id} className={`flow__lane`}>
            <header className="flow__head">
              <span className="flow__en">{stage.en}</span>
              <span className="flow__count">{byStage[stage.id].length}</span>
            </header>
            <div className="flow__label">{stage.label}</div>
            <div className="flow__body">
              {byStage[stage.id].map(t => {
                const p = prio(t.priority)
                const ac = t.target_agent_type && agColor(t.target_agent_type)
                const next = nextStage(t.stage)
                return (
                  <div key={t.id} className="flow-card" onClick={() => onOpen && onOpen(t)}>
                    <div className="flow-card__top">
                      <h4>{t.title}</h4>
                      <span className="flow-card__chev">›</span>
                    </div>
                    <div className="flow-card__meta">
                      <span className={`chip${p.p >= 3 ? ' chip--p3' : ''}${p.p === 2 ? ' chip--p2' : ''}`}>{p.label}</span>
                      {t.target_agent_type && (
                        <span className="task__agent">
                          <span className="agent-mono" style={{ background: ac.bg, borderColor: ac.fg, color: ac.fg }}>{agMono(t.target_agent_type)}</span>
                        </span>
                      )}
                      {t.spec_path && <span className="chip chip--doc" title={t.spec_path}>📄</span>}
                      {t.plan_path && <span className="chip chip--doc" title={t.plan_path}>📝</span>}
                    </div>
                    <div className="flow-card__foot">
                      <span>{fmtDur(t.est_duration_min)}</span>
                      {next && (
                        <button className="btn btn--ghost flow-card__adv" onClick={e => { e.stopPropagation(); setAdvancing({ task: t, target: next }); setArtifact('') }}>
                          → {STAGES.find(s => s.id === next)?.label}
                        </button>
                      )}
                      {(t.stage === 'brainstorming' || t.stage === 'design' || t.stage === 'planning') && (
                        <button className="btn btn--ghost btn--danger flow-card__cancel" onClick={e => { e.stopPropagation(); onCancel(t.id) }}>×</button>
                      )}
                    </div>
                  </div>
                )
              })}
              {byStage[stage.id].length === 0 && <div className="flow__empty">—</div>}
            </div>
          </section>
        ))}
      </div>

      {advancing && (
        <div className="overlay" onClick={() => setAdvancing(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal__head">
              <h3>推进到「{STAGES.find(s => s.id === advancing.target)?.label}」</h3>
              <button className="modal__close" onClick={() => setAdvancing(null)}>×</button>
            </div>
            <div className="modal__body">
              <p style={{ fontSize: 13, color: 'var(--ink-dim)' }}>{advancing.task.title}</p>
              {artifactField(advancing.target) ? (
                <div className="field">
                  <label className="field__label">
                    {advancing.target === 'done' ? '审查结论' : '产出物路径'}
                  </label>
                  <input autoFocus value={artifact}
                    onChange={e => setArtifact(e.target.value)}
                    placeholder={artifactField(advancing.target).ph} />
                </div>
              ) : (
                <p style={{ fontSize: 13 }}>确认推进？</p>
              )}
              <div className="modal__foot">
                <button className="btn btn--ghost" onClick={() => setAdvancing(null)}>取消</button>
                <button className="btn btn--accent" onClick={confirmAdvance}>推进</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

> 说明：FlowView 接收 tasks（含 stage 字段）、onOpen（打开详情）、onCancel、onAdvance（调用 api.advanceStage 后刷新）。注意 `design` 需要 discussion——若用户直接拖 brainstorming→design 且无 discussion，API 返回 422，前端 alert 提示。可在弹窗说明。

- [ ] **Step 3: App.jsx 接入**

- import `FlowView`
- `listTasks` 改为接收 params（或保持无参）
- 新增 view 值 `'flow'`，导航加「流程」入口（现有 Rail 组件需要加按钮——查看 `web/src/components/Rail.jsx` 的导航结构，加入 flow）
- 新增 handler：
```jsx
const advanceStage = async (id, body) => {
  try { await api.advanceStage(id, body); loadTasks() }
  catch (e) { setError('推进阶段失败: ' + e.message) }
}
```
- `{view === 'flow' && <FlowView tasks={tasks} onOpen={openTask} onCancel={cancelTask} onAdvance={advanceStage} />}`

> 注意：tasks 列表来自 `api.listTasks()`，需要在 list_tasks 响应里带 stage 字段（Task 4 已在 list_tasks 返回 stage）。确认 App.jsx 的 tasks 状态已含 stage。

- [ ] **Step 4: CSS**

在 `web/src/index.css` 追加 FlowView 样式（`.flow`, `.flow__track`, `.flow__lane`, `.flow-card`, `.chip--doc` 等），沿用设计系统变量。最少化。

- [ ] **Step 5: 构建验证**

Run: `npm run build`（在 `web/`）
Expected: 构建成功

- [ ] **Step 6: 提交**

```bash
git add web/src/api.js web/src/App.jsx web/src/components/FlowView.jsx web/src/components/Rail.jsx web/src/index.css
git commit -m "feat(web): 研发流程视图（阶段泳道 + 推进弹窗）"
```

---

### Task 7: Web UI — 详情抽屉阶段展示与推进

**Files:**
- Modify: `web/src/components/TaskDetail.jsx`
- Modify: `web/src/App.jsx`

- [ ] **Step 1: 实现阶段展示与推进按钮**

在 TaskDetail 增加：

- 顶部 meta 区新增「阶段」kv 行：`task.stage`
- 进度条：基于 stage 在 STAGES 数组中的 index / 总数（简单横条）
- 「推进阶段」按钮：调 `onAdvance(id, body)`，弹窗让用户选目标阶段 + 填产出物（复用 FlowView 的弹窗逻辑，或简化：直接调 advanceStage 到下一阶段，填产出物）
- 展示 spec_path / plan_path 链接、review_result
- 讨论记录区加标题「理解/讨论」

TaskDetail 新增 props：`onAdvance`。

```jsx
const STAGES = ['brainstorming','design','planning','ready','implementing','review','done']
const idx = STAGES.indexOf(task.stage)
const pct = idx >= 0 ? Math.round((idx + 1) / STAGES.length * 100) : 0
```

在 drawer meta 后加：

```jsx
<div className="kv"><span>阶段</span><b>{task.stage}</b></div>
{task.stage && (
  <div className="stage-bar" title={`${idx + 1}/${STAGES.length} · ${pct}%`}>
    <div className="stage-bar__fill" style={{ width: pct + '%' }} />
  </div>
)}
{task.spec_path && <div className="kv"><span>Spec</span><b className="mono">{task.spec_path}</b></div>}
{task.plan_path && <div className="kv"><span>Plan</span><b className="mono">{task.plan_path}</b></div>}
{task.review_result && (
  <div className="drawer__sec"><h3>审查结论</h3><p>{task.review_result}</p></div>
)}
```

- [ ] **Step 2: TaskDetail 推进按钮 + App.jsx 接线**

TaskDetail 签名加 `onAdvance` prop（在组件解构中）。在 drawer 底部（footer 前）加「推进阶段」按钮：

```jsx
{task.stage && task.stage !== 'done' && task.stage !== 'cancelled' && onAdvance && (
  <button className="btn btn--ghost" onClick={() => onAdvance(task)}>
    推进阶段 →
  </button>
)}
```

App.jsx 给 TaskDetail 传 `onAdvance={advanceTaskStage}`，其中：

```jsx
const advanceTaskStage = (task) => {
  const next = { brainstorming:'design', design:'planning', planning:'ready',
                 implementing:'review', review:'done' }[task.stage]
  if (!next) return
  // 产出物：design 需 spec_path、planning 需 plan_path、done 需 review_result
  const msg = next === 'design' ? 'Spec 路径: ' :
              next === 'planning' ? 'Plan 路径: ' :
              next === 'done' ? '审查结论: ' : ''
  const val = window.prompt(msg, '')
  if (val === null) return
  const body = { target_stage: next }
  if (next === 'design') body.spec_path = val
  else if (next === 'planning') body.plan_path = val
  else if (next === 'done') body.review_result = val
  advanceStage(task.id, body)
}
```

> 用 `window.prompt` 简化产出物输入（避免再建一个弹窗组件）；design 阶段若任务无 discussion，API 返回 422，errorbar 提示。

- [ ] **Step 2b: 构建验证**

Run: `npm run build`（在 `web/`）
Expected: 构建成功

- [ ] **Step 3: 提交**

```bash
git add web/src/components/TaskDetail.jsx web/src/App.jsx web/src/index.css
git commit -m "feat(web): 详情抽屉阶段进度与产出物展示"
```

---

### Task 8: 全量回归与清理

**Files:**
- 无（验证）

- [ ] **Step 1: 运行全部测试**

Run: `python -m pytest -q`
Expected: 全部 PASS（含既有 90 + 新增 stage 测试）

- [ ] **Step 2: 构建 Web**

Run: `npm run build`（在 `web/`）
Expected: 构建成功

- [ ] **Step 3: 端到端验证**

- 重启 hub
- 创建任务 → 确认 stage=brainstorming
- 添加讨论 → advance 到 design（spec_path）→ planning（plan_path）→ ready
- claim → 确认 stage=implementing
- submit_result → 确认 stage=review
- advance 到 done（review_result）→ 确认完成
- 浏览器打开流程视图，确认 7 列泳道 + 拖拽/推进

- [ ] **Step 4: 确认 git 状态干净（除预期改动）**

Run: `git status`
Expected: 无未预期文件

- [ ] **Step 5: 提交收尾**

```bash
git add -A
git commit -m "chore: 研发流程生命周期回归验证"
```
