# 需求版本化与变更跟踪 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 idea 增加版本化（version + IdeaChange 历史表）与变更跟踪任务（task_kind=change_tracking 去重），形成 Idea→IdeaChange→Task→Spec→Commit 可追溯链路。

**Architecture:** Idea 表只存当前状态与 version；历史进独立 IdeaChange 表（自增 id 支持 before_id 游标分页）。PATCH /ideas/{id} 按 versioning 枚举（full/history_only/none）处理递增与留痕，仅在 full+track_change 时生成/更新一条活跃变更跟踪任务。db.py 走既有 ALTER 迁移模式补列。

**Tech Stack:** Python 3.10+, FastAPI, SQLModel, SQLite, React (Vite), FastMCP。

**Spec:** `docs/superpowers/specs/2026-08-17-requirement-versioning-design.md` (v4)

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `mio_taskhub/models.py` | TaskKind 枚举、Idea.version、IdeaChange 表、Task.task_kind | Modify |
| `mio_taskhub/db.py` | idea 加 version 列、task 加 task_kind 列（ALTER 迁移） | Modify |
| `mio_taskhub/api/ideas.py` | update_idea 版本化逻辑、变更任务 upsert、get_idea 分页 | Modify |
| `mio_taskhub/mcp_server.py` | taskhub_update_idea 透传 change_reason/versioning/track_change | Modify |
| `web/src/api.js` | getIdea 支持 params | Modify |
| `web/src/components/IdeasView.jsx` | v 徽标、变更历史、编辑入口 | Modify |
| `tests/test_ideas_api.py` | 版本化/变更任务/分页测试 | Modify |
| `tests/test_db.py` | 迁移测试 | Modify |
| `tests/test_mcp_server.py` | MCP 透传测试 | Modify |

---

### Task 1: 模型层（TaskKind / Idea.version / IdeaChange / Task.task_kind）

**Files:**
- Modify: `mio_taskhub/models.py:39`（TaskKind 枚举，加在 TaskStage 后）
- Modify: `mio_taskhub/models.py:214-222`（Idea 加 version；IdeaChange 表加在 Idea 后）
- Modify: `mio_taskhub/models.py:87-114`（Task 加 task_kind）

- [ ] **Step 1: 写失败测试**

在 `tests/test_models.py` 末尾追加：

```python
def test_idea_version_and_ideachange_models():
    from mio_taskhub.models import Idea, IdeaChange
    i = Idea(title="t")
    assert i.version == 1
    c = IdeaChange(idea_id="x", version=1, diff={"title": {"old": "A", "new": "B"}}, reason="r")
    assert c.id is None  # 自增主键，insert 后赋值
    assert c.diff == {"title": {"old": "A", "new": "B"}}


def test_task_kind_default_normal():
    from mio_taskhub.models import Task, TaskKind
    t = Task(title="t")
    assert t.task_kind == TaskKind.NORMAL
    assert TaskKind.CHANGE_TRACKING.value == "change_tracking"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_models.py::test_idea_version_and_ideachange_models tests/test_models.py::test_task_kind_default_normal -v`
Expected: FAIL（`Idea` has no attribute `version` / `Task` has no attribute `task_kind`）

- [ ] **Step 3: 实现模型**

`models.py` 在 `TaskStage` 后加：

```python
class TaskKind(str, enum.Enum):
    NORMAL = "normal"
    CHANGE_TRACKING = "change_tracking"
```

`Task` 类加字段（`max_retries: int = 3` 行后）：

```python
    task_kind: TaskKind = TaskKind.NORMAL
```

`Idea` 类加字段：

```python
    version: int = 1
```

`Idea` 类之后追加：

```python
class IdeaChange(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)  # 自增，供 before_id 游标分页（同 Event.id 模式）
    idea_id: str = Field(index=True)
    version: int                          # 该条变更发生时 idea 的版本号
    created_at: datetime = Field(default_factory=_now)
    diff: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reason: str = ""

    # diff 结构：{field: {"old": ..., "new": ...}}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/models.py tests/test_models.py
git commit -m "feat: TaskKind 枚举 + Idea.version + IdeaChange 表 + Task.task_kind"
```

---

### Task 2: db.py 迁移（idea.version / task.task_kind 列）

**Files:**
- Modify: `mio_taskhub/db.py:19-74`（`_migrate_stage_column` 加两段 ALTER）

- [ ] **Step 1: 写失败测试**

`tests/test_db.py` 末尾追加：

```python
def test_migrate_idea_version_and_task_kind_columns():
    import os
    import tempfile
    from sqlalchemy import create_engine, inspect, text
    from mio_taskhub.db import _migrate_stage_column
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = create_engine(f"sqlite:///{path}")
        with eng.connect() as conn:
            conn.execute(text("CREATE TABLE idea (id VARCHAR PRIMARY KEY, title VARCHAR)"))
            conn.execute(text("INSERT INTO idea (id, title) VALUES ('i1', 't')"))
            conn.execute(text(
                "CREATE TABLE task (id VARCHAR PRIMARY KEY, title VARCHAR, state VARCHAR)"
            ))
            conn.execute(text("INSERT INTO task (id, title, state) VALUES ('t1', 'x', 'queued')"))
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            icols = {c["name"] for c in inspect(conn).get_columns("idea")}
            assert "version" in icols
            v = conn.execute(text("SELECT version FROM idea WHERE id='i1'")).fetchone()[0]
            assert v == 1
            tcols = {c["name"] for c in inspect(conn).get_columns("task")}
            assert "task_kind" in tcols
            k = conn.execute(text("SELECT task_kind FROM task WHERE id='t1'")).fetchone()[0]
            assert k == "NORMAL"  # SQLModel str-enum 按 .name 存储（大写）
        _migrate_stage_column(eng)  # 幂等
        with eng.connect() as conn:
            icols = {c["name"] for c in inspect(conn).get_columns("idea")}
            assert "version" in icols
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_db.py::test_migrate_idea_version_and_task_kind_columns -v`
Expected: FAIL（`no such column: version`）

- [ ] **Step 3: 实现迁移**

`_migrate_stage_column` 中 task 段落（`if "idea_id" not in cols:` 块后）加：

```python
            if "task_kind" not in cols:
                conn.execute(text("ALTER TABLE task ADD COLUMN task_kind VARCHAR NOT NULL DEFAULT 'NORMAL'"))
```

task 段落之后、`# Discussion table` 注释前，追加 idea 段落：

```python
        # Idea table: add version column if missing (existing installs).
        if "idea" in tables:
            icols = {c["name"] for c in inspect(conn).get_columns("idea")}
            if "version" not in icols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))
```

注意：`tables` 集合已在函数开头计算，包含 `"idea"`；`_migrate_stage_column` 结尾已有 `conn.commit()`，无需新增。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/db.py tests/test_db.py
git commit -m "feat: 迁移 idea.version 与 task.task_kind 列"
```

---

### Task 3: update_idea 版本化 + 变更任务 upsert

**Files:**
- Modify: `mio_taskhub/api/ideas.py:1-18`（imports、_idea_json）
- Modify: `mio_taskhub/api/ideas.py:80-92`（update_idea）
- Modify: `mio_taskhub/api/ideas.py`（新增 `_upsert_change_tracking_task` helper）

- [ ] **Step 1: 写失败测试**

`tests/test_ideas_api.py` 末尾追加：

```python
def test_idea_versioning_full():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t"})).json()["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "补充"})
        assert r.status_code == 200
        assert r.json()["version"] == 2
        d = await c.get(f"/api/v1/ideas/{iid}")
        changes = d.json()["changes"]
        assert len(changes) == 1
        assert changes[0]["version"] == 2
        assert changes[0]["diff"] == {"description": {"old": "", "new": "补充"}}
    _with_client(k)


def test_idea_versioning_history_only_and_none():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t", "description": "a"})).json()["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "b", "versioning": "history_only"})
        assert r.json()["version"] == 1          # 不递增
        d = await c.get(f"/api/v1/ideas/{iid}")
        assert len(d.json()["changes"]) == 1      # 但留痕
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "c", "versioning": "none"})
        assert r.json()["version"] == 1
        d = await c.get(f"/api/v1/ideas/{iid}")
        assert len(d.json()["changes"]) == 1      # 无新增
    _with_client(k)


def test_idea_versioning_no_change_no_version():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t", "description": "a"})).json()["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "a"})
        assert r.json()["version"] == 1           # 未变化不触发
    _with_client(k)


def test_idea_versioning_invalid():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t"})).json()["id"]
        r = await c.patch(f"/api/v1/ideas/{iid}", json={"description": "x", "versioning": "bogus"})
        assert r.status_code == 422
    _with_client(k)


def test_change_tracking_task_created_and_dedup():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "需求A"})).json()["id"]
        # 拆解产生关联 task（breakdown 自动设 idea_id 与 BROKEN_DOWN）
        r = await c.post(f"/api/v1/ideas/{iid}/breakdown", json={
            "tasks": [{"ref": "t1", "title": "实现A"}]
        })
        assert r.status_code == 200
        # 第一次修改 → 生成变更任务
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "v2描述"})
        tasks = (await c.get("/api/v1/tasks")).json()
        ct = [t for t in tasks if t["idea_id"] == iid and t["title"].startswith("[变更]")]
        assert len(ct) == 1
        assert ct[0]["title"] == "[变更] 需求A v2"
        # 第二次修改 → 更新而非新建
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "v3描述"})
        tasks = (await c.get("/api/v1/tasks")).json()
        ct = [t for t in tasks if t["idea_id"] == iid and t["title"].startswith("[变更]")]
        assert len(ct) == 1
        assert ct[0]["title"] == "[变更] 需求A v3"
    _with_client(k)


def test_change_tracking_respects_flags():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "需求B"})).json()["id"]
        await c.post(f"/api/v1/ideas/{iid}/breakdown", json={"tasks": [{"ref": "t1", "title": "实现B"}]})
        # history_only → 不生成
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "h", "versioning": "history_only"})
        tasks = (await c.get("/api/v1/tasks")).json()
        assert not any(t["idea_id"] == iid and t["title"].startswith("[变更]") for t in tasks)
        # full + track_change=false → 不生成
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "f", "track_change": False})
        tasks = (await c.get("/api/v1/tasks")).json()
        assert not any(t["idea_id"] == iid and t["title"].startswith("[变更]") for t in tasks)
        # full → 生成
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "g"})
        tasks = (await c.get("/api/v1/tasks")).json()
        assert any(t["idea_id"] == iid and t["title"].startswith("[变更]") for t in tasks)
    _with_client(k)


def test_change_tracking_requires_associated_task():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "需求C"})).json()["id"]
        # 未拆解（无关联 task）→ 不生成
        await c.patch(f"/api/v1/ideas/{iid}", json={"description": "v2"})
        tasks = (await c.get("/api/v1/tasks")).json()
        assert not any(t["idea_id"] == iid and t["title"].startswith("[变更]") for t in tasks)
    _with_client(k)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_ideas_api.py::test_idea_versioning_full -v`
Expected: FAIL（`version` 不存在 / changes 缺失）

- [ ] **Step 3: 实现**

`ideas.py` imports 更新：

```python
import json as _json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import (Idea, IdeaChange, IdeaStatus, Task, TaskKind, TaskStage,
                                Discussion, DiscussionMessage, TaskState)
```

`_idea_json` 加 version：

```python
def _idea_json(i: Idea) -> dict:
    return {
        "id": i.id, "title": i.title, "description": i.description,
        "status": i.status.value, "project": i.project, "labels": i.labels,
        "version": i.version,
        "created_at": i.created_at.isoformat(), "updated_at": i.updated_at.isoformat(),
    }
```

重写 `update_idea`：

```python
@router.patch("/{idea_id}")
def update_idea(idea_id: str, body: dict, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    versioning = body.get("versioning", "full")
    if versioning not in ("full", "history_only", "none"):
        raise HTTPException(422, f"invalid versioning, expected one of full/history_only/none: {versioning}")
    track_change = bool(body.get("track_change", True))

    diff = {}
    for f in ("title", "description", "project", "labels"):
        if f in body and body[f] is not None:
            old = getattr(i, f)
            if old != body[f]:
                diff[f] = {"old": old, "new": body[f]}

    track_events = []
    if diff:
        for f, d in diff.items():
            setattr(i, f, d["new"])
        if versioning == "full":
            i.version += 1
            db.add(IdeaChange(idea_id=idea_id, version=i.version, diff=diff,
                              reason=body.get("change_reason", "")))
        elif versioning == "history_only":
            db.add(IdeaChange(idea_id=idea_id, version=i.version, diff=diff,
                              reason=body.get("change_reason", "")))
        if versioning == "full" and track_change:
            ev = _upsert_change_tracking_task(i, db)
            if ev:
                track_events.append(ev)
    i.updated_at = _now()
    event = emit_event(db, type="idea_updated", entity="idea", entity_id=i.id,
                       payload={"version": i.version})
    db.add(i)
    db.commit()
    db.refresh(i)
    broadcast_for_event(event)
    for ev in track_events:
        broadcast_for_event(ev)
    return _idea_json(i)
```

在 `update_idea` 后追加 helper：

```python
def _upsert_change_tracking_task(i: Idea, db: Session):
    """在存在关联 task 的前提下，生成/更新唯一一条活跃变更跟踪任务。返回 Event 或 None。"""
    from mio_taskhub.models import TaskState
    related = db.exec(select(Task).where(Task.idea_id == i.id)).first()
    if related is None:
        return None
    existing = db.exec(select(Task).where(
        Task.idea_id == i.id,
        Task.task_kind == TaskKind.CHANGE_TRACKING,
        Task.state.not_in([TaskState.COMPLETED, TaskState.CANCELLED]),
    )).first()
    title = f"[变更] {i.title} v{i.version}"
    summary = "；".join(
        f"{f}: {d['old']} → {d['new']}" for f, d in _last_diff(db, i.id).items()
    )
    description = f"需求已变更到 v{i.version}。请 review 已拆解任务与 spec 是否需同步。\n变更内容：{summary}"
    if existing:
        existing.title = title
        existing.description = description
        ev = emit_event(db, type="task_updated", entity="task", entity_id=existing.id,
                        payload={"title": title})
        return ev
    t = Task(
        id=str(uuid.uuid4())[:8],
        title=title,
        description=description,
        stage=TaskStage.REVIEW,
        idea_id=i.id,
        task_kind=TaskKind.CHANGE_TRACKING,
    )
    db.add(t)
    ev = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                    payload={"title": title})
    return ev


def _last_diff(db: Session, idea_id: str) -> dict:
    row = db.exec(select(IdeaChange).where(IdeaChange.idea_id == idea_id)
                  .order_by(IdeaChange.id.desc())).first()
    return row.diff if row else {}
```

说明：`_last_diff` 取最新一条 IdeaChange 的 diff 供变更任务摘要；无历史时为空。

- [ ] **Step 4: 跑全部 idea 测试确认通过**

Run: `python -m pytest tests/test_ideas_api.py -v`
Expected: PASS（含新增 7 个用例）

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/ideas.py tests/test_ideas_api.py
git commit -m "feat: idea 版本化 + 变更跟踪任务 upsert（去重）"
```

---

### Task 4: get_idea 历史分页（include_changes/before_id/limit）

**Files:**
- Modify: `mio_taskhub/api/ideas.py:55-77`（get_idea）

- [ ] **Step 1: 写失败测试**

`tests/test_ideas_api.py` 末尾追加：

```python
def test_idea_history_pagination():
    async def k(c):
        iid = (await c.post("/api/v1/ideas", json={"title": "t"})).json()["id"]
        for desc in ("a", "b", "c", "d", "e"):
            await c.patch(f"/api/v1/ideas/{iid}", json={"description": desc})
        d = (await c.get(f"/api/v1/ideas/{iid}", params={"limit": 2})).json()
        assert len(d["changes"]) == 2                 # 默认返回最新 N 条
        latest = [x["version"] for x in d["changes"]]
        assert latest == sorted(latest, reverse=True)  # 按 id 倒序 = 新→旧
        before = d["changes"][-1]["id"]
        d2 = (await c.get(f"/api/v1/ideas/{iid}", params={"before_id": before, "limit": 2})).json()
        assert len(d2["changes"]) == 2                 # 游标翻页
        assert all(x["id"] < before for x in d2["changes"])
        d3 = (await c.get(f"/api/v1/ideas/{iid}", params={"include_changes": "false"})).json()
        assert "changes" not in d3
    _with_client(k)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_ideas_api.py::test_idea_history_pagination -v`
Expected: FAIL（`changes` 缺失）

- [ ] **Step 3: 实现**

重写 `get_idea`：

```python
@router.get("/{idea_id}")
def get_idea(idea_id: str, include_changes: bool = Query(True),
             before_id: int = Query(None), limit: int = Query(20, ge=1, le=100),
             db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    out = _idea_json(i)
    rows = db.exec(select(Discussion).where(Discussion.idea_id == idea_id).order_by(Discussion.started_at)).all()
    ds = []
    for d in rows:
        msgs = db.exec(select(DiscussionMessage).where(DiscussionMessage.discussion_id == d.id).order_by(DiscussionMessage.at)).all()
        ds.append({
            "id": d.id, "topic": d.topic, "agent": d.agent, "status": d.status,
            "summary": d.summary, "conclusions": d.conclusions, "stage": d.stage,
            "started_at": d.started_at.isoformat(),
            "ended_at": d.ended_at.isoformat() if d.ended_at else None,
            "messages": [{"author": m.author, "role": m.role, "content": m.content,
                          "at": m.at.isoformat()} for m in msgs],
        })
    out["discussions"] = ds
    tasks = db.exec(select(Task).where(Task.idea_id == idea_id).order_by(Task.created_at)).all()
    out["tasks"] = [{"id": t.id, "title": t.title, "stage": t.stage.value,
                     "state": t.state.value} for t in tasks]
    if include_changes:
        q = select(IdeaChange).where(IdeaChange.idea_id == idea_id)
        if before_id is not None:
            q = q.where(IdeaChange.id < before_id)
        changes = db.exec(q.order_by(IdeaChange.id.desc()).limit(limit)).all()
        out["changes"] = [{
            "id": c.id, "version": c.version, "created_at": c.created_at.isoformat(),
            "diff": c.diff, "reason": c.reason,
        } for c in changes]
    return out
```

注意：`limit: int = Query(20, ge=1, le=100)` 需已 import Query（ideas.py:1 已有）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_ideas_api.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/ideas.py tests/test_ideas_api.py
git commit -m "feat: idea 详情历史分页（before_id 游标）"
```

---

### Task 5: MCP 透传 change_reason / versioning / track_change

**Files:**
- Modify: `mio_taskhub/mcp_server.py:596-613`（taskhub_update_idea）

- [ ] **Step 1: 写失败测试**

`tests/test_mcp_server.py` 末尾追加：

```python
def test_update_idea_versioning_tool(mcp_ctx):
    iid = _call("taskhub_add_idea", {"title": "MCP需求"})["id"]
    d = _call("taskhub_update_idea", {
        "idea_id": iid,
        "description": "v2",
        "change_reason": "补充说明",
        "versioning": "full",
        "track_change": False,
    })
    assert d["version"] == 2
    d2 = _call("taskhub_update_idea", {
        "idea_id": iid,
        "description": "v3",
        "versioning": "history_only",
    })
    assert d2["version"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_mcp_server.py::test_update_idea_versioning_tool -v`
Expected: FAIL（MCP 工具无 versioning 参数 / 返回无 version 字段）

- [ ] **Step 3: 实现**

重写 `taskhub_update_idea`：

```python
@mcp.tool(name="taskhub_update_idea", title="更新想法/需求", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_update_idea(
    idea_id: str = Field(description="想法唯一标识", min_length=1),
    title: Optional[str] = Field(default=None, description="标题"),
    description: Optional[str] = Field(default=None, description="描述"),
    status: Optional[str] = Field(default=None, description="状态：new/fermenting/formed/broken_down/archived/cancelled"),
    change_reason: Optional[str] = Field(default=None, description="变更原因（写入版本历史与变更跟踪任务）"),
    versioning: Optional[str] = Field(default="full", description="版本策略：full/history_only/none"),
    track_change: Optional[bool] = Field(default=True, description="是否生成/更新变更跟踪任务"),
) -> str:
    """更新想法内容或推进其状态（发酵/成形/已拆解），体现需求演进过程。"""
    if status is not None:
        data = await _request("POST", f"/ideas/{idea_id}/status", body={"status": status})
        return _fmt(data)
    body = {}
    if title is not None: body["title"] = title
    if description is not None: body["description"] = description
    if change_reason is not None: body["change_reason"] = change_reason
    if versioning is not None: body["versioning"] = versioning
    if track_change is not None: body["track_change"] = track_change
    data = await _request("PATCH", f"/ideas/{idea_id}", body=body)
    return _fmt(data)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP update_idea 支持 change_reason/versioning/track_change"
```

---

### Task 6: 前端 api.js（getIdea 参数）

**Files:**
- Modify: `web/src/api.js:33`

- [ ] **Step 1: 修改**

```javascript
  getIdea: (id, params) => req('GET', `/ideas/${id}` + (params ? '?' + new URLSearchParams(params).toString() : '')),
```

- [ ] **Step 2: 构建验证**

Run: `npm --prefix web run build`
Expected: BUILD OK，无报错

- [ ] **Step 3: 提交**

```bash
git add web/src/api.js
git commit -m "feat: getIdea 支持查询参数"
```

---

### Task 7: 前端 IdeasView（v 徽标 + 变更历史 + 编辑入口）

**Files:**
- Modify: `web/src/components/IdeasView.jsx`
- Modify: `web/src/App.jsx`（如有必要，不改动为佳——本轮编辑/历史展示均在 IdeasView 内完成）

- [ ] **Step 1: 列表卡片 v 徽标**

在 `idea-card__head` 中标题后加版本徽标（第 146-149 行附近）：

```jsx
                <div className="idea-card__head">
                  <span className="idea-card__title">{i.title}</span>
                  {i.version > 1 && <span className="tag tag--version">v{i.version}</span>}
                  <span className={`badge badge--${m.tone}`}>{m.label}</span>
                </div>
```

- [ ] **Step 2: 详情区版本号 + 变更历史**

在 `idea-detail__head` 内加版本号（第 164-169 行附近）：

```jsx
              <div className="idea-detail__head">
                <h3>{detail.title}</h3>
                <span className="tag tag--version">v{detail.version}</span>
                <span className={`badge badge--${(IDEA_META[detail.status] || IDEA_META.new).tone}`}>
                  {(IDEA_META[detail.status] || IDEA_META.new).label}
                </span>
              </div>
```

在描述 `idea-detail__desc` 后、`idea-detail__actions` 前加历史折叠区：

```jsx
              <div className="idea-detail__history">
                <button className="btn btn--ghost" onClick={() => setShowHistory(s => !s)}>
                  变更历史（{detail.changes?.length || 0}）{showHistory ? '▾' : '▸'}
                </button>
                {showHistory && (
                  <div className="idea-detail__changes">
                    {(detail.changes || []).map(ch => (
                      <div key={ch.id} className="change-row">
                        <span className="tag tag--version">v{ch.version}</span>
                        <span className="change-row__at">{new Date(ch.created_at).toLocaleString()}</span>
                        {ch.reason && <span className="change-row__reason">{ch.reason}</span>}
                        <span className="change-row__fields">{Object.keys(ch.diff || {}).join(', ')}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
```

- [ ] **Step 3: 编辑入口（含变更原因）**

新增 state（第 22 行 `const [err, setErr] = useState(null)` 附近）：

```jsx
  const [showHistory, setShowHistory] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editForm, setEditForm] = useState({ title: '', description: '', reason: '' })
```

在 actions 区加编辑按钮（`idea-detail__actions` 内，第 182 行归档按钮后）：

```jsx
                <button className="btn btn--ghost" onClick={() => {
                  setEditForm({ title: detail.title, description: detail.description, reason: '' })
                  setEditing(true)
                }}>编辑</button>
```

在 `breaking` 块之后、`</>` 前加编辑表单：

```jsx
              {editing && (
                <div className="idea-detail__edit">
                  <div className="idea-detail__disc-head"><span>编辑需求</span></div>
                  <input className="inp" placeholder="标题" value={editForm.title}
                         onChange={e => setEditForm({ ...editForm, title: e.target.value })} />
                  <textarea className="inp" rows={3} placeholder="描述" value={editForm.description}
                            onChange={e => setEditForm({ ...editForm, description: e.target.value })} />
                  <input className="inp" placeholder="变更原因（必填建议）" value={editForm.reason}
                         onChange={e => setEditForm({ ...editForm, reason: e.target.value })} />
                  <div className="break-actions">
                    <button className="btn btn--ghost" onClick={() => setEditing(false)}>取消</button>
                    <button className="btn btn--primary" onClick={submitEdit}
                            disabled={!editForm.title.trim()}>保存</button>
                  </div>
                </div>
              )}
```

提交函数（`submitBreakdown` 后加）：

```jsx
  const submitEdit = async () => {
    try {
      await api.updateIdea(detail.id, {
        title: editForm.title.trim(),
        description: editForm.description,
        change_reason: editForm.reason.trim(),
      })
      setEditing(false)
      await reloadDetail()
      onReload()
    } catch (e) { fail(e) }
  }
```

注意：`openDetail` 时重置 `setShowHistory(false)`（第 29-34 行）。

- [ ] **Step 4: 构建验证**

Run: `npm --prefix web run build`
Expected: BUILD OK

- [ ] **Step 5: 提交**

```bash
git add web/src/components/IdeasView.jsx
git commit -m "feat: IdeasView v 徽标 + 变更历史 + 编辑入口"
```

---

### Task 8: 全量回归 + 端到端验证

- [ ] **Step 1: 后端全量测试**

Run: `python -m pytest tests/ -q`
Expected: 228 + 新增用例全绿（约 240+）

- [ ] **Step 2: 前端构建**

Run: `npm --prefix web run build`
Expected: BUILD OK

- [ ] **Step 3: 手工端到端（Playwright 或 curl）**

Run（重启 hub 后）:
```powershell
$env:MIO_TASKHUB_DB="D:\Users\gf1913\Temp\opencode\versioning-check.db"
python -m uvicorn mio_taskhub.main:app --port 48999
```
另开终端：
```powershell
curl -s -X POST http://127.0.0.1:48999/api/v1/ideas -H "Content-Type: application/json" -d '{"title":"冒烟需求"}'
curl -s -X PATCH http://127.0.0.1:48999/api/v1/ideas/<ID> -H "Content-Type: application/json" -d '{"description":"v2","change_reason":"冒烟"}'
curl -s "http://127.0.0.1:48999/api/v1/ideas/<ID>"
```
Expected: version=2、changes 含一条、reason 出现

- [ ] **Step 4: 提交收尾（如有测试补充）**

```bash
git status
# 仅提交本步骤新增的测试或修正
```

---

## Self-Review

**Spec 覆盖核对：**
- Idea.version + IdeaChange 拆表 → Task 1 ✅
- versioning 枚举 full/history_only/none → Task 3 ✅
- history_only 不递增但留痕 → Task 3 `test_idea_versioning_history_only_and_none` ✅
- 变更任务 upsert 去重 + task_kind 显式类型 → Task 3 ✅
- 触发规则表（仅 full+track_change）→ Task 3 `test_change_tracking_respects_flags` ✅
- 判定"存在关联 Task"→ Task 3 `test_change_tracking_requires_associated_task` ✅
- before_id 游标分页 + include_changes → Task 4 ✅
- MCP 透传三参数 → Task 5 ✅
- spec 文档版本历史章节 → 已在 spec v4 落地，非代码 ✅
- 迁移：idea.version 默认 1、task.task_kind 默认 NORMAL → Task 2 ✅
- diff.keys() 推导字段（无 changed_fields）→ `_upsert_change_tracking_task` 用 `_last_diff(...).items()` ✅

**占位符扫描：** 无 TBD/TODO，所有 step 含完整代码与命令。

**类型一致性：**
- `versioning` 枚举值在 API（Task 3）、MCP（Task 5）、前端（Task 7 不传，用默认 full）一致 ✅
- `IdeaChange.id` 自增 int 在 Task 4 `before_id` 游标中一致 ✅
- `task_kind` 存储大写 name（NORMAL/CHANGE_TRACKING）在 Task 2 迁移与 Task 3 查询中一致 ✅
- `changes` 响应字段形状（id/version/created_at/diff/reason）在 Task 3 断言与 Task 4 实现一致 ✅

**注意（评审要点）：** `_upsert_change_tracking_task` 中 `Task.state.not_in([...])` 使用 SQLModel 枚举查询，SQLite 存储为 name 值（大写），查询参数用 `TaskState.COMPLETED/CANCELLED` 会自动映射，与既有代码一致。
