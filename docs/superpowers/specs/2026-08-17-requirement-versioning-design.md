# 需求版本化与变更跟踪

日期：2026-08-17
状态：评审修订版（v3）

## 版本历史
| 版本 | 日期 | Commit | 变更内容 |
|------|------|--------|----------|
| v1 | 2026-08-17 | f27db72 | 初版 |
| v2 | 2026-08-17 | 82cb9b6 | IdeaChange 拆表、结构化 diff、变更任务去重、判定改为关联 Task 存在、versioning/track_change 分离 |
| v3 | 2026-08-17 | d39e6c3 | diff 类型修正、删 changed_fields、versioning 枚举化、task_kind 字段、详情历史分页、spec_path 已有确认 |
| v4 | 2026-08-17 | - | history_only 禁止变更任务、task_kind 枚举化、分页改 before_id 游标（对齐 events 模式） |

## 背景

idea（想法/需求库）目前修改不留痕：PATCH 直接覆盖，看不出"从哪版变到哪版、为什么变、是否影响已拆解任务/已写 spec"。本 spec 落地需求版本化与变更跟踪，建立需求可追溯性链路：

```
Idea (当前状态) → IdeaChange (变更历史) → Task (任务影响 + spec_path) → Spec (版本历史) → Git Commit
```

## 现状（改动面）

- `models.py`：`Idea` 表；`Task.spec_path` 已存在（design 阶段强制校验）
- `api/ideas.py`：`update_idea`（PATCH /ideas/{id}）；`get_idea`
- `db.py`：`_migrate_stage_column` 迁移模式 + `create_all`（新表自动建，已有表需手动 ALTER）
- `mcp_server.py`：`taskhub_update_idea`
- `web/src/components/IdeasView.jsx`、`web/src/api.js`

## 设计

### 1. Idea 表：只保留当前状态

```python
class Idea(SQLModel, table=True):
    ...
    version: int = 1     # 当前版本号（迁移：ALTER TABLE idea ADD COLUMN version INTEGER NOT NULL DEFAULT 1）
```

历史全部移到独立表 `IdeaChange`：

```python
class TaskKind(str, enum.Enum):
    NORMAL = "normal"
    CHANGE_TRACKING = "change_tracking"

class IdeaChange(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)  # 自增，供 before_id 游标分页（同 Event.id 模式）
    idea_id: str = Field(index=True)
    version: int                          # 该条变更发生时 idea 的版本号
    created_at: datetime = Field(default_factory=_now)
    diff: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reason: str = ""

    # diff 结构：{field: {"old": ..., "new": ...}}
    # 例：{"title": {"old": "A", "new": "B"}, "description": {"old": "X", "new": "Y"}}
```

- 变更字段由 `diff.keys()` 推导，**不单独存储** `changed_fields`（避免不一致）。
- 一对多关系，`create_all` 自动建表，无需迁移脚本。
- `GET /ideas` 只查 Idea（不背历史）。

### 2. 修改逻辑：versioning 枚举 + track_change

`PATCH /ideas/{id}` body：

```json
{
  "title": "...", "description": "...", "project": "...", "labels": [...],
  "change_reason": "补充资源感知需求",
  "versioning": "full",          // 默认 "full"
  "track_change": true           // 默认 true
}
```

**`versioning` 枚举**（替代 v2 的布尔）：

| 值 | 行为 |
|---|---|
| `full` | `version += 1`，插入 IdeaChange（记录 diff + reason） |
| `history_only` | **不**递增 version，但插入 IdeaChange（留痕不 bump）——供 agent 批量规范化 description/labels 时保留原始需求 |
| `none` | 都不做（version/history 均不变） |

- 仅 title/description/project/labels 任一实际变化才处理；未变化字段不触发任何写入。
- **`track_change`**：见 §3，独立于 versioning。

### 3. 变更跟踪任务：去重 + 显式类型

- **判定条件**：存在关联 Task（`SELECT ... FROM task WHERE idea_id = :id`），而非 status 判断。
- **显式类型**：`Task.task_kind: TaskKind = TaskKind.NORMAL`（迁移：`ALTER TABLE task ADD COLUMN task_kind VARCHAR NOT NULL DEFAULT 'normal'`）。变更跟踪任务为 `TaskKind.CHANGE_TRACKING`。
- **触发规则（versioning 与 track_change 的组合）**：

| versioning | track_change | 生成变更任务 |
|---|---|---|
| `full` | true | ✅ |
| `full` | false | 否 |
| `history_only` | 任意 | **否**（不 bump 版本，任务标题的 v{n} 会与实际修改次数错位） |
| `none` | 任意 | 否 |

即仅 `versioning == "full" and track_change` 时生成。
- **去重查询**：

```sql
SELECT ... FROM task
WHERE idea_id = :id AND task_kind = 'change_tracking' AND state NOT IN ('done', 'cancelled')
```

- 存在 → **更新**该任务（title 更新为最新版本号，description 追加/替换变更摘要）。
- 不存在 → **创建**：`title = f"[变更] {idea.title} v{version}"`，`stage = review`，`idea_id = 该 idea`，`task_kind = TaskKind.CHANGE_TRACKING`。
- 一天改 5 次 → 1 条活跃变更任务。
- 事件沿用 `task_created`/`task_updated`。

### 4. spec 文档版本化（约定，非代码）

- spec 文件「版本历史」章节（本文件即范例）：

```markdown
## 版本历史
| 版本 | 日期 | Commit | 变更内容 |
|------|------|--------|----------|
| v1 | 2026-08-17 | a1b2c3 | 初版 |
| v2 | 2026-08-18 | d4e5f6 | 增加资源感知 |
```

- 每版对应一个 git commit；变更需求时追加版本行 + commit。已有 spec 补齐 v1 行（commit 取首次提交 hash）。
- `Task.spec_path` 已存在且 design 阶段强制校验，spec 文件内版本历史即可溯源，不新增 `spec_version` 字段。

### 5. MCP 同步（`taskhub_update_idea`）

- 新增可选参数：`change_reason`、`versioning`（full/history_only/none）、`track_change`，透传 PATCH body。

### 6. 前端（IdeasView）

- 列表卡片：标题旁 `v{n}` 徽标（version > 1 突出）。
- 详情区：版本号 + 变更历史（默认展示最新 N 条，见 §7）。
- 新增"编辑需求"入口（PATCH），带「变更原因」输入框。

### 7. API 契约

- `PATCH /ideas/{id}` body 新增：`change_reason?: str`、`versioning?: "full"|"history_only"|"none" = "full"`、`track_change?: bool = true`
- `_idea_json` 返回新增：`version`
- `GET /ideas/{id}` 参数新增：`include_changes: bool = true`、`before_id?: int`、`limit: int = 20`
  - 返回 `changes`（按 id 倒序；`before_id` 给定则返回 `id < before_id` 的历史，游标翻页对齐 events.py 的 after_seq 模式；不传则最新 `limit` 条）
  - 默认返回最新 20 条，避免历史无限膨胀拖重详情接口
- 事件：`idea_updated` 新增；变更任务创建/更新沿用 `task_created`/`task_updated`

## 验证

- 后端 pytest：
  - PATCH 改描述（full）→ version+1、IdeaChange.diff 含 {old,new}；diff.keys() == 变更字段
  - `history_only` → version 不变但 history 新增，且**不生成**变更任务；`none` → 两者不变
  - `full + track_change=false` → 不生成任务
  - 连续改 3 次（full）→ version=4，活跃变更任务仅 1 条（task_kind=change_tracking，title 版本号最新）
  - idea 无关联 Task → 不生成变更任务
  - `GET /ideas/{id}?limit=5` → 最多 5 条 changes；`before_id` 游标翻页返回更早历史
  - 迁移：已有 idea 表加 version 列默认 1；task 表加 task_kind 默认 'normal'
- 前端 `npm run build` + Playwright：v 徽标、变更历史展开、编辑+变更原因 → 列表刷新
- 后端 `python -m pytest tests/ -q` 保持全绿

## 不做的事（YAGNI）

- 不做独立需求库表 / 快照表（IdeaChange + git 足够）。
- 不做变更审批流。
- 不做 `Task.spec_version`（`spec_path` 已有，spec 文件内版本历史可溯源）。
- 不做 `task_kind` 之外的任务类型扩展（NORMAL/CHANGE_TRACKING 够用，其余按需加枚举成员）。
- 不做 spec 文件程序化解析/校验（版本历史章节为人工约定）。
