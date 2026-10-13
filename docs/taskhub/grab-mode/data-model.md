> 软件项目文档链 · 7 件套  
> 上游：[模块 Spec](docs/taskhub/grab-mode/spec.md) | 下游：[接口契约](docs/taskhub/grab-mode/api.md)  
> 约定：正文**分点 + 换行**（每点一行，`-` 列表）；指引注释处填内容，未用章节删除。

# 状态模型 / 数据模型（Data & State Model）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v0.1 |
| 最后更新 | 2026-10-07 11:50 |
| 状态 | draft |
| 负责人 | 高志谦 |
| 适用范围 | 抢单模式新增/复用的实体字段、`GrabMode` 取值、抢单相关的状态转移与不变量 |
| 明确不含 | `TaskState` / `TaskStage` 存量枚举的语义（**不修改**，只叠加抢单语义）；后台线程内部实现 |

## 1. 实体与字段

### 1.1 Task 新增字段

| 实体.字段 | 类型 | 约束 | 默认 | 说明 |
|-----------|------|------|------|------|
| `task.grab_mode` | str(enum `GrabMode`) | NOT NULL，取值 `grab`/`dispatch`/`hybrid` | 迁移 `dispatch`；建单 API `hybrid` | 调度模式。迁移默认取 `dispatch` 以保证老任务行为不变（FR-1） |
| `task.bounty` | int | NOT NULL，`0 <= bounty <= bounty_cap` | 0 | 当前悬赏（排序权重，非货币） |
| `task.base_bounty` | int | NOT NULL，`>= 0` | 0 | 初始悬赏，爬升基准 |
| `task.bounty_step` | int | NOT NULL，`1..1000` | 5 | 每阶梯涨幅 |
| `task.bounty_cap` | int | NOT NULL，`>= base_bounty` | 100 | 悬赏封顶；`<= 0` 时悬赏维度不计分（防除零） |
| `task.surge_interval` | int | NOT NULL，`30..86400` | 300 | 阶梯间隔秒 |
| `task.last_surge_level` | int | NOT NULL，`>= 0` | 0 | 已生效阶梯，悬赏幂等的唯一依据 |
| `task.pool_entered_at` | datetime | 可空 | null | 入池时刻。入池**必须**写；为空的 hybrid 触发兜底补齐 + 告警 |
| `task.grab_deadline_at` | datetime | 可空 | null | hybrid 可转派时刻；`grab` 恒为 null |
| `task.grab_window_seconds` | int | NOT NULL，`30..86400` | 600 | 抢中后开工窗口 |

> 一律**不修改** `TaskState` / `TaskStage` 的取值与既有转移表。抢单只改变「谁在什么条件下能拿到 `queued` 任务」。

### 1.2 Agent 新增字段

| 实体.字段 | 类型 | 约束 | 默认 | 说明 |
|-----------|------|------|------|------|
| `agent.grab_miss_count` | int | NOT NULL，`>= 0` | 0 | 累计失约次数，驱动匹配分惩罚与冷却 |
| `agent.grab_win_count` | int | NOT NULL，`>= 0` | 0 | 累计抢中次数（统计与看板） |
| `agent.decline_count_today` | int | NOT NULL，`>= 0` | 0 | 当日拒单次数；跨 UTC 日惰性重置 |
| `agent.decline_day` | str | 可空，UTC 日期 `YYYY-MM-DD` | null | 上一次计数所属的 UTC 日；与 `now` 不同即重置 |
| `agent.grab_cooldown_until` | datetime | 可空 | null | 抢单冷却截止时刻（软惩罚，只影响排序） |

### 1.3 GrabAttempt（新增表）

| 实体.字段 | 类型 | 约束 | 默认 | 说明 |
|-----------|------|------|------|------|
| `graattempt.id` | str | 主键，8 位 hex | `uuid4[:8]` | 留痕 ID；主键冲突须重试生成而非直接失败 |
| `graattempt.task_id` | str | NOT NULL，索引 | — | 目标任务 |
| `graattempt.agent_name` | str | NOT NULL，索引 | — | 抢单/拒单方 |
| `graattempt.outcome` | str | NOT NULL，索引 | — | 枚举 `won`/`lost`/`declined`/`missed` |
| `graattempt.reason` | str | NOT NULL | `''` | 失败原因，取值见 §1.4 |
| `graattempt.decision` | str | NOT NULL | `''` | `won`/`reject`/`error`；分离业务拒绝与系统故障（统计分母只含前两者） |
| `graattempt.latency_ms` | int | NOT NULL | 0 | 请求进入到条件更新返回的毫秒数 |
| `graattempt.bounty_at_grab` | int | NOT NULL | 0 | 抢中时悬赏，供复盘 |
| `graattempt.created_at` | datetime | NOT NULL，索引 | now | 尝试时刻 |
| `graattempt.claimed_at` | datetime | 可空 | null | 仅 `won` 填；响应时长分位基准 |

- 建议复合索引：`(task_id, created_at)` 供竞争人数统计；`(agent_name, created_at)` 供拒单过滤与失约统计。
- 留痕**只增不删**；无保留周期（Spec §4.14-5），按 `created_at` 归档策略另立。

### 1.4 枚举取值

| 枚举 | 取值 | 含义 |
|---|---|---|
| `GrabMode` | `grab` | 只挂大厅，**永不派单** |
| | `dispatch` | 只走派单，行为与改造前一致 |
| | `hybrid` | 先挂大厅，超 `grab_deadline_at` 才可派单 |
| `GrabAttempt.outcome` | `won` / `lost` / `declined` / `missed` | 抢中 / 落空 / 拒单 / 失约回收 |
| `GrabAttempt.decision` | `won` / `reject` / `error` | 正常竞争 / 业务拒绝 / 系统故障 |
| `GrabAttempt.reason` | `''` / `taken` / `not_found` / `not_grab_mode` / `task_not_ready` / `cooldown` / `daily_limit` / `offline` / `agent_busy` / `internal_error` / `db_locked` | `won` 为空串；其余见 §3.2 |

## 2. 状态机

### 2.1 抢单相关的 Task 状态转移

**只列**本次新增/改动的转移，既有转移见项目原状态机。

| 当前状态 | 事件 | 下一状态 | 副作用 |
|---------|------|---------|--------|
| `queued` + `ready` | agent 抢单成功（CAS 命中） | `claimed` + `implementing` | 建Run；`attempt += 1`；留痕 `won`；发 `task_grabbed`；`pool_entered_at` 保持（用于响应时长） |
| `queued` + `ready` | 显式拒单（未占用） | **不变** | 留痕 `declined`；不扣任务状态，仅扣 agent 额度 |
| `queued` + `ready` | 悬赏阶梯推进 | **不变** | `bounty` / `last_surge_level` 更新；达封顶则阶梯推进但不加价、不发事件 |
| `queued` + `ready` | 派单 tick 命中（仅 `dispatch` 或 `hybrid` 已过期） | `claimed` + `implementing` | 建 Run；发 `task_assigned` |
| `claimed` + `implementing` | 开工窗口超时**且未开工** | `queued` + `ready` | run → `finished`；走 `enter_pool`（重置 `pool_entered_at`）；agent 失约 +1 且进冷却；留痕 `missed`；发 `grab_missed`；**不消耗 `attempt`** |
| `claimed` + `implementing` | 主动释放 / 退单 | `queued` + `ready` | run → `finished`；走 `enter_pool`；**不记失约**（显式退单≠ 失约） |
| `claimed` + `implementing` | 心跳开工 | `running` + `implementing` | run → `running`；CAS 条件 `state != 'finished'`；与回收竞争时只有一方成功 |
| 任意终态 | 任何抢单/大厅/后台动作 | **不变** | 终态不被重新激活（不变量） |

### 2.2 Run 状态转移（抢单相关）

| 当前状态 | 事件 | 下一状态 | 副作用 |
|---------|------|---------|--------|
| 不存在 | 抢单 CAS 命中 | `claimed` | 建 run；`progress = 0`；`last_heartbeat = now` |
| `claimed` | 心跳（开工） | `running` | CAS `WHERE state != 'finished'`；败方 409 `run.race_lost` |
| `claimed` | 窗口超时回收 | `finished` | CAS `WHERE state = 'claimed'`；败方放弃且不记失约 |
| `finished` | 迟到结果提交 | `finished`（不变） | **必须仍返 200**，由 `_handle_success` 依 `_is_system_timeout_failure` 决定是否纠正（既有 `late_result_recovery` 能力，不得被状态校验打断） |
| `finished`（成功已交接） | 重复提交成功 | `finished`（不变） | **409** `run.result_already_submitted`；判据 `exit_code == 0 且 task.state == COMPLETED` |

### 2.3 非法转移处理

- 统一走既有 `_safe_transition`：捕获 `IllegalTransition` 并**返回真实状态**，不抛异常、不谎报。
-抢单侧的非法转移在**入口即拒**（`422 grab.task_not_ready` / `422 grab.not_grab_mode`），不进状态机。
- CAS 未命中一律返 `409`，**不改任何状态**。
- 违反 §3.1 不变量的写入视为 bug，不做兜底掩盖。

## 3. 完整性约束

### 3.1 跨对象不变量（必须成立）

| 约束 | 守护者 |
|---|---|
| 一个任务同时至多一个有效执行 Run（`claimed`/`running`） | 抢单 CAS、派单 CAS |
| 一个 Agent 同时至多一个有效执行 Run | 在途 run 检查 + `409 agent_busy` |
| `Task.state=claimed/running` 必有对应 run，且 `run.task_id` 指向该任务 | 状态流转与 CAS |
| `Task.state=queued` + `stage=ready` 时 `pool_entered_at` 非空 | `enter_pool` |
| 终态任务无有效执行 Run，且不被大厅/后台重新激活 | 候选查询恒含 `state=queued` |
| 悬赏不超过 `bounty_cap` | 悬赏 CAS 的 `min()` |
| 失约计数不重复累加 | 回收 CAS `WHERE state='claimed'` |
| 拒单计数不超上限，跨日不重复重置 | 额度条件更新 |

### 3.2 唯一键与外键

| 关系 | 说明 |
|---|---|
| `Run.task_id` → `Task.id` | 逻辑外键，不设数据库 FK（沿用既有风格） |
| `GrabAttempt.task_id` → `Task.id` | 逻辑外键；任务删除时留痕保留（只增不删） |
| `GrabAttempt.agent_name` → `Agent.name` | 逻辑外键；agent 注销后留痕仍可读 |
| `GrabAttempt.id` | 唯一主键；8 位 hex 撞库时**重试生成**，不静默覆盖 |

### 3.3 业务不变量

- `bounty` 是**排序权重与展示值，不是货币**，任何路径不得把它当金额累加或结算。
- 冷更是**软惩罚**：只影响大厅排序，**不禁止抢单**。接口不得在其他入口再硬拦，否则与排序规则自相矛盾。
- `grab_mode=dispatch` 的任务字段取值与事件序列**不被新增后台线程改变**（兼容性承诺，Spec §5.3）。
- 悬赏阶梯推进与加价分离：达封顶后 `last_surge_level` 继续推进但不加价、不发事件。

## 4. 迁移策略

### 4.1 加列（幂等）

- 迁移入口 `migrations.run_migrations`，启动时执行；用 `inspect(conn).get_columns(...)` 先查列再`ALTER TABLE ADD COLUMN`，**重复执行安全**。
- `Task` 新增 10 列、`Agent` 新增 5 列，全部给 NOT NULL DEFAULT 或可空，无需回填历史数据。
- `grab_mode` 的 **DB 列默认值为 `dispatch`**（保证存量行为不变），**建单 API 层缺省 `hybrid`**——两者刻意不同，不可统一。

### 4.2 建表与索引

- `SQLModel.metadata.create_all` 建 `graattempt`；`run_migrations` 补复合索引。
- 历史库升级路径：加列 → 建表 → 建索引，三步各自幂等，可中断后重跑。

### 4.3 回滚

- 回滚方式：代码回退即可，**新增列/表不做DROP**（留痕数据不因回滚丢失）。
- 回滚后行为：新增字段被ORM 忽略，`dispatch` 语义与改造前一致。
- ⚠️ 迁移**无自动降级**；若需降级须手工`ALTER TABLE DROP COLUMN`（SQLite 3.35+ 支持），执行前确认无数据依赖。

### 4.4 NULL 兜底

| 场景 | 兜底 |
|---|---|
| 旧行 `grab_mode` 为 NULL | 读路径统一 `or GrabMode.DISPATCH` |
| 旧行 `pool_entered_at` 为 NULL | 大厅排序按等待时长 0 处理；`hybrid` 触发后台补齐 + 告警 |
| 旧行 `bounty*` 为 0 | 悬赏维度记0 分，不做除法 |
| `decline_day` 为 NULL | 视为「非今日」，惰性日切重置 |
