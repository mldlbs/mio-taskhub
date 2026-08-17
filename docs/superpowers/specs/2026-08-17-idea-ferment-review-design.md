# 想法自动发酵与完整轨迹 · 设计

> 日期：2026-08-17 ｜ 模块：mio-taskhub 想法工作台 ｜ 状态：已批准

## 背景

Idea（想法/需求）目前只有 `status` 字段（new→fermenting→formed→broken_down），发酵需手动点击推进；轨迹仅一条 `idea_status` 事件，无状态流转史、无评审记录、无讨论摘要汇聚。用户要求：

1. **agent 定时评审**：按可配置间隔把想法派单给空闲 agent 评审，全自动推进状态。
2. **完整轨迹**：评审记录、状态流转、讨论摘要、操作日志、思维链（精简要点），前端时间线展示。

## 现状

- `models.py`：`IdeaStatus` 枚举 + `can_advance` 顺序规则；`Idea` 仅有 `created_at/updated_at`。
- `api/ideas.py`：`set_idea_status` 手动推进（校验 `can_advance`）；`breakdown` 拆解后状态置 `broken_down`。
- `scheduler.py`：通用 `Scheduler`（interval + get_due_tasks + on_enqueue），目前只处理任务入队。
- `wiring.py`：启动后台任务；`mcp_server.py` 已有 20+ 工具。
- 讨论（`Discussion`）有关闭结论 `summary/conclusions`，未汇入想法轨迹。

## 设计

### 1. 数据模型（models.py）

```python
class IdeaHistory(SQLModel, table=True):
    id: Optional[int] = Field(primary_key=True)      # 自增
    idea_id: str = Field(index=True)
    kind: str        # review / status / discussion / operation
    actor: str       # agent 名 或 manual/auto
    content: str     # 核心内容（评审结论 / 状态变化 / 摘要 / 操作说明）
    reasoning: str   # 评审理由 / 思维链精简要点（review 用）
    extra: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # from→to、推荐状态、重复检测结果等
    at: datetime = Field(default_factory=_now)
```

Idea 新增：
```python
last_reviewed_at: Optional[datetime] = None
review_count: int = 0
```

`can_advance` 规则不变（new→fermenting→formed→broken_down；archived/cancelled 从任意非终态可入）。

### 2. 评审任务与调度（scheduler / wiring）

可配置项（环境变量）：
- `MIO_IDEA_REVIEW_INTERVAL_MIN`（默认 1440，即每晚一次）
- `MIO_IDEA_REVIEW_ENABLED`（默认 1）

新增 `IdeaReviewScanner(Scheduler)`（复用现有 Scheduler）：
- `get_due_tasks()` 筛选待评审想法：
  - 状态 ∈ {new, fermenting, formed}
  - `last_reviewed_at` 为 null 或距上次 ≥ interval
  - 创建后已过存活冷却（≥ 1h 才首评）
  - 无进行中 run / 无未完成评审任务（去重，同一想法只允许一个在途评审任务）
- `on_enqueue(idea_id)` 创建评审任务：
  - title=`「{idea.title}」想法评审`
  - `target_agent_type="idea-reviewer"`，`idea_id` 关联，stage=ready

工作流：agent 收到/领取任务 → `taskhub_review_idea(idea_id)` 取详情+判定清单 → 评估 → `taskhub_submit_review(idea_id, recommend, reasoning, action)` → hub 校验并推进 + 写 IdeaHistory → run 提交成功。

### 3. MCP 工具与 API

| 工具 | 作用 |
|---|---|
| `taskhub_review_idea(idea_id)` | 返回想法详情 + 讨论摘要 + 4 项判定清单（描述完整度/讨论活跃度/存活时长/重复检测） |
| `taskhub_submit_review(idea_id, recommend, reasoning, action)` | 提交评审：校验可推进、写 IdeaHistory（含思维链要点）、返回推进后状态；recommend ∈ {nothing, ferment, form, archive} |
| `taskhub_idea_history(idea_id)` | 查询想法完整轨迹（时间线） |

后端 API（ideas.py）：
- `POST /api/v1/ideas/{id}/review` — 提交评审（校验 + 轨迹写入），内部复用状态推进逻辑
- `GET /api/v1/ideas/{id}/history` — 轨迹列表
- `GET /api/v1/ideas/{id}` 响应增加 `history`、`last_reviewed_at`、`review_count`

### 4. 前端时间线（IdeasView.jsx + index.css）

- 想法详情页新增「轨迹」区块，时间线复用任务详情 `hist-row` 风格。
- 类型徽章：评审/流转/讨论/操作 + 时间 + 操作者 + 内容。
- `kind=review`：评审结论徽章 + 理由（思维链要点）。
- `kind=status`：`from → to`。
- `kind=discussion`：讨论结论摘要。
- 详情页显示 `last_reviewed_at`（上次评审时间）。

### 5. 讨论摘要自动进轨迹

`close_discussion` 时自动写一条 `kind=discussion` 的 IdeaHistory（含 summary + conclusions）。有 idea_id 的讨论才写。

## 测试

- 评审推进：agent 提交 review 后状态正确推进 + IdeaHistory 记录（含 reasoning）
- 非法评审：推荐状态不可达（如跳过 fermenting 直接 formed）→ 422
- 轨迹查询：`GET /ideas/{id}/history` 按时间排序
- 讨论关闭写轨迹
- 评审去重：已有在途评审任务时不重复派单
- 可配置：interval 环境变量生效

## 数据迁移

`Idea` 新增字段为非空默认，SQLModel 启动建表时会自动加列（SQLite ALTER）。既有数据 `last_reviewed_at=null`、`review_count=0` 自然兼容，无迁移脚本。
