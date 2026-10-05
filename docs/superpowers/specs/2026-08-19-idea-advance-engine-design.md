# 想法推进引擎（Idea Advancement Engine）· 设计

> 日期：2026-08-19 ｜ 模块：mio-taskhub 想法工作台 ｜ 状态：已批准

## 背景

现有想法流程（new→fermenting→formed→broken_down）依赖「一次性评审任务 + 定时扫描」，存在四类体验问题：开会要手动开、发酵是黑盒（60min 首评/24h 周期，无人领取就没下文）、讨论结论不落地、整体流程重。

本设计将「想法自动发酵」升级为**基于事件的协作推进系统（event-driven collaborative workflow）**：用户每一次回复 = 新的推进事件，agent 每轮执行一个最小推进动作，想法逐步孵化成任务。核心不是定时器，而是**事件**。

## 心智模型

```
Idea → Discussion → Event → Agent → Task → History
```

- 用户的每一次回复 = 新的推进事件（而非「更新数据库」）
- 讨论消息本身是新的上下文，必须立即触发下一轮推进，而非等定时器
- 状态推进分阶段：创建→开会提问→用户回复→评审→成形→拆解建议→用户确认→真正建任务

## 现状

- `models.py`：`IdeaStatus` 枚举 + `can_advance`；`Idea` 有 `last_reviewed_at/review_count/version`；`IdeaHistory` 表（kind ∈ review/status/discussion/operation）。
- `api/ideas.py`：`transition_idea_status` 唯一状态入口；`submit_review` 单事务推进；`breakdown` 拆解。
- `api/discussions.py`：`create_discussion`/`messages`/`close` 均 emit 事件；关闭写 `kind=discussion` 轨迹。
- `idea_review.py`：`IdeaReviewScanner`（Scheduler 子类）按间隔派发 `task_kind=REVIEW` 任务；`_due_idea_ids`/`_has_inflight_review` 去重。
- `mcp_server.py`：`taskhub_review_idea`/`taskhub_submit_review`/`taskhub_idea_history`。
- `wiring.py`：启动 `sweep/scheduler/idea_scanner`。
- 前端 `IdeasView.jsx`：详情页手动开会、回复、结束讨论、时间线、上次评审时间。

## 设计

### 1. 数据模型（models.py）

**原则：单一持久化状态源（status）+ 推进队列（needs_advancement）+ 责任指针（pending_action），不引入第二个持久化状态机。**

Idea 新增字段：

```python
# 推进系统
needs_advancement: bool = False                 # 有待推进事件，等待 scanner 建任务
advancement_requested_at: Optional[datetime] = None  # 最近一次需要推进的时间
pending_action: str = "agent"                   # 当前需要谁出手: user / agent
breakdown_draft: Optional[dict] = None          # agent 生成的拆解建议草稿 {tasks:[...], reasoning}

# 兜底
last_advancement_remind_at: Optional[datetime] = None  # 24h 兜底上次提醒时间（限频）
```

`advance_state` 为**运行时派生**，不持久化：

```python
def derived_advance_state(idea) -> str:
    if idea.needs_advancement:
        return "waiting_agent"          # 有推进事件未处理
    if idea.pending_action == "user":
        return "awaiting_user"          # 等你回复/确认
    if idea.status == IdeaStatus.FORMED and idea.breakdown_draft:
        return "awaiting_confirm"       # 有拆解草稿待确认
    return "idle"
```

### 2. 推进状态机（运行时）

```
new
 ├─(描述不完整)→ agent 开会提问(role=ask) → pending_action=user → awaiting_user
 │                  └─(用户回复)→ mark_needs_advancement → 下一轮
 ├─(描述完整)→ 直接评审 → fermenting/formed
fermenting → 评审 → formed
formed ──(agent 生成 breakdown_draft)→ awaiting_confirm
          └─(用户确认)→ breakdown 真正建任务 → broken_down
任意非终态 → archived/cancelled（人工或 agent archive）
```

### 3. 事件驱动落点（api 层）

所有事件只调用 `mark_needs_advancement(idea)`（幂等：置 true + 刷新 `advancement_requested_at`），**不直接建任务**：

| 事件 | 落点 | 备注 |
|---|---|---|
| 想法创建 | `POST /ideas` 成功 | |
| 用户补描述/编辑 | `PATCH /ideas/{id}` 成功 | |
| 讨论新增消息 | `POST /discussions/{id}/messages` 成功 | agent 自身消息**不**触发（仅 user 消息） |
| 讨论关闭 | `POST /discussions/{id}/close` 成功 | |
| 驳回拆解草稿 | 驳回接口 | 自动插入讨论消息 + mark |

### 4. IdeaAdvanceScheduler（原 IdeaReviewScanner 改名）

- 每 30-60s 扫描：
  - `needs_advancement=true` 且无在途推进任务 → 建 `IDEA_ADVANCE` 任务（`task_kind=ADVANCE`，`target_agent_type=idea-reviewer`，stage=ready）
  - **兜底**：`needs_advancement=false` 但超过 `MIO_IDEA_ADVANCE_REMIND_MIN`（默认 1440，24h）无推进 → 仅**重新排队提醒**（置 `needs_advancement=true`），不推进
- 兜底限频：同一 idea 距 `last_advancement_remind_at` 不足周期则跳过
- 去重：存在在途 `ADVANCE` 任务（queued/claimed/running/retrying）则跳过

**一次性小步 + 断点续做**：一次 `ADVANCE` 任务只做一步（开会提问/评审/生成拆解建议/归档），完成后即使 agent 离线，进度留在 Idea 上，下个 agent 从断点续做。无长驻 run。

### 5. 配置（环境变量）

```python
MIO_IDEA_ADVANCE_ENABLED        默认 1
MIO_IDEA_ADVANCE_SCAN_MIN       默认 1      # 扫描间隔（分钟）
MIO_IDEA_ADVANCE_REMIND_MIN     默认 1440   # 兜底提醒周期（24h）
```

### 6. MCP 接口（mcp_server.py）

**新接口（主）**：

```python
@mcp.tool(name="taskhub_get_idea_context", ...)
async def taskhub_get_idea_context(idea_id) -> str:
    """返回想法详情 + 讨论 + 最近轨迹 + 派生推进状态 + 判定清单 + action 选项。"""

@mcp.tool(name="taskhub_advance_idea", ...)
async def taskhub_advance_idea(
    idea_id: str,
    action: str,            # ask_question / advance_status / create_breakdown / archive / no_change
    reasoning: str = "",
) -> str:
    """执行一个最小推进动作。"""
```

`action` 语义：
- `ask_question`：开会提问（role=ask），置 `pending_action=user`
- `advance_status`：状态推进一档（复用 `transition_idea_status`）
- `create_breakdown`：生成 `breakdown_draft`，置 `pending_action=user`
- `archive`：归档
- `no_change`：仅记录，不推进

**旧接口兼容（三阶段）**：
- **v1 兼容**：`taskhub_review_idea` → 转发 `taskhub_get_idea_context`（emit DeprecationWarning）；`taskhub_submit_review` → `map_recommend_to_action` 映射后转发 `taskhub_advance_idea`
- **v2 迁移**：前端/agent/测试/文档全部改调新接口
- **v3 删除**：移除旧接口

`map_recommend_to_action`：`nothing→no_change`、`ferment→advance_status`、`form→advance_status`、`archive→archive`（hub 仍只推进一档，越级保护保留）。

### 7. 前端（IdeasView.jsx + api.js + index.css）

1. **卡片角标**（排队三态 + 待用户红点）：
   - `derived=waiting_agent` 且无在途任务 → 灰点「更新中…」
   - 有在途 `ADVANCE` 任务未领取 → 「等待 agent…」
   - 有在途任务已领取/运行 → 「agent 正在推进…」
   - `pending_action=user` → 红点「等你回复/待确认」
2. **详情页待办区**（讨论区上方）：当前该谁出手 + 下一步提示
3. **拆解确认面板**：`breakdown_draft` 存在时显示建议子任务（可微调：改标题/优先级/依赖/删除任务；不允许新增任务或重写结构）+「确认拆解」「驳回」两按钮
   - 确认 → 调 breakdown 真正建任务
   - 驳回 → 自动插入讨论消息（驳回理由）→ mark → agent 重出草稿。**无本地编辑模式**
4. **回复即续推**：回复后界面显示「已重新排队」，无需手动操作
5. **轨迹标签**：agent 动作带「推进：ask/status/draft/archive」标签

### 8. 错误处理

- 无 agent 在线：任务停留 ready，前端显示「等待 agent…」，不失败不重复建
- 推进任务超时：走现有 run 超时重试机制（幂等，重试安全）
- `mark_needs_advancement` 幂等：已 true 只刷新时间
- agent 自身消息不触发新一轮

## 测试

- 事件落点：create/patch/message/close 各自置 `needs_advancement=true`；agent 自身消息不触发
- 调度器：扫描建任务；兜底仅重排不推进；兜底限频（24h 内一次）；在途去重
- MCP：`taskhub_advance_idea` 五 action 全路径；`taskhub_get_idea_context` 含派生状态
- 别名：旧工具转发 + DeprecationWarning + recommend→action 映射正确
- breakdown_draft：生成→确认建任务 / 驳回→讨论消息+重新排队
- 迁移：4+1 新列幂等迁移（含 `last_advancement_remind_at`），既有数据兼容
- 前端：build 通过 + 三态角标 + 确认/驳回交互冒烟
- 全量回归：`python -m pytest tests -q` 全绿 + `npm run build`

## 数据迁移

`_migrate_stage_column` idea 块幂等追加（inspect + ALTER，沿用现有模式）：

```python
needs_advancement           BOOLEAN NOT NULL DEFAULT 0
advancement_requested_at    DATETIME
pending_action              VARCHAR DEFAULT 'agent'
breakdown_draft             JSON
last_advancement_remind_at  DATETIME
```

既有 idea 兼容：`needs_advancement=false`、`pending_action=agent`、其余 null。

## 验收标准

- [ ] 想法创建/补描述/讨论消息/关闭讨论 → `needs_advancement` 立即置 true
- [ ] agent 执行 `advance_idea` 五类 action 各落一步，进度可断点续做
- [ ] 24h 兜底仅提醒，不自动推进，且同 idea 限频一次
- [ ] 拆解草稿确认→建任务；驳回→讨论消息→agent 重出草稿
- [ ] 旧 `taskhub_review_idea`/`taskhub_submit_review` 兼容转发可用
- [ ] 前端三态角标 + 待办区 + 拆解确认面板
- [ ] 全量回归通过
