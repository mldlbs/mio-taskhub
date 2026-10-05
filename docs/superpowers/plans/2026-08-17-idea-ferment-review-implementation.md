# 想法自动发酵与完整轨迹 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Idea 由 agent 定时评审全自动推进状态（new→fermenting→formed→broken_down），并将评审/流转/讨论/操作全部沉淀为完整轨迹，前端时间线展示。

**Architecture:** 新增 `IdeaHistory` 表 + `Idea.last_reviewed_at/review_count` 元数据；所有状态变更统一走 `transition_idea_status()` 单一入口（写 `kind=status` 轨迹）；新增 `IdeaReviewScanner`（复用现有 Scheduler）按可配置间隔派发 `task_kind=idea_review` 评审任务给 agent；`submit_review` 单事务完成"状态推进 + review 轨迹 + 元数据更新"；`close_discussion` 自动写 `kind=discussion` 轨迹。前端想法详情页加时间线 + 上次评审时间。

**Tech Stack:** Python 3.12 / FastAPI / SQLModel / SQLite、MCP (FastMCP)、React (Vite) + 既有 `.drawer__timeline/.hist-row` 样式复刻。

---
## File Structure

- `mio_taskhub/models.py` — `TaskKind.REVIEW`、`IdeaHistory` 表、`Idea.last_reviewed_at/review_count`
- `mio_taskhub/db.py` — idea 表迁移（`_migrate_stage_column` 内补两列）
- `mio_taskhub/api/ideas.py` — `transition_idea_status`、review/history 端点、`_idea_json` 扩展
- `mio_taskhub/api/discussions.py` — 关闭讨论写 `kind=discussion` 轨迹
- `mio_taskhub/idea_review.py`（新）— `IdeaReviewScanner` + 配置 + 去重
- `mio_taskhub/wiring.py` — 启动评审扫描器
- `mio_taskhub/mcp_server.py` — `taskhub_review_idea` / `taskhub_submit_review` / `taskhub_idea_history`
- `web/src/api.js`、`web/src/components/IdeasView.jsx` — 时间线 + 上次评审时间
- `tests/test_idea_review.py`（新）、`tests/test_idea_review_scanner.py`（新）、`tests/test_mcp_server.py`、`tests/test_db.py`

测试命令（在 `E:\work\code\agent-dev\mio-taskhub` 目录内执行，勿在仓库根跑，会 ImportPathMismatchError）：
```bash
C:/Python312/python.exe -m pytest tests -q
```
前端验证命令：`cd web && npm run build`（产出 `dist/assets/index-*.js`，hub 静态目录直接读盘）。

### 关键事实（实现前必须知道）
- **enum 存取**：`str`-enum 按 `.name`（大写，如 `"NORMAL"`）入库；`Task.task_kind == TaskKind.REVIEW` 与字符串小写值比较均可匹配。
- **原子性来源**：`get_session` 用 `with Session(engine)`，端点任何未 commit 的改动在异常时随 `Session.close()` 回滚。review 端点只 commit 一次即天然单事务。
- **`Task.id` 有默认 `_uuid()`**，不传也行；但 `Task.title` 必填。
- **`_idea_json` 被多端点复用**，扩展它即让 list/get/status 全部带新字段。
- **`IdeaStatus.can_advance` 允许 new→broken_down**（跳档），现有 breakdown 测试依赖此行为，不得回退。

---

### Task 1: 数据模型 — TaskKind.REVIEW + IdeaHistory + Idea 元数据

**Files:**
- Modify: `mio_taskhub/models.py:63-65`（TaskKind 枚举）、`mio_taskhub/models.py:219-228`（Idea）、`mio_taskhub/models.py:230-238` 之后（IdeaHistory）
- Test: `tests/test_ideas_api.py`（复用运行机制，端正点行为）

- [ ] **Step 1: 写失败测试** — 在 `tests/test_ideas_api.py` 末尾追加：

```python
def test_idea_json_has_review_metadata():
    async def k(c):
        r = await c.post("/api/v1/ideas", json={"title": "meta"})
        assert r.status_code == 200
        body = r.json()
        assert body["last_reviewed_at"] is None
        assert body["review_count"] == 0
    _with_client(k)
```

- [ ] **Step 2: 运行确认失败**

Run: `C:/Python312/python.exe -m pytest tests/test_ideas_api.py::test_idea_json_has_review_metadata -q`
Expected: FAIL（KeyError: 'last_reviewed_at'）

- [ ] **Step 3: 实现** — `models.py` 三处修改 + `ideas.py` 的 `_idea_json` 扩展：

TaskKind 枚举追加成员：
```python
class TaskKind(str, enum.Enum):
    NORMAL = "normal"
    CHANGE_TRACKING = "change_tracking"
    REVIEW = "idea_review"
```

Idea 类追加两字段（`description`/`labels` 之间）：
```python
class Idea(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    title: str
    description: str = ""
    status: IdeaStatus = IdeaStatus.NEW
    version: int = 1
    project: str = ""
    labels: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_reviewed_at: Optional[datetime] = None
    review_count: int = 0
```

文件末尾新增表：
```python
class IdeaHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)   # 自增
    idea_id: str = Field(index=True)
    kind: str        # review / status / discussion / operation
    actor: str = ""
    content: str = ""
    reasoning: Optional[str] = None   # 评审依据 / 决策摘要（结论性）
    extra: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    at: datetime = Field(default_factory=_now)
```

`ideas.py` 的 `_idea_json`（第 13-18 行）追加两键，使 TestClient 断言可见：
```python
def _idea_json(i: Idea) -> dict:
    return {
        "id": i.id, "title": i.title, "description": i.description,
        "status": i.status.value, "project": i.project, "labels": i.labels,
        "created_at": i.created_at.isoformat(), "updated_at": i.updated_at.isoformat(),
        "last_reviewed_at": i.last_reviewed_at.isoformat() if i.last_reviewed_at else None,
        "review_count": i.review_count,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `C:/Python312/python.exe -m pytest tests/test_ideas_api.py -q`
Expected: PASS（原有 idea/discussion 测试不受影响）

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/models.py tests/test_ideas_api.py
git commit -m "feat: IdeaHistory 表 + TaskKind.REVIEW + Idea 评审元数据字段"
```

---

### Task 2: DB 迁移 — idea 表补两列

**Files:**
- Modify: `mio_taskhub/db.py:59-63`（`if "idea" in tables:` 块）
- Test: `tests/test_db.py:177`（追加一个迁移测试）

- [ ] **Step 1: 写失败测试** — 在 `tests/test_db.py` 末尾追加：

```python
def test_migrate_idea_review_columns():
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
            conn.commit()
        _migrate_stage_column(eng)
        with eng.connect() as conn:
            icols = {c["name"] for c in inspect(conn).get_columns("idea")}
            assert "last_reviewed_at" in icols
            assert "review_count" in icols
            v = conn.execute(text("SELECT review_count FROM idea WHERE id='i1'")).fetchone()[0]
            assert v == 0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
```

- [ ] **Step 2: 运行确认失败**

Run: `C:/Python312/python.exe -m pytest tests/test_db.py::test_migrate_idea_review_columns -q`
Expected: FAIL（AssertionError: 'last_reviewed_at' not in icols）

- [ ] **Step 3: 实现** — `db.py` idea 块追加：

```python
        if "idea" in tables:
            icols = {c["name"] for c in inspect(conn).get_columns("idea")}
            if "version" not in icols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))
            if "last_reviewed_at" not in icols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN last_reviewed_at DATETIME"))
            if "review_count" not in icols:
                conn.execute(text("ALTER TABLE idea ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0"))
```

- [ ] **Step 4: 运行确认通过**

Run: `C:/Python312/python.exe -m pytest tests/test_db.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/db.py tests/test_db.py
git commit -m "feat: idea 表迁移 last_reviewed_at/review_count 列"
```

---

### Task 3: 统一状态入口 transition_idea_status + history 端点（读轨迹）

**Files:**
- Modify: `mio_taskhub/api/ideas.py` — 新增 `transition_idea_status`；重构 `set_idea_status`（96-112）、`breakdown_idea`（170-171）；新增 `GET /{idea_id}/history`（读轨迹，便于立刻验证 transition 落库）
- Test: `tests/test_idea_review.py`（新文件，本任务先建；TestClient 风格同 `tests/test_api.py`）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_idea_review.py`：

```python
from fastapi.testclient import TestClient
from sqlmodel import Session
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import IdeaHistory

client = TestClient(app)


def _mk(title="t", **kw):
    return client.post("/api/v1/ideas", json={"title": title, **kw}).json()


def _history(iid):
    return client.get(f"/api/v1/ideas/{iid}/history").json()


def test_manual_status_writes_history():
    iid = _mk()["id"]
    client.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
    client.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
    h = _history(iid)
    assert h["count"] == 2
    status_rows = [x for x in h["items"] if x["kind"] == "status"]
    assert len(status_rows) == 2
    assert status_rows[0]["extra"]["from"] == "fermenting"
    assert status_rows[0]["extra"]["to"] == "formed"


def test_breakdown_writes_status_history():
    iid = _mk()["id"]
    client.post(f"/api/v1/ideas/{iid}/status", json={"status": "formed"})
    r = client.post(f"/api/v1/ideas/{iid}/breakdown", json={"tasks": [{"title": "a"}]})
    assert r.status_code == 200
    h = _history(iid)
    status_rows = [x for x in h["items"] if x["kind"] == "status"]
    assert any(x["extra"]["to"] == "broken_down" for x in status_rows)


def test_cancel_manual_writes_history():
    iid = _mk()["id"]
    r = client.post(f"/api/v1/ideas/{iid}/status", json={"status": "cancelled"})
    assert r.status_code == 200
    h = _history(iid)
    assert h["items"][0]["extra"]["to"] == "cancelled"


def test_history_pagination():
    # 建 15 个想法，各推进一次 → 每条 idea 1 条 status 轨迹；取一个 idea 造 15 条评审轨迹
    iid = _mk()["id"]
    with Session(engine) as s:
        for n in range(15):
            h = IdeaHistory(idea_id=iid, kind="review", actor="agent",
                            content=f"r{n}", extra={"recommend": "nothing"})
            s.add(h)
        s.commit()
    p1 = client.get(f"/api/v1/ideas/{iid}/history", params={"page": 1, "page_size": 10}).json()
    assert p1["count"] == 15 and len(p1["items"]) == 10
    p2 = client.get(f"/api/v1/ideas/{iid}/history", params={"page": 2, "page_size": 10}).json()
    assert len(p2["items"]) == 5


def test_get_idea_does_not_inline_history():
    iid = _mk()["id"]
    client.post(f"/api/v1/ideas/{iid}/status", json={"status": "fermenting"})
    d = client.get(f"/api/v1/ideas/{iid}").json()
    assert "history" not in d
```

- [ ] **Step 2: 运行确认失败**

Run: `C:/Python312/python.exe -m pytest tests/test_idea_review.py -q`
Expected: FAIL（/history 404，尚未实现）

- [ ] **Step 3: 实现** — `ideas.py`：

顶部 import 增加 `IdeaHistory` 与 `func`（`from sqlalchemy import func`）。在 `_idea_json` 后新增：

```python
def transition_idea_status(i: Idea, dst: IdeaStatus, db: Session,
                           actor: str = "manual", source: str = "manual") -> IdeaStatus:
    """唯一状态变更入口：校验 can_advance → 修改状态 → 写 kind=status 轨迹。"""
    if not IdeaStatus.can_advance(i.status, dst):
        raise HTTPException(422, f"cannot advance from {i.status.value} to {dst.value}")
    src = i.status.value
    i.status = dst
    i.updated_at = _now()
    db.add(IdeaHistory(idea_id=i.id, kind="status", actor=actor,
                       content=f"{src} → {dst}",
                       extra={"from": src, "to": dst.value, "source": source}))
    return dst
```

新增只读 history 端点（`get_idea` 之后插入；`Page` 用 `int = Query(...)`）：
```python
@router.get("/{idea_id}/history")
def idea_history(idea_id: str, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                 db: Session = Depends(get_session)):
    if not db.get(Idea, idea_id):
        raise HTTPException(404, "idea not found")
    total = db.exec(select(func.count()).select_from(IdeaHistory)
                    .where(IdeaHistory.idea_id == idea_id)).one()
    rows = db.exec(select(IdeaHistory)
                   .where(IdeaHistory.idea_id == idea_id)
                   .order_by(IdeaHistory.at.desc(), IdeaHistory.id.desc())
                   .offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "count": total, "page": page, "page_size": page_size,
        "items": [{
            "id": h.id, "idea_id": h.idea_id, "kind": h.kind, "actor": h.actor,
            "content": h.content, "reasoning": h.reasoning, "extra": h.extra,
            "at": h.at.isoformat(),
        } for h in rows],
    }
```

重构 `set_idea_status`：
```python
@router.post("/{idea_id}/status")
def set_idea_status(idea_id: str, body: dict, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    try:
        dst = IdeaStatus(body.get("status", ""))
    except ValueError:
        raise HTTPException(400, f"invalid status, expected one of {[e.value for e in IdeaStatus]}")
    dst = transition_idea_status(i, dst, db, actor=body.get("actor", "manual"), source="manual")
    event = emit_event(db, type="idea_status", entity="idea", entity_id=i.id,
                       payload={"status": dst.value})
    db.add(i); db.commit(); db.refresh(i)
    broadcast_for_event(event)
    return _idea_json(i)
```

重构 `breakdown_idea` 中状态置位（原 170-171 行 `i.status = IdeaStatus.BROKEN_DOWN; i.updated_at = _now()`）：
```python
        transition_idea_status(i, IdeaStatus.BROKEN_DOWN, db, actor="auto", source="breakdown")
```

- [ ] **Step 4: 运行确认通过**

Run: `C:/Python312/python.exe -m pytest tests/test_idea_review.py tests/test_ideas_api.py tests/test_breakdown.py -q`
Expected: PASS（含既有 `test_idea_status_flow` 倒退报 422、breakdown 重复 409 等不受影响）

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/ideas.py tests/test_idea_review.py
git commit -m "feat: transition_idea_status 唯一状态入口（手动/breakdown 写 kind=status 轨迹）"
```

---

### Task 4: review / history API + get_idea 扩展

**Files:**
- Modify: `mio_taskhub/api/ideas.py` — `_idea_json`、新增 `POST /{idea_id}/review`
- Test: `tests/test_idea_review.py`（history 分页/不内嵌测试已在 Task 3 通过）

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_idea_review.py`：

```python
def test_review_advances_and_records():
    iid = _mk()["id"]
    r = client.post(f"/api/v1/ideas/{iid}/review",
                    json={"recommend": "ferment", "reasoning": "描述完整，讨论活跃"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "fermenting"
    assert body["review_count"] == 1
    assert body["last_reviewed_at"] is not None
    h = _history(iid)
    kinds = [x["kind"] for x in h["items"]]
    assert kinds == ["review", "status"]          # 新的在前
    review = h["items"][0]
    assert review["reasoning"] == "描述完整，讨论活跃"
    assert review["extra"]["recommend"] == "ferment"
```

```python
def test_review_nothing_records_without_transition():
    iid = _mk()["id"]
    r = client.post(f"/api/v1/ideas/{iid}/review",
                    json={"recommend": "nothing", "reasoning": "还需发酵"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "new"                # 不推进
    assert body["review_count"] == 1
    assert body["last_reviewed_at"] is not None
    h = _history(iid)
    assert h["items"][0]["kind"] == "review"
```

```python
def test_review_invalid_target_422_and_unchanged():
    iid = _mk()["id"]                              # new → form 跳档
    r = client.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "form"})
    assert r.status_code == 422
    d = client.get(f"/api/v1/ideas/{iid}").json()
    assert d["status"] == "new"
    assert d["review_count"] == 0
    assert d["last_reviewed_at"] is None
    assert _history(iid)["count"] == 0
```

```python
def test_review_archive_allowed():
    iid = _mk()["id"]
    r = client.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "archive", "reasoning": "不需要"})
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
```

```python
def test_review_invalid_recommend_400():
    iid = _mk()["id"]
    r = client.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "cancel"})  # cancel 属人工
    assert r.status_code == 400
```

```python
def test_review_atomic_rollback(monkeypatch):
    iid = _mk()["id"]
    import mio_taskhub.api.ideas as ideas_mod

    def boom(*a, **k):
        raise RuntimeError("history write failed")

    monkeypatch.setattr(ideas_mod, "IdeaHistory", boom)
    r = client.post(f"/api/v1/ideas/{iid}/review", json={"recommend": "ferment"})
    assert r.status_code == 500
    d = client.get(f"/api/v1/ideas/{iid}").json()
    assert d["status"] == "new"
    assert d["review_count"] == 0
    assert d["last_reviewed_at"] is None
    h = _history(iid)
    assert h["count"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `C:/Python312/python.exe -m pytest tests/test_idea_review.py -q`
Expected: FAIL（/review 404）

- [ ] **Step 3: 实现** — `ideas.py`（`_idea_json` 已在 Task 1 扩展，勿重复）：

新增 review 端点（放在 `set_idea_status` 之后）：

```python
@router.post("/{idea_id}/review")
def submit_review(idea_id: str, body: dict, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    recommend = (body.get("recommend") or "").strip()
    reasoning = body.get("reasoning") or None
    actor = body.get("actor") or "agent"
    if recommend not in ("nothing", "ferment", "form", "archive"):
        raise HTTPException(400, "invalid recommend, expected nothing/ferment/form/archive")
    dst_map = {"ferment": IdeaStatus.FERMENTING, "form": IdeaStatus.FORMED,
               "archive": IdeaStatus.ARCHIVED}
    dst = dst_map.get(recommend)                 # nothing → None，不推进
    if dst is not None:
        transition_idea_status(i, dst, db, actor=actor, source="review")
    i.last_reviewed_at = _now()
    i.review_count += 1
    i.updated_at = _now()
    db.add(IdeaHistory(idea_id=i.id, kind="review", actor=actor,
                       content=f"recommend={recommend}",
                       reasoning=reasoning, extra={"recommend": recommend}))
    event = emit_event(db, type="idea_reviewed", entity="idea", entity_id=idea_id,
                       payload={"recommend": recommend})
    db.add(i); db.commit(); db.refresh(i)
    broadcast_for_event(event)
    return _idea_json(i)
```

`get_idea`（55-77）不变（不内嵌 history；`_idea_json` 已带元数据）。

- [ ] **Step 4: 运行确认通过**

Run: `C:/Python312/python.exe -m pytest tests/test_idea_review.py tests/test_ideas_api.py -q`
Expected: PASS（含原子性、nothing、422、分页、archive、非法 recommend）

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/ideas.py tests/test_idea_review.py
git commit -m "feat: 想法评审提交/review 轨迹 API（单事务原子）"
```

---

### Task 5: 讨论关闭写 kind=discussion 轨迹

**Files:**
- Modify: `mio_taskhub/api/discussions.py:98-111`（close）与 26-54（create 带 conclusions 即算关闭）
- Test: `tests/test_idea_review.py`

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_idea_review.py`：

```python
def test_discussion_close_writes_idea_history():
    iid = _mk()["id"]
    did = client.post("/api/v1/discussions",
                      json={"idea_id": iid, "topic": "怎么做"}).json()["id"]
    client.post(f"/api/v1/discussions/{did}/close",
                json={"conclusions": "做 Idea 表", "summary": "一轮"})
    h = _history(iid)
    rows = [x for x in h["items"] if x["kind"] == "discussion"]
    assert len(rows) == 1
    assert rows[0]["reasoning"] == "做 Idea 表"
    assert rows[0]["extra"]["discussion_id"] == did
```

- [ ] **Step 2: 运行确认失败**

Run: `C:/Python312/python.exe -m pytest tests/test_idea_review.py::test_discussion_close_writes_idea_history -q`
Expected: FAIL（无 discussion 轨迹）

- [ ] **Step 3: 实现** — `discussions.py`：

顶部 import 增加 `IdeaHistory`。新增 helper 并在 close 时调用（有 idea_id 才写）：

```python
def _write_discussion_history(db: Session, d: Discussion):
    if not d.idea_id:
        return
    db.add(IdeaHistory(idea_id=d.idea_id, kind="discussion", actor=d.agent or "auto",
                       content=f"讨论结束：{d.topic}",
                       reasoning=d.conclusions or None,
                       extra={"discussion_id": d.id, "summary": d.summary,
                              "conclusions": d.conclusions}))
```

`close_discussion` 在 `db.add(d)` 之前插入 `_write_discussion_history(db, d)`（同事务 commit）。

同时 `create_discussion` 里，当 `status == "closed"`（即传了 conclusions）时也写：
```python
    db.add(d); db.commit(); db.refresh(d)
    _write_discussion_history(db, d)
```
但注意 `create_discussion` 的 `d.id` 需在 refresh 后才有（已有 refresh）。将调用放在 refresh 之后、messages 循环之前即可（`_write_discussion_history` 读 d.id/d.idea_id）。

- [ ] **Step 4: 运行确认通过**

Run: `C:/Python312/python.exe -m pytest tests/test_idea_review.py tests/test_ideas_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/api/discussions.py tests/test_idea_review.py
git commit -m "feat: 讨论关闭自动写 kind=discussion 想法轨迹"
```

---

### Task 6: IdeaReviewScanner 调度器 + wiring 接入

**Files:**
- Create: `mio_taskhub/idea_review.py`
- Modify: `mio_taskhub/wiring.py:206-211`（`start_background_jobs`）
- Modifies: `mio_taskhub/main.py:68-78`（返回元组长度变化，遍历 stop 逻辑不改）
- Test: `tests/test_idea_review_scanner.py`（新）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_idea_review_scanner.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `C:/Python312/python.exe -m pytest tests/test_idea_review_scanner.py -q`
Expected: FAIL（ImportError: 无 idea_review 模块）

- [ ] **Step 3: 实现** — 新建 `mio_taskhub/idea_review.py`：

```python
# mio_taskhub/idea_review.py
"""Idea 自动评审调度：按间隔派发 task_kind=idea_review 任务给 agent 评审。

配置（环境变量）：
- MIO_IDEA_REVIEW_INTERVAL_MIN     默认 1440（距上次评审）
- MIO_IDEA_REVIEW_INITIAL_DELAY_MIN 默认 60（创建后首评冷却）
- MIO_IDEA_REVIEW_ENABLED          默认 1
"""
import os
from datetime import datetime, timezone
from sqlmodel import Session, select
from mio_taskhub.db import engine
from mio_taskhub.models import (Idea, IdeaStatus, Task, TaskKind, TaskStage,
                                TaskState)
from mio_taskhub.scheduler import Scheduler
from mio_taskhub.events import emit_event, broadcast_for_event

INTERVAL_MIN = int(os.environ.get("MIO_IDEA_REVIEW_INTERVAL_MIN", "1440"))
INITIAL_DELAY_MIN = int(os.environ.get("MIO_IDEA_REVIEW_INITIAL_DELAY_MIN", "60"))
ENABLED = os.environ.get("MIO_IDEA_REVIEW_ENABLED", "1") not in ("0", "false", "")

# 评审命中的非终态（去重参照：排队/领取/运行/重试均视为在途）
_INFLIGHT_STATES = [TaskState.QUEUED, TaskState.CLAIMED,
                    TaskState.RUNNING, TaskState.RETRYING]
_REVIEWABLE = [IdeaStatus.NEW, IdeaStatus.FERMENTING, IdeaStatus.FORMED]


def _utc(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _has_inflight_review(db: Session, idea_id: str) -> bool:
    t = db.exec(select(Task).where(
        Task.idea_id == idea_id,
        Task.task_kind == TaskKind.REVIEW,
        Task.state.in_(_INFLIGHT_STATES),
    )).first()
    return t is not None


def _due_idea_ids() -> list:
    """返回需要评审（且无在途评审任务）的 idea id。"""
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        ideas = db.exec(select(Idea).where(Idea.status.in_(_REVIEWABLE))).all()
        due = []
        for i in ideas:
            if _has_inflight_review(db, i.id):
                continue
            created = _utc(i.created_at) or now
            last = _utc(i.last_reviewed_at)
            if last is None:
                if (now - created).total_seconds() >= INITIAL_DELAY_MIN * 60:
                    due.append(i.id)
            else:
                if (now - last).total_seconds() >= INTERVAL_MIN * 60:
                    due.append(i.id)
        return due


def _enqueue(idea_id: str):
    """为 idea 建一个待领取的评审任务（去重：在途则跳过）。"""
    with Session(engine) as db:
        idea = db.get(Idea, idea_id)
        if not idea:
            return
        if _has_inflight_review(db, idea_id):
            return
        t = Task(
            title=f"「{idea.title}」想法评审",
            description=("对想法进行评审：调用 taskhub_review_idea 获取详情+判定清单（描述完整度/"
                         "讨论活跃度/存活时长/重复检测），评估后调用 taskhub_submit_review 提交结论"
                         "（recommend ∈ nothing/ferment/form/archive，附 reasoning）。"),
            target_agent_type="idea-reviewer",
            priority=1,
            est_duration_min=30,
            task_kind=TaskKind.REVIEW,
            idea_id=idea_id,
            stage=TaskStage.READY,
        )
        db.add(t)
        event = emit_event(db, type="task_created", entity="task", entity_id=t.id,
                           payload={"title": t.title, "stage": t.stage.value,
                                    "task_kind": t.task_kind.value, "idea_id": idea_id})
        db.commit()
        broadcast_for_event(event)


class IdeaReviewScanner(Scheduler):
    """复用 Scheduler：get_due_tasks 返回待评审 idea id；on_enqueue 建评审任务。"""

    def __init__(self, interval: float = 60.0):
        super().__init__(interval=interval,
                         get_due_tasks=self._get_due,
                         on_enqueue=self._on_enqueue)

    def _get_due(self):
        if not ENABLED:
            return []
        return [{"id": iid} for iid in _due_idea_ids()]

    def _on_enqueue(self, idea_id: str):
        _enqueue(idea_id)
```

`wiring.py` 修改：
```python
from mio_taskhub.idea_review import IdeaReviewScanner

def start_background_jobs():
    sweep = HeartbeatSweep(get_runs=_get_runs, on_timeout=_on_timeout, on_alive=_on_alive)
    scheduler = Scheduler(get_due_tasks=_get_due_tasks, on_enqueue=_on_enqueue)
    idea_scanner = IdeaReviewScanner()
    sweep.start()
    scheduler.start()
    idea_scanner.start()
    return sweep, scheduler, idea_scanner
```

- [ ] **Step 4: 运行确认通过**

Run: `C:/Python312/python.exe -m pytest tests/test_idea_review_scanner.py -q`
Expected: PASS（4 用例）→ 再跑全量 `tests -q` 确认 wiring/main 未破坏既有调度测试。

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/idea_review.py mio_taskhub/wiring.py tests/test_idea_review_scanner.py
git commit -m "feat: IdeaReviewScanner 定时派发 idea_review 评审任务"
```

---

### Task 7: MCP 工具（3 个）

**Files:**
- Modify: `mio_taskhub/mcp_server.py` — `taskhub_breakdown_idea` 之后追加
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_mcp_server.py`：

```python
def test_review_idea_tools_flow(mcp_ctx):
    iid = _call("taskhub_add_idea", {"title": "MCP 评审"})["id"]
    ctx = _call("taskhub_review_idea", {"idea_id": iid})
    assert ctx["idea"]["title"] == "MCP 评审"
    assert "checklist" in ctx and "recommend_options" in ctx
    r = _call("taskhub_submit_review",
              {"idea_id": iid, "recommend": "ferment", "reasoning": "ok"})
    assert r["status"] == "fermenting"
    assert r["review_count"] == 1
    hist = _call("taskhub_idea_history", {"idea_id": iid})
    assert hist["count"] == 2


def test_review_idea_history_paged(mcp_ctx):
    iid = _call("taskhub_add_idea", {"title": "paged"})["id"]
    for _ in range(3):
        _call("taskhub_submit_review", {"idea_id": iid, "recommend": "nothing"})
    hist = _call("taskhub_idea_history", {"idea_id": iid, "page": 2, "page_size": 2})
    assert hist["count"] == 3
    assert len(hist["items"]) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `C:/Python312/python.exe -m pytest tests/test_mcp_server.py::test_review_idea_tools_flow -q`
Expected: FAIL（tool absent / error）

- [ ] **Step 3: 实现** — `mcp_server.py` 末尾（`taskhub_breakdown_idea` 之后、`def main()` 之前）追加三个工具：

```python
@mcp.tool(name="taskhub_review_idea", title="获取想法评审上下文", annotations={
    "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
async def taskhub_review_idea(
    idea_id: str = Field(description="想法唯一标识", min_length=1),
) -> str:
    """返回想法详情 + 讨论 + 最近轨迹 + 4 项判定清单，供评审 agent 使用。"""
    detail = await _request("GET", f"/ideas/{idea_id}")
    if "error" in detail:
        return _fmt(detail)
    hist = await _request("GET", f"/ideas/{idea_id}/history",
                          params={"page": 1, "page_size": 20})
    items = hist.get("items", []) if "error" not in hist else []
    return _fmt({
        "idea": detail,
        "discussion_count": len(detail.get("discussions", [])),
        "recent_history": items,
        "checklist": [
            "1) 描述是否完整（背景/目标/边界）？",
            "2) 讨论是否活跃（消息数与结论）？",
            "3) 存活时长是否足够发酵？",
            "4) 是否与既有想法/任务重复？",
        ],
        "recommend_options": ["nothing", "ferment", "form", "archive"],
    })


@mcp.tool(name="taskhub_submit_review", title="提交想法评审结论", annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
})
async def taskhub_submit_review(
    idea_id: str = Field(description="想法唯一标识", min_length=1),
    recommend: str = Field(description="评审结论：nothing/ferment/form/archive（hub 只推进当前状态下一档）"),
    reasoning: str = Field(default="", description="评审依据/决策摘要"),
) -> str:
    """提交评审结论。状态推进 + kind=review 轨迹 + 评审元数据在同一事务内完成。"""
    body = {"recommend": recommend, "reasoning": reasoning, "actor": "agent"}
    data = await _request("POST", f"/ideas/{idea_id}/review", body=body)
    return _fmt(data)


@mcp.tool(name="taskhub_idea_history", title="查询想法完整轨迹", annotations={
    "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False,
})
async def taskhub_idea_history(
    idea_id: str = Field(description="想法唯一标识", min_length=1),
    page: int = Field(default=1, ge=1, description="页码"),
    page_size: int = Field(default=50, ge=1, le=200, description="每页条数"),
) -> str:
    """查询想法的评审/流转/讨论/操作完整轨迹（时间线，新的在前）。"""
    data = await _request("GET", f"/ideas/{idea_id}/history",
                          params={"page": page, "page_size": page_size})
    return _fmt(data)
```

- [ ] **Step 4: 运行确认通过**

Run: `C:/Python312/python.exe -m pytest tests/test_mcp_server.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mio_taskhub/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP 工具 taskhub_review_idea/submit_review/idea_history"
```

---

### Task 8: 前端 — api.js + 想法详情时间线 + 上次评审时间

**Files:**
- Modify: `web/src/api.js:42`（breakdownIdea 后追加）
- Modify: `web/src/components/IdeasView.jsx`
- Modify: `web/src/index.css`（时间线样式已存在 `.drawer__timeline/.hist-row`，另行确认足用）

- [ ] **Step 1: api.js** — 在 `breakdownIdea` 后追加：

```js
  ideaHistory: (id, page = 1, pageSize = 20) =>
    req('GET', `/ideas/${id}/history?page=${page}&page_size=${pageSize}`),
```

- [ ] **Step 2: IdeasView.jsx** — 三处改动：

(1) import 增加 `fmtDate`：
```js
import { fmtAgo, fmtDate } from '../constants'
const KIND_LABEL = { review: '评审', status: '状态流转', discussion: '讨论', operation: '操作' }
```

(2) state 增加 `hist`；`openDetail`/`reloadDetail` 一并拉取：
```js
  const [hist, setHist] = useState(null)

  const openDetail = useCallback(async (id) => {
    setBreaking(false)
    setBreakRows([{ ref: 't1', title: '', deps: '' }])
    setSubmitting(false)
    try {
      const [d, h] = await Promise.all([api.getIdea(id), api.ideaHistory(id)])
      setDetail(d); setHist(h); setErr(null)
    } catch (e) { fail(e) }
  }, [fail])

  const reloadDetail = useCallback(async () => {
    if (!detail) return
    try {
      const [d, h] = await Promise.all([api.getIdea(detail.id), api.ideaHistory(detail.id)])
      setDetail(d); setHist(h)
    } catch (e) { /* 静默 */ }
  }, [detail])
```

(3) 详情头部展示上次评审时间（`head` 内 badge 之后）：
```jsx
                {detail.last_reviewed_at && (
                  <div className="idea-card__project">上次评审：{fmtDate(detail.last_reviewed_at)}</div>
                )}
```

(4) 讨论区块之后（`breaking` 弹层之前）插入时间线：
```jsx
              <div className="idea-detail__hist">
                <div className="idea-detail__disc-head"><span>轨迹（{hist?.count || 0}）</span></div>
                {(hist?.items || []).length === 0 && <div className="ideas__empty">还没有轨迹记录。</div>}
                {hist && hist.items && hist.items.length > 0 && (
                  <div className="drawer__timeline">
                    {hist.items.map(h => (
                      <div key={h.id} className="hist-row">
                        <span className="hist-row__dot" aria-hidden="true" />
                        <div className="hist-row__body">
                          <div className="hist-row__head">
                            <b>{KIND_LABEL[h.kind] || h.kind}</b>
                            <span className="hist-row__at mono">{fmtDate(h.at)}</span>
                            {h.actor && <span className="tag">{h.actor}</span>}
                          </div>
                          <div className="hist-row__content">{h.content}</div>
                          {h.reasoning && <pre className="hist-row__payload mono">{h.reasoning}</pre>}
                        </div>
                      </div>
                    ))}
                    {hist.count > hist.items.length && (
                      <div className="hist-row__more">… 更早的记录请用 MCP taskhub_idea_history 查询</div>
                    )}
                  </div>
                )}
              </div>
```

- [ ] **Step 3: css** — `web/src/index.css` 时间线样式后追加两块：

```css
.idea-detail__hist { margin-top: 18px; }
.hist-row__content { font-size: 12px; color: var(--ink); }
.hist-row__more { font-size: 10.5px; color: var(--ink-faint); padding: 4px 0 8px; }
```

- [ ] **Step 4: 构建验证**

Run（在 `web` 目录）: `npm run build`
Expected: `✓ built in ~2s`，产出 `dist/assets/index-*.js`；无编译错误。

- [ ] **Step 5: Commit**

```bash
git add web/src/api.js web/src/components/IdeasView.jsx web/src/index.css
git commit -m "feat: 想法详情时间线 + 上次评审时间"
```

---

## Self-Review（对照规格 v3）

| 规格要求 | 任务 |
|---|---|
| IdeaHistory 表 + Idea 元数据 | Task 1 |
| 迁移补列 | Task 2 |
| transition_idea_status 唯一入口（手动/breakdown/archive/cancel） | Task 3（cancel 走 set_idea_status→transition） |
| submit_review 单事务原子 | Task 4（一次 commit；Task 4 原子性测试） |
| recommend=nothing 也完整记录 | Task 4（`test_review_nothing_records_without_transition`） |
| archive 属 recommend；cancel 仅人工 | Task 4（`test_review_invalid_recommend_400`：recommend=cancel → 400） |
| `GET /ideas/{id}` 不内嵌 history；独立分页 `/history` | Task 4（`test_get_idea_does_not_inline_history` + 分页测试 + Task 3 `_history` 基于 /history） |
| review → 只推进一档，非法 422 | Task 4（`test_review_invalid_target_422_and_unchanged`） |
| 讨论关闭写 kind=discussion | Task 5 |
| 评审调度/去重/可配置 | Task 6（`_due_idea_ids`/`_has_inflight_review`/ENV） |
| 3 个 MCP 工具 | Task 7 |
| 前端时间线 + last_reviewed_at | Task 8 |
| 全量回归 | 各 Task Step 4 + 最终 `python -m pytest tests -q` |

**Placeholder scan:** 无 "TBD/TODO"；每步含完整代码与精确命令。类型命名跨 Task 一致：`IdeaHistory.kind`（review/status/discussion/operation）、`transition_idea_status(i, dst, db, actor=, source=)`、`_due_idea_ids()`/`_enqueue(idea_id)`、`/history` 返回 `{count,page,page_size,items}`、`_idea_json` 的 `last_reviewed_at/review_count`。

## 验收清单（最终手工过一遍）

- [ ] `C:/Python312/python.exe -m pytest tests -q` 全绿（既有 228 + 新增 ≈ 20）
- [ ] `cd web && npm run build` 成功
- [ ] 重启 hub 后托盘打开：「想法与需求」详情能看到轨迹时间线、上次评审时间；历史 channel（旧 idea）无 review 字段不报错