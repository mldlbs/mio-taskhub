# 代码包命名空间整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理 `mio_taskhub` 包的模块命名、文件组织和导入结构，消除命名歧义和职责重叠。

**Architecture:** 不改动业务逻辑，仅做结构性调整：合并微小模块、拆分过载文件、统一命名规范、修复路由前缀不一致。

**Tech Stack:** Python, FastAPI, SQLModel

---

## 现状问题清单

| # | 问题 | 文件 | 影响 |
|---|------|------|------|
| 1 | `utils.py` 仅 5 行 `_now()`，与 `models.py` 重复 | `utils.py`, `models.py` | 冗余 |
| 2 | `notifications.py` 仅 29 行，可合并 | `notifications.py` | 过碎 |
| 3 | `auth.py` 仅 25 行，可合并 | `auth.py` | 过碎 |
| 4 | `status.py` 444 行，职责过载（状态机+依赖检查+复合标签） | `status.py` | 难维护 |
| 5 | `wiring.py` 299 行，背景调度全堆一起 | `wiring.py` | 难维护 |
| 6 | `memory.py` 缺少 `/api/v1` 前缀，URL 不一致 | `api/memory.py` | 接口不一致 |
| 7 | `ideas.py` + `ideas_enhanced.py` 路由前缀都是 `/ideas` | `api/ideas.py`, `api/ideas_enhanced.py` | 路由冲突风险 |
| 8 | `task_templates.py` vs `api/templates.py` 命名混淆 | `idea_templates.py`, `api/templates.py` | 命名歧义 |
| 9 | `migrate_v1.py` vs `migrations.py` 命名混淆 | `migrate_v1.py`, `migrations.py` | 命名歧义 |
| 10 | `git_sync.py` 用相对导入，其余用绝对导入 | `git_sync.py` | 风格不一致 |

---

## Task 1: 消除 utils.py + 统一 `_now()`

**Files:**
- Modify: `mio_taskhub/models.py:8` — 删除重复 `_now()`，改为 import
- Modify: `mio_taskhub/utils.py` — 保留 `_now()` 作为唯一定义
- Modify: `mio_taskhub/models.py` — 所有使用 `_now()` 的地方改为 `from mio_taskhub.utils import _now`

- [ ] **Step 1: 检查 models.py 中 _now() 的使用**

Read `mio_taskhub/models.py` lines 1-20 to see the current `_now()` definition and its usage pattern.

- [ ] **Step 2: 修改 models.py**

Remove the local `_now()` definition from `models.py`. Add `from mio_taskhub.utils import _now` at the top.

- [ ] **Step 3: 验证无其他模块定义 _now()**

Run: `grep -rn "def _now" mio_taskhub/` — should only find `utils.py`.

- [ ] **Step 4: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/utils.py mio_taskhub/models.py
git commit -m "refactor: remove duplicate _now(), use utils._now everywhere"
```

---

## Task 2: 合并微小模块 (notifications + auth → 内联)

**Files:**
- Delete: `mio_taskhub/notifications.py` (29 lines) — 合并到 `events.py`
- Delete: `mio_taskhub/auth.py` (25 lines) — 合并到 `main.py`
- Modify: `mio_taskhub/events.py` — 接管 `WSManager`
- Modify: `mio_taskhub/main.py` — 接管 auth 函数，删除 import

- [ ] **Step 1: 合并 notifications → events.py**

Read `mio_taskhub/notifications.py` (29 lines) and `mio_taskhub/events.py` (86 lines).

将 `WSManager` 类和 `ws_manager` 单例从 `notifications.py` 移入 `events.py`。删除 `notifications.py`。

- [ ] **Step 2: 更新 events.py 的导入**

`events.py` 原来 `from .notifications import ws_manager`。移除此 import，因为 `ws_manager` 现在在同文件内。

- [ ] **Step 3: 更新所有引用 notifications 的文件**

Search for `from mio_taskhub.notifications` or `from .notifications` and update to `from mio_taskhub.events import ws_manager`。

Files likely affected: `main.py`, `wiring.py`, `api/events.py`

- [ ] **Step 4: 合并 auth → main.py**

Read `mio_taskhub/auth.py` (25 lines). 将 `get_token()`, `generate_token()`, `make_auth_middleware()` 移入 `main.py`（在 `configure_auth()` 附近）。删除 `auth.py`。

- [ ] **Step 5: 更新 main.py 的导入**

移除 `from .auth import ...`，改为本地定义。

- [ ] **Step 6: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 7: 提交**

```bash
git add -A mio_taskhub/
git commit -m "refactor: merge notifications.py into events.py, auth.py into main.py"
```

---

## Task 3: 拆分 status.py (444 行 → 3 个聚焦模块)

**Files:**
- Create: `mio_taskhub/state_machine.py` — 核心状态机 (enums + transitions + validation)
- Create: `mio_taskhub/dependency.py` — 依赖检查 + 拓扑排序
- Create: `mio_taskhub/composite.py` — 复合标签 + UI 辅助
- Delete: `mio_taskhub/status.py` — 被上述 3 个文件替代

- [ ] **Step 1: 分析 status.py 的职责边界**

Read `mio_taskhub/status.py` (444 lines). Identify 3 clusters:
- Core: `State`, `Stage`, `ActorType`, `Transition`, `LEGAL_COMBOS`, `TRANSITIONS`, `validate_transition()`, `find_transition()`, `is_terminal()`, `is_fully_done()`
- Dependency: `dependency_satisfied()`, `normalize_depends()`, `task_deps()`, `detect_cycle()`
- Composite: `composite_status()`, `export_mapping_json()`

- [ ] **Step 2: 创建 state_machine.py**

Extract core state machine into `mio_taskhub/state_machine.py`:
- Enums: `State`, `Stage`, `ActorType`, `Transition`
- Constants: `LEGAL_COMBOS`, `TRANSITIONS`
- Functions: `validate_transition()`, `find_transition()`, `is_terminal()`, `is_fully_done()`
- Exception: `IllegalTransition`

- [ ] **Step 3: 创建 dependency.py**

Extract dependency logic into `mio_taskhub/dependency.py`:
- Functions: `dependency_satisfied()`, `normalize_depends()`, `task_deps()`
- Import from `state_machine` for `State`/`Stage`

- [ ] **Step 4: 创建 composite.py**

Extract composite/UI helpers into `mio_taskhub/composite.py`:
- Functions: `composite_status()`, `export_mapping_json()`

- [ ] **Step 5: 更新所有 status.py 的导入**

Search for `from mio_taskhub.status import` and `from .status import` across the codebase. Update each import to point to the correct new module.

Likely files: `transitions.py`, `wiring.py`, `cron_engine.py`, `planner.py`, `migrate_v1.py`, `migrations.py`, `api/tasks.py`, `api/board.py`, `api/runs.py`, `api/plans.py`

- [ ] **Step 6: 删除 status.py**

After all imports updated, delete `status.py`.

- [ ] **Step 7: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 8: 提交**

```bash
git add -A mio_taskhub/
git commit -m "refactor: split status.py into state_machine.py, dependency.py, composite.py"
```

---

## Task 4: 重命名 wiring.py → background.py

**Files:**
- Rename: `mio_taskhub/wiring.py` → `mio_taskhub/background.py`
- Update all imports referencing `wiring`

- [ ] **Step 1: 重命名文件**

```bash
git mv mio_taskhub/wiring.py mio_taskhub/background.py
```

- [ ] **Step 2: 更新 main.py**

Change `from .wiring import start_background_jobs` to `from .background import start_background_jobs` in `main.py`.

- [ ] **Step 3: 更新所有其他引用**

Search for `wiring` across codebase and update imports.

- [ ] **Step 4: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 5: 提交**

```bash
git add -A mio_taskhub/
git commit -m "refactor: rename wiring.py → background.py for clarity"
```

---

## Task 5: 修复 memory.py 路由前缀

**Files:**
- Modify: `mio_taskhub/api/memory.py` — 添加 `/api/v1` 前缀
- Modify: `mio_taskhub/main.py` — 更新 router 注册

- [ ] **Step 1: 检查当前 memory.py 的路由注册**

Read `mio_taskhub/api/memory.py` to find the router prefix.
Read `mio_taskhub/main.py` line ~68 to see current registration:
```python
app.include_router(memory.router, tags=["memory-gateway"])  # NOTE: no /api/v1 prefix!
```

- [ ] **Step 2: 修复 main.py 中的注册**

Change to:
```python
app.include_router(memory.router, prefix="/api/v1", tags=["memory"])
```

- [ ] **Step 3: 检查 memory.py 中路由路径**

If memory.py defines routes like `/api/memory/query`, simplify to `/memory/query` since the prefix is now `/api/v1`.

- [ ] **Step 4: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 5: 提交**

```bash
git add mio_taskhub/api/memory.py mio_taskhub/main.py
git commit -m "fix: add /api/v1 prefix to memory router for URL consistency"
```

---

## Task 6: 合并 ideas_enhanced.py → ideas.py

**Files:**
- Modify: `mio_taskhub/api/ideas.py` — 接管 enhanced 端点
- Delete: `mio_taskhub/api/ideas_enhanced.py`
- Modify: `mio_taskhub/main.py` — 移除 ideas_enhanced router 注册

- [ ] **Step 1: 分析 ideas_enhanced.py 的端点**

Read `mio_taskhub/api/ideas_enhanced.py` (643 lines). Identify all router endpoints and their prefix.

- [ ] **Step 2: 将 enhanced 端点移入 ideas.py**

Move all endpoint functions from `ideas_enhanced.py` into `ideas.py`. Keep the same router (both use the same `router` object or merge routers).

- [ ] **Step 3: 更新 ideas.py 的导入**

Move any unique imports from `ideas_enhanced.py` into `ideas.py`.

- [ ] **Step 4: 删除 ideas_enhanced.py**

- [ ] **Step 5: 更新 main.py**

Remove `app.include_router(ideas_enhanced.router, ...)` line.

- [ ] **Step 6: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 7: 提交**

```bash
git add -A mio_taskhub/
git commit -m "refactor: merge ideas_enhanced.py into ideas.py, eliminate duplicate router prefix"
```

---

## Task 7: 重命名 migrate_v1.py → data_fixes.py

**Files:**
- Rename: `mio_taskhub/migrate_v1.py` → `mio_taskhub/data_fixes.py`
- Update references

- [ ] **Step 1: 重命名**

```bash
git mv mio_taskhub/migrate_v1.py mio_taskhub/data_fixes.py
```

- [ ] **Step 2: 更新 main.py 或 db.py 中的引用**

Search for `migrate_v1` across codebase and update.

- [ ] **Step 3: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 4: 提交**

```bash
git add -A mio_taskhub/
git commit -m "refactor: rename migrate_v1.py → data_fixes.py for clarity"
```

---

## Task 8: 统一 git_sync.py 导入风格

**Files:**
- Modify: `mio_taskhub/git_sync.py` — 相对导入 → 绝对导入

- [ ] **Step 1: 读取 git_sync.py 的导入部分**

Read `mio_taskhub/git_sync.py` lines 1-20.

- [ ] **Step 2: 替换所有相对导入为绝对导入**

Change `from .db import ...` → `from mio_taskhub.db import ...`
Change `from .models import ...` → `from mio_taskhub.models import ...`

- [ ] **Step 3: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 4: 提交**

```bash
git add mio_taskhub/git_sync.py
git commit -m "refactor: use absolute imports in git_sync.py for consistency"
```

---

## Task 9: 重命名 idea_templates.py → idea_prompts.py

**Files:**
- Rename: `mio_taskhub/idea_templates.py` → `mio_taskhub/idea_prompts.py`
- Update all imports referencing it

- [ ] **Step 1: 重命名**

```bash
git mv mio_taskhub/idea_templates.py mio_taskhub/idea_prompts.py
```

- [ ] **Step 2: 更新引用**

Search for `idea_templates` across codebase (likely `api/ideas_enhanced.py` which we already merged in Task 6, and `seed.py`).

- [ ] **Step 3: 运行测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 4: 提交**

```bash
git add -A mio_taskhub/
git commit -m "refactor: rename idea_templates.py → idea_prompts.py to avoid confusion with task templates"
```

---

## 最终验证

- [ ] **Step 1: 全量测试**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short -k "not test_two_agents_race"`
Expected: 506 passed

- [ ] **Step 2: 检查包结构**

Run: `Get-ChildItem mio_taskhub/*.py | Select-Object Name, Length | Sort-Object Name`
Expected: 更清晰的模块命名，无微小文件，无歧义命名

- [ ] **Step 3: 检查 OpenAPI schema**

Run: `& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -c "from mio_taskhub.main import app; import warnings; warnings.filterwarnings('ignore'); s=app.openapi(); print(len([p for methods in s.get('paths',{}).values() for m,info in methods.items() if m in ('get','post','put','patch','delete')]), 'endpoints')"`
Expected: 98 endpoints (same as before)
