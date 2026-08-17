# 需求版本化与变更跟踪

日期：2026-08-17
状态：评审修订版（v2）

## 版本历史
| 版本 | 日期 | Commit | 变更内容 |
|------|------|--------|----------|
| v1 | 2026-08-17 | f27db72 | 初版 |
| v2 | 2026-08-17 | - | 评审修订：IdeaChange 拆表、结构化 diff、变更任务去重、判定改为关联 Task 存在、versioning/track_change 分离 |

## 背景

idea（想法/需求库）目前修改不留痕：PATCH 直接覆盖，看不出"从哪版变到哪版、为什么变、是否影响已拆解任务/已写 spec"。本 spec 落地需求版本化与变更跟踪，最终目标是建立需求可追溯性链路：

```
Idea (当前状态) → IdeaChange (变更历史) → Task (任务影响) → Spec (需求同步) → Git Commit (代码提交)
```

## 现状（改动面）

- `models.py`：`Idea` 表（id/title/description/status/project/labels/created_at/updated_at）
- `api/ideas.py`：`update_idea`（PATCH /ideas/{id}，改字段并 emit `idea_updated`）
- `db.py`：`_migrate_stage_column` 迁移模式 + `create_all`（新表自动建，已有表需手动 ALTER）
- `mcp_server.py`：`taskhub_update_idea`（title/description/status）
- `web/src/components/IdeasView.jsx`：想法卡片列表 + 详情
- `web/src/api.js`：`updateIdea`/`advanceIdea`

## 设计

### 1. Idea 表：只保留当前状态

```python
class Idea(SQLModel, table=True):
    ...
    version: int = 1     # 当前版本号（迁移：ALTER TABLE idea ADD COLUMN version INTEGER NOT NULL DEFAULT 1）
```

历史全部移到独立表 `IdeaChange`：

```python
class IdeaChange(SQLModel, table=True):
    id: Optional[str] = Field(default_factory=_uuid, primary_key=True)
    idea_id: str = Field(index=True)
    version: int
    created_at: datetime = Field(default_factory=_now)
    changed_fields: list = Field(default_factory=list, sa_column=Column(JSON))  # ["title", "description"]
    diff: str = Field(default="", sa_column=Column(JSON))   # {"title": {"old": "A", "new": "B"}, ...}
    reason: str = ""
```

- 一对多关系，`create_all` 自动建表，无需迁移脚本。
- `GET /ideas` 只查 Idea（不背历史）；`GET /ideas/{id}` 可选返回 `changes`（按 idea_id 查 IdeaChange）。

### 2. 修改逻辑：versioning 与 track_change 分离

`PATCH /ideas/{id}` body：

```json
{
  "title": "...", "description": "...", "project": "...", "labels": [...],
  "change_reason": "补充资源感知需求",
  "versioning": true,     // 默认 true：控制 version++ 与写历史
  "track_change": true    // 默认 true：控制变更跟踪任务
}
```

- **versioning=true**：若 title/description/project/labels 任一实际变化 → `version += 1`，插入一条 `IdeaChange`（记录 changed_fields + 结构化 diff + reason）。
- **versioning=false**：不递增版本、不写历史（供 agent 批量修正 labels / 规范化描述）。
- **track_change**：见 §3。
- 未变化的字段不触发任何写入。

### 3. 变更跟踪任务：去重（一条活跃任务）

- **判定条件**：存在关联 Task（`SELECT ... FROM task WHERE idea_id = :id`），而非 `status == BROKEN_DOWN`。状态只是提示，任务关联才是事实来源。
- **去重**：先查是否已有该 idea 的活跃变更跟踪任务：
  - `idea_id = :id AND title LIKE '[变更] %' AND state NOT IN (done, cancelled)`
  - 存在 → **更新**该任务（追加/替换描述，title 更新为最新版本号）。
  - 不存在 → **创建**新任务：`title = f"[变更] {idea.title} v{version}"`，stage=review，`idea_id=该 idea`。
- 效果：一天改 5 次 → 1 条活跃变更任务，不被淹没。
- 通过 `emit_event(task_created/task_updated)` 触发事件，前端实时可见。
- track_change=false 时跳过此逻辑。

### 4. spec 文档版本化（约定，非代码）

- spec 文件新增「版本历史」章节（本文件即范例），置于标题下方：

```markdown
## 版本历史
| 版本 | 日期 | Commit | 变更内容 |
|------|------|--------|----------|
| v1 | 2026-08-17 | a1b2c3 | 初版 |
| v2 | 2026-08-18 | d4e5f6 | 增加资源感知 |
```

- 每版对应一个 git commit，Commit 列可溯源。需求变更需更新 spec 时：追加新版本行 + git commit。
- 已有 spec 补齐 v1 行（commit 取首次提交 hash）。

### 5. MCP 同步（`taskhub_update_idea`）

- 新增可选参数：`change_reason`、`versioning`、`track_change`，透传 PATCH body。

### 6. 前端（IdeasView）

- 列表卡片：标题旁显示 `v{n}` 徽标（version > 1 时突出）。
- 详情区：版本号 + 变更历史可展开（`version · at · changed_fields · reason`）。
- 新增"编辑需求"入口（PATCH），带「变更原因」输入框（可选；最小化时保留 v 徽标 + 历史展示）。

### API 契约

- `PATCH /ideas/{id}` body 新增：`change_reason?: str`、`versioning?: bool = true`、`track_change?: bool = true`
- `_idea_json` 返回新增：`version`
- `GET /ideas/{id}` 返回新增：`changes`（IdeaChange 列表，按版本升序）
- 事件：`idea_updated` 新增；变更任务创建/更新沿用 `task_created`/`task_updated`

## 验证

- 后端 pytest：
  - PATCH 改描述 → version+1、IdeaChange 追加（diff 含 {old,new}、changed_fields 含 description）
  - 连续改 3 次 → version=4，但活跃变更任务只有 1 条（去重，title 版本号最新）
  - `versioning: false` → version/history 不变；`track_change: false` → 不生成任务
  - idea 无关联 Task → 不生成变更任务
  - 迁移：已有 idea 表加 version 列后默认 1；GET /ideas 返回 version 但不返回 changes
- 前端 `npm run build` + Playwright：v 徽标、变更历史展开、编辑+变更原因 → 列表刷新
- 后端 `python -m pytest tests/ -q` 保持全绿

## 不做的事（YAGNI）

- 不做独立需求库表 / 快照表（IdeaChange + git 足够）。
- 不做变更审批流。
- 不做 spec 文件的程序化解析/校验（版本历史章节为人工约定）。
