# 模块设计 · 性能修复（列投影 + SQL 聚合 + 轮询收敛）

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.1 |
| 状态 | approved |
| 最后更新 | 2026-11-18 17:34 |
| 负责人 | 项目作者（单人） |
| 关联任务 | 613bc0ca |
| 上游需求 | `docs/taskhub/613bc0ca/requirement.md`（FR-1 ~ FR-8） |
| 接口契约 | `docs/taskhub/613bc0ca/api.md` |
| 讨论 | 讨论 `5ec981d9`（性能修复的取舍：零契约变更 vs 加分页） |

## 模块职责与边界

### 做什么

- 把 `GET /api/v1/tasks` 的取数从「加载全列实体」改为「**只取用到的列**」，响应构造逻辑完全不动。
- 把 `GET /api/v1/board/summary` 的计数部分从「Python 侧遍历全量实体」改为「**SQL 侧聚合**」，列表部分改为**定点限量查询**。
- 把前端 `board/summary`、`ideas` 的轮询周期从 5 s 放宽到 30 s。

### 不做什么

- 不改任何响应字段名、结构、过滤语义、错误码（FR-6、FR-8）。
- 不做分页、不做缓存、不动内存、不做日志轮转、不归档僵尸任务。
- 不动后台调度线程、reaper、cron 引擎等既有后台逻辑。

### 边界归属

| 关注点 | 归属 | 理由 |
|---|---|---|
| 取数方式（投影 / 聚合） | `api/tasks.py`、`api/board.py` | 纯服务端内部实现 |
| 响应结构 | `api/tasks.py` 现有 dict 构造逻辑 | **不动**，保证零契约变更 |
| 轮询频率 | `web/src/App.jsx` 与视图组件 | 客户端行为 |
| 实时性 | 既有 WebSocket `task_update` 推送 | 已在运行，不新建通道 |

## 输入 / 输出

### 列表端点投影字段清单

改前 `select(Task)` 取全部列；改后只取下列字段（**取数**用）：

| 字段 | 用途 |
|---|---|
| `id` | 响应字段 |
| `title` | 响应字段 |
| `state` | 响应字段 + `state` 过滤条件 |
| `stage` | 响应字段 + `stage` 过滤条件 |
| `priority` | 响应字段 |
| `target_agent_type` | 响应字段 + `agent_type` 过滤条件 |
| `created_at` | 响应字段 |
| `due_at` | 响应字段 |
| `labels` | 响应字段（**唯一必须保留的 JSON 列**） |

被剔除的 JSON 列：`files`、`deliverables`、`doc_paths`、`doc_statuses`（`labels` 需保留，因响应里有它）。

> **关键约束**：投影字段集必须与「响应 dict 里实际用到的字段」一一对应。少一个 → 响应缺字段（破坏契约）；多一个 → 白白反序列化。本表是唯一定义处，改响应字段时必须同步本表。

### board 汇总的取数清单

| 数据块 | 改前 | 改后 |
|---|---|---|
| 各阶段 / 各状态计数 | `select(Task).all()` → Python 侧 `_count_by_stage` | SQL `group by stage, state` → 字典 |
| 待领取队列 `_ready_queue` | 遍历全量实体 | SQL `where state='queued'` + `limit 20` |
| 运行中 `_running_tasks` | 遍历全量实体 | SQL `where state in (claimed, running)` + `limit 50` |
| 逾期 `_check_alerts` | 遍历全量实体 | SQL `where due_at is not null and state not in (completed, cancelled)` + `limit 5` |
| 依赖阻塞检查 | 遍历全量实体 + `task_deps(t)` | **保留遍历但只针对有 `depends_on` 的任务**（SQL 先过滤出非空依赖的任务） |
| 最近完成 `_recent_done` | 单独查询 | 不变 |
| 交付质量 `_delivery_quality` | `select(Run).limit(200)` | 不变（已有 limit，实测 47 ms） |

**单请求加载的 Task 实体目标 ≤ 100**（FR-4）。

## 详细设计

### 一、列表端点列投影（FR-1、FR-2）

现状（`api/tasks.py:265-300`）：

```python
rows = db.exec(q).all()
return [{"id": r.id, "title": r.title, ...} for r in rows]
```

`q = select(Task)` 取全列 → 2039 行 × (5 个 JSON 列反序列化) = 实测 171.7 ms。

改法：

```python
from sqlalchemy import select as sa_select

# 只取用得到的列：过滤条件用到的列 + 响应 dict 用到的列
_PROJECTION = (
    Task.id, Task.title, Task.state, Task.stage, Task.priority,
    Task.target_agent_type, Task.created_at, Task.due_at, Task.labels,
)

# 过滤条件仍用 ORM 表达式拼装（保持 state/stage/agent_type 语义不变）
q = select(*_PROJECTION)
if not cancelled:
    q = q.where(Task.state != TaskState.CANCELLED)
if state:
    q = q.where(Task.state == ts)
if stage:
    q = q.where(Task.stage == _stage)
if agent_type:
    q = q.where((Task.target_agent_type == agent_type) | (Task.target_agent_type == None))
rows = db.exec(q).all()
```

要点：

- SQLModel 的 `select(*columns)` 返回 **Row 元组**而非实体；取值改为按下标/属性名访问，**响应 dict 的键与值必须逐一保持不变**。
- 非法入参仍返回 400 业务码（`task.invalid_state` / `task.invalid_stage`）——过滤条件拼装逻辑不动，错误分支自然保留。
- `cancelled=true` 与默认共用同一实现：过滤逻辑本来就只是 `where` 的有无，**不需要第二个函数**（FR-2）。前端合并为一次调用后，服务端两个入参组合仍兼容。
- 排序：改前没有显式 `order_by`（依赖 SQLite 默认返回序），**不得新增排序**，否则会改变既有顺序（FR-8）。

预期：171.7 ms → **21.9 ms**（实测列投影），端到端 308 ms → **≤ 80 ms**，响应体 988 KB → **≤ 200 KB**。

### 二、board 汇总 SQL 聚合（FR-3、FR-4）

现状（`api/board.py:251-273`）：

```python
tasks = db.exec(select(Task)).all()     # ← 171.7 ms，两次
counts = _count_by_stage(tasks)
ready_queue = _ready_queue(db, now)
running, active = _running_tasks(db, agent)
alerts, overdue = _check_alerts(db, tasks, active, now)
```

改法（**保持输出结构与语义不变**，只换取数）：

```python
# 1) 计数：SQL 聚合（实测 5.9 ms）
rows = db.exec(
    sa_select(Task.stage, Task.state, sa_func.count()).group_by(Task.stage, Task.state)
).all()
counts = _counts_from_rows(rows)     # 新的纯函数，输入聚合行，输出与 _count_by_stage 同构

# 2) 待领取 / 运行中 / 逾期：定点限量
ready_queue = _ready_queue(db, now)  # 内部改为 select(Task).where(state=queued).limit(20)
running, active = _running_tasks(db, agent)   # 内部改为 where(state in (claimed, running)).limit(50)

# 3) 告警与逾期：只对少量任务做实体加载
alerts, overdue = _check_alerts(db, tasks_candidates, active, now)
#     tasks_candidates = select(Task).where(due_at is not null).limit(200)
#     依赖阻塞检查只针对有依赖的任务
dep_tasks = db.exec(select(Task).where(Task.depends_on != None).limit(500)).all()
```

要点：

- `_count_by_stage` 是纯函数（输入实体列表 → 输出计数）。**不删它**，改为新增一个吃聚合行的 `_counts_from_rows`，两者输出**必须逐项相等**（FR-7 的一致性验收点）。
- `_check_alerts` 的依赖阻塞分支需要 `task_deps(t)` 解析 `depends_on` JSON——该字段被投影剔除会取不到，所以依赖检查这一支**仍需实体加载**，但用 `where(depends_on IS NOT NULL)` 预先过滤，避免遍历 2039 条。
- `_delivery_quality` 与 `_recent_done` **不动**（前者已有 limit 200，实测 47 ms）。

预期：board 端到端 1008 ms → **≤ 100 ms**（内部 ORM 加载从两次全表 318 ms 降到 11.8 ms + 少量定点查询）。

### 三、前端轮询收敛（FR-5）

| 位置 | 现状 | 改后 | 理由 |
|---|---|---|---|
| `web/src/App.jsx:289` | `setInterval(() => { loadTasks(); loadIdeas() }, 5000)` | `loadTasks()` 保持 5 s；`loadIdeas()` 拆出独立 30 s 定时器 | 任务列表是主视图，保持手感；想法面板非主视图，30 s 足够 |
| `board/summary` 调用方 | 随主轮询 5 s | 独立 30 s | 看板汇总无秒级诉求 |
| `web/src/components/AppsIdeasView.jsx:887` | `setInterval(reloadDetail, 5000)` | 30000 | 详情面板 |
| `web/src/components/MemoryView.jsx:171` | `setInterval(reload, 5000)` | 30000 | 记忆面板非主视图 |

**不得改动**：

- `App.jsx:233` 的 `setInterval(tick, 250)`——WS 重连倒计时显示，与轮询无关，250 ms 是 UI 刷新需要。
- `App.jsx:162` 的 `setInterval(load, 30000)`（版本检查）、`TaskDetail.jsx:304` 的 1 s 重试倒计时。

实时性兜底：hub 的 WebSocket `task_update` 事件已在推送任务状态变化（`App.jsx:164-167` 已消费），任务列表的实时性**主要靠 WS 而非轮询**，放宽轮询不影响看板操作体验。

### 四、前端合并 tasks 调用（FR-2）

现状前端同时请求 `/tasks` 与 `/tasks?cancelled=true`——后者只是多带 318 条取消任务。改法：

- 后端不动（两个入参都保留）。
- 前端**一次请求**取全量（含取消态），在客户端按 `state !== 'cancelled'` 过滤出主列表、把取消态放进「已取消」分组。
- 若前端有「默认不展示取消任务」的其他依赖（看板计数、状态徽标），需一并从同一份数据算出，避免二次请求。

## 异常与边界情况

| 场景 | 处理 | 是否符合预期 |
|---|---|---|
| `state` 传非法值 | 仍返回 400 `task.invalid_state` | FR-8 保持 |
| `stage` 传非法值 | 仍返回 400 `task.invalid_stage` | FR-8 保持 |
| `agent_type` 传值 | 过滤语义不变（目标匹配或为空） | FR-8 保持 |
| `cancelled` 缺省 | 过滤掉 CANCELLED | FR-8 保持 |
| 投影漏字段 | 响应缺键 → **破坏契约**，验收 diff 会直接抓到 | FR-6 由 diff 验收兜底 |
| 聚合结果与改前不一致 | 视为缺陷，必须修到逐项相等 | FR-7 |
| SQLite 返回顺序 | 改前无 `order_by`，改后**不得新增**，避免顺序漂移 | FR-8 |
| 依赖阻塞检查依赖 `depends_on` | 该支仍走实体加载（`where depends_on IS NOT NULL`） | FR-4 实体数 ≤ 100 的例外（限 500 上限） |
| 任务表继续增长到 1 万行 | 投影与聚合仍为 O(1) 次扫描，耗时会线性上升但**不再有 ORM 实例化开销** | 留待后续分页立项 |

## 涉及文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `mio_taskhub/api/tasks.py` | 改 | 列表端点列投影（FR-1、FR-2、FR-8） |
| `mio_taskhub/api/board.py` | 改 | 计数改 SQL 聚合 + 定点限量查询（FR-3、FR-4） |
| `web/src/App.jsx` | 改 | 合并 tasks 调用 + ideas 轮询间隔（FR-2、FR-5） |
| `web/src/components/IdeasView.jsx` | 改 | 轮询 5 s → 30 s（FR-5） |
| `web/src/components/MemoryView.jsx` | 改 | 轮询 5 s → 30 s（FR-5） |
| `tests/test_task_list_projection.py` | 新增 | 契约一致性 + 过滤语义 + 聚合一致性用例（见 test 文档） |
