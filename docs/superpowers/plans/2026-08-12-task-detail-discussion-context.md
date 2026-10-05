# 任务细节、讨论与上下文关联 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 mio-taskhub 的任务增加丰富细节字段（验收标准/截止时间/标签/项目/工作区/文件/产出物）、子任务、Git 引用、执行历史、独立讨论回写，以及 claim 时传入上下文。

**Architecture:** 规范化子表方案。Task 表新增标量列；新增 Subtask/GitRef/HistoryEvent/Discussion/DiscussionMessage 五张子表（1-N）。API 层在 `mio_taskhub/api/tasks.py` 扩展详情与子资源接口，claim 增加 context 参数。MCP 层在 `mio_taskhub/mcp_server.py` 扩展工具。Web UI 扩展表单与详情抽屉。

**Tech Stack:** Python 3.10+, FastAPI, SQLModel (SQLAlchemy JSON), SQLite, MCP Python SDK (FastMCP), React/Vite。

**Spec:** `docs/superpowers/specs/2026-08-12-task-detail-discussion-context-design.md`

---

## 文件结构

| 文件 | 职责 | 改动 |
|---|---|---|
| `mio_taskhub/models.py` | 数据模型 | 修改 Task + 新增 5 张子表 + 2 个枚举 |
| `mio_taskhub/db.py` | DB 初始化/迁移 | 修改 init_db 处理 JSON 列 |
| `mio_taskhub/api/tasks.py` | 任务 API | 修改 get_task/claim + 新增 PATCH/子资源 |
| `mio_taskhub/mcp_server.py` | MCP 工具 | 修改 claim/get_task + 新增 7 工具 |
| `web/src/api.js` | 前端 API 封装 | 修改 |
| `web/src/App.jsx` | 前端 UI | 修改表单 + 详情抽屉 |
| `tests/test_models.py` | 模型测试 | 修改 |
| `tests/test_api.py` | API 测试 | 修改 |
| `tests/test_mcp_server.py` | MCP 测试 | 修改 |

---

### Task 1: 数据模型扩展

**Files:**
- Modify: `mio_taskhub/models.py`

- [ ] **Step 1: 写失败测试（模型）**

在 `tests/test_models.py` 末尾追加：

```python
def test_task_rich_fields():
    from sqlmodel import Session
    from mio_taskhub.db import engine
    from mio_taskhub.models import Task, Subtask, GitRef, HistoryEvent, Discussion, DiscussionMessage
    with Session(engine) as s:
        t = Task(title="rich", acceptance_criteria="AC", due_at=_now(),
                 labels=["blocked"], project="p", workspace="/w", files=["a.py"],
                 deliverables=["report.md"])
        s.add(t); s.commit(); s.refresh(t)
        assert t.labels == ["blocked"] and t.project == "p" and t.files == ["a.py"]

        st = Subtask(task_id=t.id, order=1, title="step1", status="in_progress")
        g = GitRef(task_id=t.id, ref_type="branch", value="feat/x", note="n")
        h = HistoryEvent(task_id=t.id, type="created")
        d = Discussion(task_id=t.id, topic="讨论1", agent="opencode", status="open", summary="s")
        s.add_all([st, g, h, d]); s.commit()

        got = s.get(Subtask, st.id)
        assert got.status == "in_progress" and got.task_id == t.id
        assert s.get(GitRef, g.id).ref_type == "branch"
        assert s.get(HistoryEvent, h.id).type == "created"
        assert s.get(Discussion, d.id).status == "open"

        m = DiscussionMessage(discussion_id=d.id, author="user", role="user", content="hi")
        s.add(m); s.commit()
        assert s.get(DiscussionMessage, m.id).content == "hi"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_models.py -q`
Expected: FAIL 报 `ImportError`（Subtask 等不存在）

- [ ] **Step 3: 实现模型**

在 `mio_taskhub/models.py` 顶部加导入 `from sqlalchemy import Column, JSON`，在 `Task` 类新增字段，在 `RunState` 之后新增枚举与子表：

```python
from sqlalchemy import Column, JSON
```

Task 类新增列（追加在 `created_at` 之前）：

```python
    acceptance_criteria: str = ""
    due_at: Optional[datetime] = None
    labels: list = Field(default_factory=list, sa_column=Column(JSON))
    project: str = ""
    workspace: str = ""
    files: list = Field(default_factory=list, sa_column=Column(JSON))
    deliverables: list = Field(default_factory=list, sa_column=Column(JSON))
```

新增枚举（放在 `TaskState` 之后）：

```python
class SubtaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"

class RefType(str, enum.Enum):
    BRANCH = "branch"
    COMMIT = "commit"
    PR = "pr"
    TAG = "tag"
```

新增子表（放在 `Run` 之后、`Event` 之前）：

```python
class Subtask(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(index=True)
    order: int = 0
    title: str
    status: SubtaskStatus = SubtaskStatus.PENDING

class GitRef(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(index=True)
    ref_type: RefType = RefType.BRANCH
    value: str
    note: str = ""

class HistoryEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    type: str
    payload: Optional[str] = None
    at: datetime = Field(default_factory=_now)

class Discussion(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(index=True)
    topic: str
    agent: str = ""
    status: str = "open"
    summary: str = ""
    conclusions: str = ""
    started_at: datetime = Field(default_factory=_now)
    ended_at: Optional[datetime] = None

class DiscussionMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    discussion_id: str = Field(index=True)
    author: str
    role: str = "user"
    content: str
    at: datetime = Field(default_factory=_now)
```

> 注意：Discussion.status 用 str 而非枚举，简化 open/closed 切换；Subtask.status/GitRef.ref_type 用枚举。

- [ ] **Step 4: 处理 JSON 默认值加载**

SQLite 存储的 JSON 列在 SQLModel 读取时若字段类型为 `list` 且值合法，会反序列化为 list；但空/旧行可能为 NULL。为稳妥，保持默认 `default_factory=list`，写入时始终传 list。

Run: `python -m pytest tests/test_models.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/models.py tests/test_models.py
git commit -m "feat: 任务富字段与子表模型（subtask/gitref/history/discussion）"
```

---

### Task 2: DB 迁移兼容

**Files:**
- Modify: `mio_taskhub/db.py`

现有 SQLite 库已建表，新增列不会自动 ALTER。为保证 `init_db` 对旧库兼容（新增列有默认值即可，无需数据迁移），在 `init_db` 中调用 `SQLModel.metadata.create_all` 即可——SQLite 对新增列的已有表不会变更，但测试环境每次 drop_all 重建，生产旧库需手动重建或容忍旧库无新列。

- [ ] **Step 1: 写失败测试（create_all 含新表）**

在 `tests/test_db.py` 追加：

```python
def test_create_all_creates_new_tables():
    from sqlalchemy import inspect
    from mio_taskhub.db import engine
    tables = set(inspect(engine).get_table_names())
    for name in ["subtask", "gitref", "historyevent", "discussion", "discussionmessage"]:
        assert name in tables, f"missing table {name}"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_db.py -q`
Expected: 初始 FAIL（表不存在），随后 Step 3 后 PASS

- [ ] **Step 3: 确认 init_db 行为**

`init_db` 已调用 `SQLModel.metadata.create_all(engine)`，新表会自动创建。若测试仍失败，检查 conftest 的 drop_all 是否覆盖所有新表（`SQLModel.metadata.drop_all` 基于注册的 metadata，包含新表，无需改）。

Run: `python -m pytest tests/test_db.py -q`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add mio_taskhub/db.py tests/test_db.py
git commit -m "test: 验证新表自动创建"
```

---

### Task 3: 任务详情与 PATCH

**Files:**
- Modify: `mio_taskhub/api/tasks.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api.py` 追加：

```python
def test_patch_task_rich_fields():
    created = client.post("/api/v1/tasks", json={"title": "Patch me"}).json()
    r = client.patch(f"/api/v1/tasks/{created['id']}", json={
        "acceptance_criteria": "AC", "due_at": "2026-12-31T23:59:59+00:00",
        "labels": ["blocked"], "project": "p", "workspace": "/w",
        "files": ["a.py"], "deliverables": ["report.md"],
    })
    assert r.status_code == 200
    d = r.json()
    assert d["acceptance_criteria"] == "AC"
    assert d["labels"] == ["blocked"]
    assert d["project"] == "p"
    assert d["workspace"] == "/w"
    assert d["files"] == ["a.py"]
    assert d["deliverables"] == ["report.md"]
    assert d["due_at"] is not None

def test_get_task_returns_rich_fields():
    created = client.post("/api/v1/tasks", json={"title": "Rich"}).json()
    client.patch(f"/api/v1/tasks/{created['id']}", json={"labels": ["x"], "project": "p"})
    d = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert d["labels"] == ["x"] and d["project"] == "p"
    assert d["acceptance_criteria"] == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_api.py::test_patch_task_rich_fields tests/test_api.py::test_get_task_returns_rich_fields -v`
Expected: FAIL（405 PATCH 不存在 / 字段缺失）

- [ ] **Step 3: 实现 PATCH 与详情扩展**

在 `get_task` 返回 dict 中补充新字段（`created_at` 之后追加）：

```python
        "acceptance_criteria": t.acceptance_criteria,
        "due_at": t.due_at.isoformat() if t.due_at else None,
        "labels": t.labels,
        "project": t.project,
        "workspace": t.workspace,
        "files": t.files,
        "deliverables": t.deliverables,
```

在 `get_task` 之后新增 PATCH 路由：

```python
@router.patch("/{task_id}")
def update_task(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    editable = ["title", "description", "priority", "est_duration_min", "max_retries",
                "acceptance_criteria", "due_at", "labels", "project", "workspace",
                "files", "deliverables", "target_agent_type", "depends_on"]
    for k in editable:
        if k in body:
            setattr(t, k, body[k])
    db.add(t)
    db.commit()
    db.refresh(t)
    from mio_taskhub.models import Subtask, GitRef, HistoryEvent, Discussion
    subtasks = db.exec(select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.order)).all()
    gitrefs = db.exec(select(GitRef).where(GitRef.task_id == task_id)).all()
    history = db.exec(select(HistoryEvent).where(HistoryEvent.task_id == task_id).order_by(HistoryEvent.at)).all()
    discussions = db.exec(select(Discussion).where(Discussion.task_id == task_id)).all()
    return {
        "id": t.id, "title": t.title, "description": t.description, "state": t.state.value,
        "priority": t.priority, "target_agent_type": t.target_agent_type,
        "schedule_type": t.schedule_type, "run_at": t.run_at.isoformat() if t.run_at else None,
        "cron_expr": t.cron_expr, "est_duration_min": t.est_duration_min,
        "depends_on": t.depends_on, "max_retries": t.max_retries, "attempt": t.attempt,
        "created_at": t.created_at.isoformat(),
        "acceptance_criteria": t.acceptance_criteria,
        "due_at": t.due_at.isoformat() if t.due_at else None,
        "labels": t.labels, "project": t.project, "workspace": t.workspace,
        "files": t.files, "deliverables": t.deliverables,
        "subtasks": [{"id": s.id, "order": s.order, "title": s.title, "status": s.status.value} for s in subtasks],
        "gitrefs": [{"id": g.id, "ref_type": g.ref_type.value, "value": g.value, "note": g.note} for g in gitrefs],
        "history": [{"id": h.id, "type": h.type, "payload": h.payload, "at": h.at.isoformat()} for h in history],
        "discussions": [{"id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
                         "summary": d.summary, "conclusions": d.conclusions,
                         "started_at": d.started_at.isoformat()} for d in discussions],
    }
```

> 注意：`due_at` 传字符串即可，SQLModel 自动解析为 datetime（需 ISO 格式）。为保持一致，`get_task` 同样返回该结构。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_api.py -q`
Expected: PASS（含既有用例）

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/tasks.py tests/test_api.py
git commit -m "feat: 任务 PATCH 富字段与详情扩展"
```

---

### Task 4: 子任务接口

**Files:**
- Modify: `mio_taskhub/api/tasks.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api.py` 追加：

```python
def test_subtask_crud():
    created = client.post("/api/v1/tasks", json={"title": "ST"}).json()
    tid = created["id"]
    r = client.post(f"/api/v1/tasks/{tid}/subtasks", json={"title": "s1", "order": 1})
    assert r.status_code == 200
    sid = r.json()["id"]
    assert r.json()["status"] == "pending"

    r2 = client.patch(f"/api/v1/tasks/{tid}/subtasks/{sid}", json={"status": "done"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "done"

    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert len(d["subtasks"]) == 1 and d["subtasks"][0]["title"] == "s1"

def test_subtask_404():
    r = client.post("/api/v1/tasks/nonexistent/subtasks", json={"title": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_api.py::test_subtask_crud tests/test_api.py::test_subtask_404 -v`
Expected: FAIL（404/405）

- [ ] **Step 3: 实现**

在 `tasks.py` 顶部 import 补充 `Subtask, SubtaskStatus`：

```python
from mio_taskhub.models import Task, TaskState, Run, RunState, Subtask, SubtaskStatus
```

在 `update_task` 之后新增：

```python
@router.post("/{task_id}/subtasks")
def add_subtask(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    st = Subtask(task_id=task_id, order=body.get("order", 0),
                 title=body.get("title", ""), status=SubtaskStatus(body.get("status", "pending")))
    db.add(st); db.commit(); db.refresh(st)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}

@router.patch("/{task_id}/subtasks/{sid}")
def update_subtask(task_id: str, sid: str, body: dict, db: Session = Depends(get_session)):
    st = db.get(Subtask, sid)
    if not st or st.task_id != task_id:
        raise HTTPException(404, "subtask not found")
    if "title" in body: st.title = body["title"]
    if "order" in body: st.order = body["order"]
    if "status" in body: st.status = SubtaskStatus(body["status"])
    db.add(st); db.commit(); db.refresh(st)
    return {"id": st.id, "task_id": st.task_id, "order": st.order,
            "title": st.title, "status": st.status.value}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/tasks.py tests/test_api.py
git commit -m "feat: 子任务接口"
```

---

### Task 5: Git 引用接口

**Files:**
- Modify: `mio_taskhub/api/tasks.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api.py` 追加：

```python
def test_gitref_add():
    created = client.post("/api/v1/tasks", json={"title": "GR"}).json()
    r = client.post(f"/api/v1/tasks/{created['id']}/gitrefs",
                    json={"ref_type": "branch", "value": "feat/x", "note": "n"})
    assert r.status_code == 200
    assert r.json()["ref_type"] == "branch"
    d = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert len(d["gitrefs"]) == 1 and d["gitrefs"][0]["value"] == "feat/x"

def test_gitref_404():
    r = client.post("/api/v1/tasks/nope/gitrefs", json={"ref_type": "commit", "value": "abc"})
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_api.py::test_gitref_add tests/test_api.py::test_gitref_404 -v`
Expected: FAIL

- [ ] **Step 3: 实现**

import 补充 `GitRef, RefType`：

```python
from mio_taskhub.models import Task, TaskState, Run, RunState, Subtask, SubtaskStatus, GitRef, RefType
```

在 `update_subtask` 之后新增：

```python
@router.post("/{task_id}/gitrefs")
def add_gitref(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    g = GitRef(task_id=task_id, ref_type=RefType(body.get("ref_type", "branch")),
               value=body.get("value", ""), note=body.get("note", ""))
    db.add(g); db.commit(); db.refresh(g)
    return {"id": g.id, "task_id": g.task_id, "ref_type": g.ref_type.value,
            "value": g.value, "note": g.note}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/tasks.py tests/test_api.py
git commit -m "feat: Git 引用接口"
```

---

### Task 6: 执行历史接口

**Files:**
- Modify: `mio_taskhub/api/tasks.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api.py` 追加：

```python
def test_history_add():
    created = client.post("/api/v1/tasks", json={"title": "Hist"}).json()
    r = client.post(f"/api/v1/tasks/{created['id']}/history",
                    json={"type": "discussion", "payload": {"msg": "hi"}})
    assert r.status_code == 200
    assert r.json()["type"] == "discussion"
    d = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert len(d["history"]) == 1

def test_history_404():
    r = client.post("/api/v1/tasks/nope/history", json={"type": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_api.py::test_history_add tests/test_api.py::test_history_404 -v`
Expected: FAIL

- [ ] **Step 3: 实现**

import 补充 `HistoryEvent`：

```python
from mio_taskhub.models import (Task, TaskState, Run, RunState, Subtask, SubtaskStatus,
                                GitRef, RefType, HistoryEvent)
```

在 `add_gitref` 之后新增：

```python
@router.post("/{task_id}/history")
def add_history(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    import json as _json
    h = HistoryEvent(task_id=task_id, type=body.get("type", ""),
                     payload=_json.dumps(body.get("payload")) if body.get("payload") is not None else None)
    db.add(h); db.commit(); db.refresh(h)
    return {"id": h.id, "task_id": h.task_id, "type": h.type,
            "payload": h.payload, "at": h.at.isoformat()}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/tasks.py tests/test_api.py
git commit -m "feat: 执行历史接口"
```

---

### Task 7: 讨论接口（agent 拉回回写）

**Files:**
- Modify: `mio_taskhub/api/tasks.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api.py` 追加：

```python
def test_discussion_add_and_close():
    created = client.post("/api/v1/tasks", json={"title": "Disc"}).json()
    tid = created["id"]
    r = client.post(f"/api/v1/tasks/{tid}/discussions", json={
        "topic": "如何实现", "agent": "opencode",
        "summary": "讨论了方案", "conclusions": "用方案B",
        "messages": [{"author": "opencode", "role": "assistant", "content": "建议方案B"}],
    })
    assert r.status_code == 200
    did = r.json()["id"]
    assert r.json()["status"] == "closed"
    assert r.json()["conclusions"] == "用方案B"

    d = client.get(f"/api/v1/tasks/{tid}/discussions").json()
    assert len(d["discussions"]) == 1
    assert d["discussions"][0]["messages"][0]["content"] == "建议方案B"

def test_discussion_404():
    r = client.post("/api/v1/tasks/nope/discussions", json={"topic": "t"})
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_api.py::test_discussion_add_and_close tests/test_api.py::test_discussion_404 -v`
Expected: FAIL

- [ ] **Step 3: 实现**

import 补充 `Discussion, DiscussionMessage`：

```python
from mio_taskhub.models import (Task, TaskState, Run, RunState, Subtask, SubtaskStatus,
                                GitRef, RefType, HistoryEvent, Discussion, DiscussionMessage)
```

在 `add_history` 之后新增：

```python
@router.post("/{task_id}/discussions")
def add_discussion(task_id: str, body: dict, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    conclusions = body.get("conclusions", "")
    status = "closed" if conclusions else "open"
    d = Discussion(task_id=task_id, topic=body.get("topic", ""), agent=body.get("agent", ""),
                   status=status, summary=body.get("summary", ""), conclusions=conclusions,
                   ended_at=_now() if status == "closed" else None)
    db.add(d); db.commit(); db.refresh(d)
    for m in body.get("messages", []):
        db.add(DiscussionMessage(discussion_id=d.id, author=m.get("author", ""),
                                 role=m.get("role", "user"), content=m.get("content", "")))
    db.commit()
    return {"id": d.id, "task_id": d.task_id, "topic": d.topic, "agent": d.agent,
            "status": d.status, "summary": d.summary, "conclusions": d.conclusions,
            "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None}

@router.get("/{task_id}/discussions")
def list_discussions(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    rows = db.exec(select(Discussion).where(Discussion.task_id == task_id).order_by(Discussion.started_at)).all()
    out = []
    for d in rows:
        msgs = db.exec(select(DiscussionMessage).where(DiscussionMessage.discussion_id == d.id).order_by(DiscussionMessage.at)).all()
        out.append({
            "id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
            "summary": d.summary, "conclusions": d.conclusions,
            "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None,
            "messages": [{"author": m.author, "role": m.role, "content": m.content,
                          "at": m.at.isoformat()} for m in msgs],
        })
    return {"task_id": task_id, "discussions": out}
```

> 注：`get_task` 详情里的 discussions 不含 messages（精简），完整消息走 `/discussions` 接口。若需详情也带，可后续扩展。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/tasks.py tests/test_api.py
git commit -m "feat: 讨论接口（回写摘要+结论）"
```

---

### Task 8: claim 携带上下文

**Files:**
- Modify: `mio_taskhub/api/tasks.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api.py` 追加：

```python
def test_claim_carries_context():
    client.post("/api/v1/tasks", json={"title": "Ctx"})
    r = client.post("/api/v1/tasks/claim", params={
        "agent": "ctx-agent",
        "project": "p", "workspace": "/w", "files": "a.py,b.py",
    })
    assert r.status_code == 200
    # 任务应带上上下文
    tid = r.json()["task_id"]
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["project"] == "p"
    assert d["workspace"] == "/w"
    assert d["files"] == ["a.py", "b.py"]

def test_claim_does_not_overwrite_context():
    client.post("/api/v1/tasks", json={"title": "Keep"})
    r1 = client.post("/api/v1/tasks/claim", params={"agent": "ctx1", "project": "p1"})
    tid = r1.json()["task_id"]
    r2 = client.post("/api/v1/tasks/claim", params={"agent": "ctx2", "project": "p2"})
    assert r2.status_code == 204  # 无其他任务，但说明第二个 agent 领取不到
    d = client.get(f"/api/v1/tasks/{tid}").json()
    assert d["project"] == "p1"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_api.py::test_claim_carries_context tests/test_api.py::test_claim_does_not_overwrite_context -v`
Expected: FAIL（project 为空）

- [ ] **Step 3: 实现**

修改 `claim_task` 签名与领取逻辑：

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
    q = select(Task).where(Task.state == TaskState.QUEUED)
    if agent_type:
        q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
    q = q.order_by(Task.priority.desc(), Task.created_at.asc())
    task = db.exec(q).first()
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
    )
    task.state = TaskState.CLAIMED
    task.attempt += 1
    db.add(run)
    db.add(task)
    db.commit()
    db.refresh(run)
    return {"id": run.id, "task_id": run.task_id, "state": run.state.value,
            "agent_name": run.agent_name, "attempt": run.attempt}
```

> `files` 以逗号分隔字符串传入，服务端拆成 list；仅任务无值时写入。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/tasks.py tests/test_api.py
git commit -m "feat: claim 携带项目/工作区/文件上下文"
```

---

### Task 9: MCP — claim 上下文 + 详情/更新/子任务

**Files:**
- Modify: `mio_taskhub/mcp_server.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_mcp_server.py` 追加：

```python
def test_claim_with_context(mcp_ctx):
    _call("taskhub_create_task", {"title": "MCP Ctx"})
    claim = _call("taskhub_claim", {"agent": "mcp-agent", "project": "p",
                                    "workspace": "/w", "files": "a.py,b.py"})
    assert claim["task"]["project"] == "p"
    assert claim["task"]["files"] == ["a.py", "b.py"]

def test_update_task_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "Upd"})
    tid = created["id"]
    r = _call("taskhub_update_task", {"task_id": tid, "acceptance_criteria": "AC",
                                      "labels": ["blocked"], "project": "p"})
    assert r["acceptance_criteria"] == "AC"
    assert r["labels"] == ["blocked"]

def test_subtask_tools(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "Sub"})
    tid = created["id"]
    st = _call("taskhub_add_subtask", {"task_id": tid, "title": "s1", "order": 1})
    assert st["status"] == "pending"
    up = _call("taskhub_update_subtask", {"task_id": tid, "subtask_id": st["id"], "status": "done"})
    assert up["status"] == "done"
    detail = _call("taskhub_get_task", {"task_id": tid})
    assert len(detail["subtasks"]) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: FAIL（未知工具）

- [ ] **Step 3: 实现**

在 `taskhub_claim` 增加参数并在请求中带上：

```python
async def taskhub_claim(
    agent: str = Field(description="当前 agent 名称，需先注册", min_length=1, max_length=64),
    agent_type: Optional[str] = Field(default=None, description="若设置，只领取匹配该类型的任务", max_length=32),
    project: Optional[str] = Field(default=None, description="关联项目名", max_length=200),
    workspace: Optional[str] = Field(default=None, description="工作区根路径", max_length=500),
    files: Optional[str] = Field(default=None, description="逗号分隔的文件路径列表", max_length=2000),
) -> str:
    query = {"agent": agent, "agent_type": agent_type}
    if project: query["project"] = project
    if workspace: query["workspace"] = workspace
    if files: query["files"] = files
    claim = await _request("POST", "/tasks/claim", params=query)
    ...
```

新增工具（放在 `taskhub_cancel_task` 之前）：

```python
@mcp.tool(name="taskhub_update_task", title="更新任务细节", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_update_task(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    title: Optional[str] = Field(default=None, description="标题"),
    description: Optional[str] = Field(default=None, description="描述"),
    acceptance_criteria: Optional[str] = Field(default=None, description="验收标准"),
    due_at: Optional[str] = Field(default=None, description="截止时间 ISO 格式"),
    labels: Optional[list] = Field(default=None, description="自定义状态标签列表"),
    project: Optional[str] = Field(default=None, description="项目名"),
    workspace: Optional[str] = Field(default=None, description="工作区路径"),
    files: Optional[list] = Field(default=None, description="文件路径列表"),
    deliverables: Optional[list] = Field(default=None, description="产出物路径列表"),
) -> str:
    """更新任务细节字段。仅传需要修改的字段。
    Args:
        task_id: 任务唯一标识
        其余字段均为可选，传了才更新
    Returns:
        JSON: 更新后的任务完整详情
    """
    body = {k: v for k, v in {
        "title": title, "description": description, "acceptance_criteria": acceptance_criteria,
        "due_at": due_at, "labels": labels, "project": project, "workspace": workspace,
        "files": files, "deliverables": deliverables,
    }.items() if v is not None}
    data = await _request("PATCH", f"/tasks/{task_id}", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_add_subtask", title="添加子任务", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_add_subtask(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    title: str = Field(description="子任务标题", min_length=1, max_length=200),
    order: int = Field(default=0, description="排序号"),
    status: str = Field(default="pending", description="状态：pending/in_progress/done/blocked"),
) -> str:
    """为任务添加一个子任务/计划步骤。"""
    data = await _request("POST", f"/tasks/{task_id}/subtasks",
                          body={"title": title, "order": order, "status": status})
    return _fmt(data)


@mcp.tool(name="taskhub_update_subtask", title="更新子任务状态", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_update_subtask(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    subtask_id: str = Field(description="子任务唯一标识", min_length=1),
    status: Optional[str] = Field(default=None, description="状态：pending/in_progress/done/blocked"),
    title: Optional[str] = Field(default=None, description="标题"),
    order: Optional[int] = Field(default=None, description="排序号"),
) -> str:
    """更新子任务的标题/排序/状态。"""
    body = {k: v for k, v in {"title": title, "order": order, "status": status}.items() if v is not None}
    data = await _request("PATCH", f"/tasks/{task_id}/subtasks/{subtask_id}", body=body)
    return _fmt(data)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP claim 上下文 + 更新/子任务工具"
```

---

### Task 10: MCP — Git 引用/历史/讨论工具

**Files:**
- Modify: `mio_taskhub/mcp_server.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_mcp_server.py` 追加：

```python
def test_gitref_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "GitT"})
    tid = created["id"]
    g = _call("taskhub_add_gitref", {"task_id": tid, "ref_type": "branch", "value": "feat/x"})
    assert g["ref_type"] == "branch"
    detail = _call("taskhub_get_task", {"task_id": tid})
    assert detail["gitrefs"][0]["value"] == "feat/x"

def test_history_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "HistT"})
    tid = created["id"]
    h = _call("taskhub_add_history", {"task_id": tid, "type": "discussion"})
    assert h["type"] == "discussion"
    detail = _call("taskhub_get_task", {"task_id": tid})
    assert len(detail["history"]) == 1

def test_discussion_tool(mcp_ctx):
    created = _call("taskhub_create_task", {"title": "DiscT"})
    tid = created["id"]
    d = _call("taskhub_add_discussion", {
        "task_id": tid, "topic": "方案", "agent": "mcp-agent",
        "summary": "讨论", "conclusions": "用B",
        "messages": [{"author": "mcp-agent", "role": "assistant", "content": "建议B"}],
    })
    assert d["status"] == "closed"
    disc = _call("taskhub_get_task", {"task_id": tid})
    assert len(disc["discussions"]) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: FAIL（未知工具）

- [ ] **Step 3: 实现**

新增工具（放在 `taskhub_cancel_task` 之前，`taskhub_update_subtask` 之后）：

```python
@mcp.tool(name="taskhub_add_gitref", title="关联 Git 引用", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_add_gitref(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    ref_type: str = Field(default="branch", description="类型：branch/commit/pr/tag"),
    value: str = Field(description="引用值，如分支名或 commit hash", min_length=1),
    note: str = Field(default="", description="备注"),
) -> str:
    """为任务关联一个 Git 引用（分支/commit/PR/tag）。"""
    data = await _request("POST", f"/tasks/{task_id}/gitrefs",
                          body={"ref_type": ref_type, "value": value, "note": note})
    return _fmt(data)


@mcp.tool(name="taskhub_add_history", title="追加执行历史", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_add_history(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    type: str = Field(description="事件类型：created/claimed/heartbeat/result/discussion/...", max_length=50),
    payload: Optional[str] = Field(default=None, description="JSON 字符串格式的附加数据"),
) -> str:
    """为任务追加一条执行历史事件。"""
    import json as _json
    try:
        p = _json.loads(payload) if payload else None
    except Exception:
        p = {"raw": payload}
    data = await _request("POST", f"/tasks/{task_id}/history", body={"type": type, "payload": p})
    return _fmt(data)


@mcp.tool(name="taskhub_add_discussion", title="回写讨论结果", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_add_discussion(
    task_id: str = Field(description="任务唯一标识", min_length=1),
    topic: str = Field(description="讨论主题", min_length=1, max_length=200),
    agent: str = Field(default="", description="发起讨论的 agent 名称", max_length=64),
    summary: str = Field(default="", description="讨论摘要"),
    conclusions: str = Field(default="", description="结论（非空则标记 closed）"),
    messages: Optional[list] = Field(default=None, description="消息列表：[{author, role, content}]"),
) -> str:
    """agent 将任务拉回独立会话讨论后，回写摘要与结论到任务。

    有 conclusions 时讨论标记为 closed。消息列表可选。
    """
    body = {"topic": topic, "agent": agent, "summary": summary,
            "conclusions": conclusions, "messages": messages or []}
    data = await _request("POST", f"/tasks/{task_id}/discussions", body=body)
    return _fmt(data)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP Git 引用/历史/讨论工具"
```

---

### Task 11: Web UI — API 封装与表单

**Files:**
- Modify: `web/src/api.js`
- Modify: `web/src/App.jsx`

- [ ] **Step 1: 扩展 api.js**

在 `api.js` 追加方法：

```js
export const api = {
  listTasks: () => req('GET', '/tasks'),
  createTask: (t) => req('POST', '/tasks', t),
  getTask: (id) => req('GET', `/tasks/${id}`),
  updateTask: (id, body) => req('PATCH', `/tasks/${id}`, body),
  claim: (agent) => req('POST', `/tasks/claim?agent=${encodeURIComponent(agent)}`),
  heartbeat: (rid, body) => req('POST', `/runs/${rid}/heartbeat`, body),
  result: (rid, body) => req('POST', `/runs/${rid}/result`, body),
  nightPlan: (start, end) => req('GET', `/plans/night?start=${start}&end=${end}`),
  addSubtask: (id, body) => req('POST', `/tasks/${id}/subtasks`, body),
  updateSubtask: (id, sid, body) => req('PATCH', `/tasks/${id}/subtasks/${sid}`, body),
  addGitref: (id, body) => req('POST', `/tasks/${id}/gitrefs`, body),
  addHistory: (id, body) => req('POST', `/tasks/${id}/history`, body),
  addDiscussion: (id, body) => req('POST', `/tasks/${id}/discussions`, body),
  listDiscussions: (id) => req('GET', `/tasks/${id}/discussions`),
}
```

- [ ] **Step 2: 创建任务表单加字段**

在 `App.jsx` 的 `form` state 与 `createTask` 中补充：

```jsx
const [form, setForm] = useState({ title: '', description: '', priority: 0, est_duration_min: 30,
  acceptance_criteria: '', due_at: '', labels: '', project: '', workspace: '', files: '', deliverables: '' })

const createTask = async (e) => {
  e.preventDefault()
  if (!form.title.trim()) return
  try {
    const body = {
      title: form.title, description: form.description, priority: +form.priority,
      est_duration_min: +form.est_duration_min, acceptance_criteria: form.acceptance_criteria,
      due_at: form.due_at || null,
      labels: form.labels ? form.labels.split(',').map(s => s.trim()).filter(Boolean) : [],
      project: form.project, workspace: form.workspace,
      files: form.files ? form.files.split(',').map(s => s.trim()).filter(Boolean) : [],
      deliverables: form.deliverables ? form.deliverables.split(',').map(s => s.trim()).filter(Boolean) : [],
    }
    await api.createTask(body)
    setForm({ title: '', description: '', priority: 0, est_duration_min: 30,
      acceptance_criteria: '', due_at: '', labels: '', project: '', workspace: '', files: '', deliverables: '' })
    setShowModal(false)
    loadTasks()
  } catch (e) { setError('创建失败: ' + e.message) }
}
```

在创建弹窗表单中（`est_duration_min` 块之后）插入字段：

```jsx
<div style={{ marginBottom: 16 }}>
  <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 4, color: '#374151' }}>验收标准</label>
  <textarea value={form.acceptance_criteria} onChange={e => setForm({ ...form, acceptance_criteria: e.target.value })} placeholder="完成定义 / 验收标准..." rows={2} style={{ width: '100%', padding: '8px 12px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 14, boxSizing: 'border-box', resize: 'vertical' }} />
</div>
<div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
  <div style={{ flex: 1 }}>
    <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 4, color: '#374151' }}>截止时间</label>
    <input type="datetime-local" value={form.due_at} onChange={e => setForm({ ...form, due_at: e.target.value })} style={{ width: '100%', padding: '8px 12px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 14, boxSizing: 'border-box' }} />
  </div>
  <div style={{ flex: 1 }}>
    <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 4, color: '#374151' }}>标签（逗号分隔）</label>
    <input value={form.labels} onChange={e => setForm({ ...form, labels: e.target.value })} placeholder="blocked,waiting-review" style={{ width: '100%', padding: '8px 12px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 14, boxSizing: 'border-box' }} />
  </div>
</div>
<div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
  <div style={{ flex: 1 }}>
    <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 4, color: '#374151' }}>项目</label>
    <input value={form.project} onChange={e => setForm({ ...form, project: e.target.value })} placeholder="项目名" style={{ width: '100%', padding: '8px 12px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 14, boxSizing: 'border-box' }} />
  </div>
  <div style={{ flex: 1 }}>
    <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 4, color: '#374151' }}>工作区路径</label>
    <input value={form.workspace} onChange={e => setForm({ ...form, workspace: e.target.value })} placeholder="E:\\work\\code\\..." style={{ width: '100%', padding: '8px 12px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 14, boxSizing: 'border-box' }} />
  </div>
</div>
<div style={{ marginBottom: 16 }}>
  <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 4, color: '#374151' }}>文件路径（逗号分隔）</label>
  <input value={form.files} onChange={e => setForm({ ...form, files: e.target.value })} placeholder="src/a.py,src/b.py" style={{ width: '100%', padding: '8px 12px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 14, boxSizing: 'border-box' }} />
</div>
<div style={{ marginBottom: 16 }}>
  <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 4, color: '#374151' }}>产出物（逗号分隔）</label>
  <input value={form.deliverables} onChange={e => setForm({ ...form, deliverables: e.target.value })} placeholder="report.md,dist/app.js" style={{ width: '100%', padding: '8px 12px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 14, boxSizing: 'border-box' }} />
</div>
```

- [ ] **Step 3: 构建验证**

Run: `npm run build`（在 `web/` 目录）
Expected: 构建成功，`web/dist/assets/index-*.js` 更新

- [ ] **Step 4: 提交**

```bash
git add web/src/api.js web/src/App.jsx web/dist
git commit -m "feat: Web UI 创建任务富字段表单"
```

---

### Task 12: Web UI — 任务详情抽屉

**Files:**
- Modify: `web/src/App.jsx`

- [ ] **Step 1: 实现详情抽屉**

在 `App.jsx` 增加 state 与组件。在组件顶部 state 区追加：

```jsx
const [detail, setDetail] = useState(null)
```

看板卡片点击打开详情（在卡片 `<div key={task.id} draggable ...>` 上加 `onClick`，并阻止 drag 冒泡）：

```jsx
<div key={task.id} draggable
  onDragStart={() => setDraggedTask(task.id)}
  onClick={() => openDetail(task.id)}
  ...
```

新增函数：

```jsx
const openDetail = async (id) => {
  try {
    const d = await api.getTask(id)
    setDetail(d)
  } catch (e) { setError('加载详情失败: ' + e.message) }
}

const toggleSubtask = async (st, done) => {
  if (!detail) return
  await api.updateSubtask(detail.id, st.id, { status: done ? 'done' : 'pending' })
  openDetail(detail.id)
}
```

在 `</main>` 之后、`{showModal && (...)}` 之前插入抽屉渲染（简化版，展示富字段）：

```jsx
{detail && (
  <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', justifyContent: 'flex-end', zIndex: 90 }} onClick={() => setDetail(null)}>
    <div style={{ width: 520, maxWidth: '92vw', height: '100%', background: '#fff', overflowY: 'auto', padding: 24, boxShadow: '-8px 0 24px rgba(0,0,0,0.15)' }} onClick={e => e.stopPropagation()}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ margin: 0, fontSize: 18 }}>{detail.title}</h3>
        <button onClick={() => setDetail(null)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: '#94a3b8' }}>×</button>
      </div>
      {detail.description && <p style={{ color: '#555', fontSize: 13, marginBottom: 12 }}>{detail.description}</p>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
        <span style={{ background: '#e3f2fd', color: '#1565c0', borderRadius: 4, padding: '2px 8px', fontSize: 12 }}>状态 {detail.state}</span>
        <span style={{ background: '#ede7f6', color: '#5e35b1', borderRadius: 4, padding: '2px 8px', fontSize: 12 }}>P{detail.priority}</span>
        {detail.project && <span style={{ background: '#e8f5e9', color: '#2e7d32', borderRadius: 4, padding: '2px 8px', fontSize: 12 }}>📁 {detail.project}</span>}
        {detail.workspace && <span style={{ background: '#fff8e1', color: '#f57f17', borderRadius: 4, padding: '2px 8px', fontSize: 12 }}>🗂 {detail.workspace}</span>}
        {(detail.labels || []).map(l => <span key={l} style={{ background: '#fce4ec', color: '#c62828', borderRadius: 4, padding: '2px 8px', fontSize: 12 }}>🏷 {l}</span>)}
      </div>
      {detail.due_at && <p style={{ fontSize: 12, color: '#f57f17' }}>⏰ 截止: {detail.due_at}</p>}
      {detail.acceptance_criteria && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>验收标准</div>
          <div style={{ background: '#f8fafc', borderRadius: 6, padding: 10, fontSize: 13, color: '#374151', whiteSpace: 'pre-wrap' }}>{detail.acceptance_criteria}</div>
        </div>
      )}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>子任务</div>
        {(detail.subtasks || []).map(st => (
          <label key={st.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={st.status === 'done'} onChange={e => toggleSubtask(st, e.target.checked)} />
            <span style={{ textDecoration: st.status === 'done' ? 'line-through' : 'none', color: st.status === 'done' ? '#94a3b8' : '#1a1a2e' }}>{st.title}</span>
          </label>
        ))}
        {(detail.subtasks || []).length === 0 && <div style={{ color: '#b0bec5', fontSize: 12 }}>暂无子任务</div>}
      </div>
      {detail.files?.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>关联文件</div>
          {detail.files.map(f => <div key={f} style={{ fontSize: 12, color: '#1565c0', padding: '2px 0' }}>📄 {f}</div>)}
        </div>
      )}
      {detail.deliverables?.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>产出物</div>
          {detail.deliverables.map(f => <div key={f} style={{ fontSize: 12, color: '#2e7d32', padding: '2px 0' }}>📦 {f}</div>)}
        </div>
      )}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>执行历史</div>
        {(detail.history || []).slice().reverse().map(h => (
          <div key={h.id} style={{ fontSize: 12, color: '#64748b', padding: '3px 0', borderBottom: '1px solid #f1f5f9' }}>
            <b>{h.type}</b> · {h.at} {h.payload ? `· ${h.payload}` : ''}
          </div>
        ))}
        {(detail.history || []).length === 0 && <div style={{ color: '#b0bec5', fontSize: 12 }}>暂无历史</div>}
      </div>
      <div>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>讨论记录</div>
        {(detail.discussions || []).map(dc => (
          <div key={dc.id} style={{ background: '#f8fafc', borderRadius: 6, padding: 10, marginBottom: 8, fontSize: 12 }}>
            <div><b>{dc.topic}</b> <span style={{ color: dc.status === 'closed' ? '#2e7d32' : '#f57f17' }}>[{dc.status}]</span></div>
            {dc.summary && <div style={{ color: '#555', marginTop: 4 }}>{dc.summary}</div>}
            {dc.conclusions && <div style={{ color: '#1a1a2e', marginTop: 4 }}><b>结论:</b> {dc.conclusions}</div>}
          </div>
        ))}
        {(detail.discussions || []).length === 0 && <div style={{ color: '#b0bec5', fontSize: 12 }}>暂无讨论</div>}
      </div>
    </div>
  </div>
)}
```

- [ ] **Step 2: 构建验证**

Run: `npm run build`（在 `web/` 目录）
Expected: 构建成功

- [ ] **Step 3: 手动冒烟**

启动 hub 后浏览器打开 `http://127.0.0.1:8080/`，点击任务卡片应打开详情抽屉，显示富字段/子任务/历史/讨论。

- [ ] **Step 4: 提交**

```bash
git add web/src/App.jsx web/dist
git commit -m "feat: Web UI 任务详情抽屉"
```

---

### Task 13: WS 广播任务变更

**Files:**
- Modify: `mio_taskhub/api/tasks.py`
- Modify: `tests/test_ws.py`

spec 约束「子资源写入后统一通过 WS 广播任务变更」。现有 `notifications.py` 提供 `ws_manager.broadcast(message)`，但任务写入路由均为 sync `def`，无法直接 await。方案：这些路由改为 `async def`，写入后在返回前广播 `{"type": "task_update", "task_id": ...}`。SQLite 本地同步调用在 async 路由中会短暂阻塞事件循环，本地单用户可接受。

- [ ] **Step 1: 写失败测试**

在 `tests/test_ws.py` 追加：

```python
def test_create_task_broadcasts(ws_client_factory):
    # 需要 TestClient 的 websocket_connect 上下文
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # 先触发一次连接建立（fastapi testclient 需要先 receive 一次? 不需）
        r = client.post("/api/v1/tasks", json={"title": "WS broadcast"})
        assert r.status_code == 200
        data = ws.receive_json()
        assert data["type"] == "task_update"
```

> 若现有 test_ws.py 已有 ws_client 工厂/夹具，沿用其模式；否则直接用 TestClient。注：TestClient 同一 client 内 websocket_connect 与 POST 并发时，POST 需在独立线程或复用同 client（FastAPI TestClient 支持同 client 内 ws + http）。若该方式在测试中不成立，改为仅验证广播函数被调用（见 Step 3 备选）。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ws.py -q`
Expected: 新增用例 FAIL（未收到 task_update）

- [ ] **Step 3: 实现广播**

在 `tasks.py` 顶部 import：

```python
import asyncio
from mio_taskhub.notifications import ws_manager
```

新增 helper（放在 router 定义后）：

```python
async def _broadcast_task_update(task_id: str):
    try:
        await ws_manager.broadcast({"type": "task_update", "task_id": task_id})
    except Exception:
        pass
```

将需要广播的路由改为 `async def`，在 `db.commit()` 之后、`return` 之前调用 `await _broadcast_task_update(task_id)`。涉及路由：`create_task`、`update_task`、`add_subtask`、`update_subtask`、`add_gitref`、`add_history`、`add_discussion`、`cancel_task`、`claim_task`。

以 `create_task` 为例（其余路由同样处理）：

```python
@router.post("", response_model=dict)
async def create_task(body: dict, db: Session = Depends(get_session)):
    t = Task(...)  # 保持现有构造
    db.add(t)
    db.commit()
    db.refresh(t)
    await _broadcast_task_update(t.id)
    return {...}
```

> 备选（若 TestClient ws+http 并发测试不可行）：保留路由为 sync，广播改为在线程中执行：
> ```python
> def _broadcast_task_update(task_id: str):
>     try:
>         asyncio.run(ws_manager.broadcast({"type": "task_update", "task_id": task_id}))
>     except Exception:
>         pass
> ```
> 路由保持 sync `def`，调用 `_broadcast_task_update(task_id)`。优先采用此版（改动最小、与既有 sync 路由一致），测试相应调整。

**最终实现采用备选（sync + asyncio.run），路由保持 sync def 不变。**

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ws.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/tasks.py tests/test_ws.py
git commit -m "feat: 任务变更 WS 广播"
```

---

### Task 14: 全量回归与清理

**Files:**
- 无（验证）

- [ ] **Step 1: 运行全部测试**

Run: `python -m pytest -q`
Expected: 全部 PASS（含既有 32 个 + 新增用例）

- [ ] **Step 2: MCP stdio 冒烟**

```bash
$input = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"s","version":"0"}}}' + "`n" + '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
$input | python -m mio_taskhub.mcp_server 2>$null | Select-String "taskhub_add_discussion|taskhub_update_task"
```
Expected: 输出包含新工具名

- [ ] **Step 3: 确认 git 状态干净（除预期改动）**

Run: `git status`
Expected: 无未预期文件

- [ ] **Step 4: 提交收尾**

```bash
git add -A
git commit -m "chore: 回归验证任务细节/讨论/上下文功能"
```
