# 需求版本化与变更跟踪

日期：2026-08-17
状态：已确认（brainstorming 结论）

## 背景

idea（想法/需求库）目前修改不留痕：PATCH 直接覆盖，看不出"从哪版变到哪版、为什么变、是否影响已拆解任务/已写 spec"。本 spec 落地：

1. **Idea 版本化**：修改内容自动递增版本并追加变更历史。
2. **变更自动跟踪**：需求变更时自动生成「需求变更跟踪」任务，提醒 review 已排任务与 spec。
3. **spec 文档版本化**：spec 文件内维护「版本历史」章节。

## 现状（改动面）

- `models.py`：`Idea` 表（id/title/description/status/project/labels/created_at/updated_at）
- `api/ideas.py`：`update_idea`（PATCH /ideas/{id}，改字段并 emit `idea_updated`）
- `mcp_server.py`：`taskhub_update_idea`（title/description/status）
- `web/src/components/IdeasView.jsx`：想法卡片列表 + 详情
- `web/src/api.js`：`updateIdea`/`advanceIdea`

## 设计

### 1. Idea 表加版本字段

```python
class Idea(SQLModel, table=True):
    ...
    version: int = 1                       # 当前版本号
    changes: list = Field(default_factory=list, sa_column=Column(JSON))  # 变更历史
```

`changes` 元素结构：

```json
{
  "version": 2,
  "at": "2026-08-17T09:00:00",
  "what": "标题: A → B; 描述: 改写",
  "reason": "补充资源感知需求"
}
```

### 2. 修改自动递增版本 + 追加历史（`update_idea`）

- 请求体新增可选字段 `change_reason`（变更原因，供变更历史与跟踪任务使用）。
- 若 `title`/`description`/`project`/`labels` 任一实际发生变化：
  - `version += 1`
  - 追一条 changes：`{version, at, what, reason}`（what 记录变化字段摘要，reason 取 `change_reason`，缺省为空）
- 仅改 labels 也计版本（内容变化）。
- 未变化的字段不触发。

**变更自动生成跟踪任务**（条件：idea 已 BROKEN_DOWN，即已有拆解任务）：

- 自动创建任务：
  - `title = f"[变更] {idea.title} v{version}"`
  - `description`：旧版→新版摘要 + 变更原因 + 提示"review 已拆解任务与 spec 是否需同步"
  - `stage = review`（走 review 阶段提醒人）
  - `idea_id = 该 idea`
  - `depends_on = []`
- 若 idea 未拆解（无任务），不生成跟踪任务（改 labels/status 场景也不生成——status 走独立接口）。
- 通过 `emit_event(task_created)` 触发事件，前端实时可见。

**避免误生成**：PATCH 请求带 `track_change: false` 可显式跳过（供 agent 批量调整用，默认 true）。

### 3. spec 文档版本化（约定，非代码）

- spec 文件新增「版本历史」章节，置于标题下方：

```markdown
## 版本历史
| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1 | 2026-08-17 | 初版 |
| v2 | 2026-08-18 | 增加资源感知 |
```

- 需求变更需更新 spec 时：追加一行新版本，git commit 保留全文历史。
- 已有 spec 补齐 v1 行。

### 4. MCP 同步（`taskhub_update_idea`）

- 新增可选参数 `change_reason`、`track_change`。
- 透传给 PATCH body。

### 5. 前端（IdeasView）

- 列表卡片：标题旁显示 `v{n}` 徽标（version > 1 时突出）。
- 详情区：版本号 + 变更历史可展开（`version · at · what · reason`）。
- 编辑入口：新增"编辑需求"（PATCH），带「变更原因」输入框。当前 UI 无编辑按钮，需新增（可选；最小化时也可仅保留 v 徽标 + 历史展示，编辑走 MCP）。

### API 契约

- `PATCH /ideas/{id}` body 新增：`change_reason?: str`、`track_change?: bool = true`
- `_idea_json` 返回新增：`version`、`changes`
- `GET /ideas` / `GET /ideas/{id}` 均含上述字段

## 验证

- 后端 pytest：
  - PATCH 改描述 → version+1、changes 追加、track_change 时生成 review 任务
  - 未变字段不递增；`track_change: false` 不生成任务
  - idea 未拆解时不生成跟踪任务
- 前端 `npm run build` + Playwright：
  - v 徽标显示、变更历史展开
  - 编辑 + 变更原因 → 列表刷新
- 后端 `python -m pytest tests/ -q` 保持全绿。

## 不做的事（YAGNI）

- 不做独立需求库表 / 快照表（JSON 变更历史 + git 足够）。
- 不做变更审批流。
- 不做 spec 文件的程序化解析/校验（仅约定版本历史章节格式）。
