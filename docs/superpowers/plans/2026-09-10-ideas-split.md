# ideas.py 拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 拆分 `api/ideas.py` (1424 行, 6+ 职责) 为 4 个聚焦模块。

**Architecture:** 按业务职责拆分，共享 `_idea_json` 和 `transition_idea_status` 工具函数。

**Tech Stack:** Python, FastAPI, SQLModel

---

## 拆分方案

| 新模块 | 职责 | 行数(估) | 来源行 |
|--------|------|----------|--------|
| `api/ideas.py` | Core CRUD + breakdown + suggest | ~650 | 1-640 |
| `api/adr.py` | ADR 操作 (evolve/action/markdown) | ~250 | 645-838 |
| `api/idea_scoring.py` | 搜索/评分/批量/统计/导出 | ~400 | 839-1424 |
| `api/idea_templates.py` | 模板 CRUD + 生成 | ~200 | 839-1036 |

共享函数留在 `ideas.py`，其他模块 import 使用：
- `_idea_json(i)` — idea 序列化
- `_get_next_adr_number(db)` — ADR 序号
- `transition_idea_status(idea, dst, db)` — 状态转换

---

### Task 1: 提取 ADR 模块

**Files:**
- Create: `mio_taskhub/api/adr.py`
- Modify: `mio_taskhub/api/ideas.py` (删除 ADR 函数)
- Modify: `mio_taskhub/main.py` (注册 adr router)

- [ ] **Step 1: 创建 adr.py**

从 ideas.py 提取以下函数到 `mio_taskhub/api/adr.py`：
- `_fill_madr_fields` (678-683)
- `_record_evolve_history` (685-700)
- `_record_evolve_outbox` (702-710)
- `evolve_to_adr` (645-676) — router endpoint
- `_apply_adr_action` (741-762)
- `_apply_supersede` (764-783)
- `_record_action_history` (785-800)
- `_record_action_outbox` (802-814)
- `adr_action` (712-739) — router endpoint
- `get_adr_markdown` (816-837) — router endpoint

adr.py 需要的 imports：
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Idea, IdeaType, IdeaStatus, IdeaHistory, ChangeType, OutboxEvent, OutboxStatus
from mio_taskhub.events import emit_event
from mio_taskhub.utils import _now
from mio_taskhub.api.ideas import _idea_json, _get_next_adr_number, transition_idea_status
```

Router: `router = APIRouter(prefix="/ideas", tags=["ideas"])`

- [ ] **Step 2: 从 ideas.py 删除 ADR 函数**

删除上述函数，保留 import。

- [ ] **Step 3: 更新 main.py**

添加 `from mio_taskhub.api import adr` 和 `app.include_router(adr.router, prefix="/api/v1", tags=["ideas"])`

- [ ] **Step 4: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 5: 提交**

```bash
git add -A mio_taskhub/
git commit -m "refactor: extract adr.py from ideas.py (ADR operations)"
```

---

### Task 2: 提取搜索/评分模块

**Files:**
- Create: `mio_taskhub/api/idea_scoring.py`
- Modify: `mio_taskhub/api/ideas.py` (删除搜索/评分函数)
- Modify: `mio_taskhub/main.py` (注册 router)

- [ ] **Step 1: 创建 idea_scoring.py**

从 ideas.py 提取：
- Pydantic models: `IdeaSearchRequest`, `IdeaScoreResponse`, `BulkActionRequest` (849-877)
- `_calculate_idea_score` (1110-1182)
- `search_ideas` (1037-1108) — router endpoint
- `get_idea_score` (1184-1192) — router endpoint
- `get_all_scores` (1194-1225) — router endpoint
- `bulk_action` (1227-1284) — router endpoint
- `get_ideas_summary` (1286-1331) — router endpoint
- `get_related_ideas` (1333-1377) — router endpoint
- `export_ideas` (1379-1420) — router endpoint
- `ideas_health` (1422-1423) — router endpoint

imports:
```python
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select
from sqlalchemy import func, or_
from typing import List, Optional, Dict, Any
from mio_taskhub.db import get_session
from mio_taskhub.models import Idea, IdeaStatus, IdeaType
from mio_taskhub.api.ideas import _idea_json
```

Router: `router = APIRouter(prefix="/ideas", tags=["ideas"])`

- [ ] **Step 2: 从 ideas.py 删除搜索/评分函数**

- [ ] **Step 3: 更新 main.py**

- [ ] **Step 4: 运行测试**

- [ ] **Step 5: 提交**

```bash
git add -A mio_taskhub/
git commit -m "refactor: extract idea_scoring.py (search, scoring, bulk, export)"
```

---

### Task 3: 提取模板模块

**Files:**
- Create: `mio_taskhub/api/idea_templates.py`
- Modify: `mio_taskhub/api/ideas.py` (删除模板函数)
- Modify: `mio_taskhub/main.py` (注册 router)

- [ ] **Step 1: 创建 idea_templates.py**

从 ideas.py 提取：
- Pydantic models: `TemplateResponse`, `TemplateGenerateRequest` (839-848, 879-886)
- `list_templates` (888-907) — router endpoint
- `get_template` (909-925) — router endpoint
- `generate_from_template` (927-1035) — router endpoint

imports:
```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session
from typing import Optional
from mio_taskhub.db import get_session
from mio_taskhub.models import Task, TaskKind, TaskStage, Idea
from mio_taskhub.idea_prompts import DEFAULT_TEMPLATES, get_template_by_id, get_templates_by_category, render_template_prompt
from mio_taskhub.utils import _now
from mio_taskhub.events import emit_event
```

Router: `router = APIRouter(prefix="/ideas", tags=["ideas"])`

- [ ] **Step 2: 从 ideas.py 删除模板函数**

- [ ] **Step 3: 更新 main.py**

- [ ] **Step 4: 运行测试**

- [ ] **Step 5: 提交**

```bash
git add -A mio_taskhub/
git commit -m "refactor: extract idea_templates.py (template CRUD and generation)"
```

---

### Task 4: 最终验证

- [ ] **Step 1: 检查 ideas.py 行数**

Run: `(Get-Content 'E:\work\code\agent-dev\mio-taskhub\mio_taskhub\api\ideas.py').Count`
Expected: ~650 行

- [ ] **Step 2: 运行全量测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 505 passed

- [ ] **Step 3: 检查新模块行数**

| 模块 | 预期行数 |
|------|----------|
| ideas.py | ~650 |
| adr.py | ~250 |
| idea_scoring.py | ~400 |
| idea_templates.py | ~200 |

- [ ] **Step 4: 验证无循环依赖**

`adr.py` → imports from `ideas.py` ✅ (单向)
`idea_scoring.py` → imports from `ideas.py` ✅ (单向)
`idea_templates.py` → imports from `ideas.py` ✅ (单向)
