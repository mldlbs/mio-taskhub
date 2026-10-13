> 软件项目文档链 · 7 件套  
> 上游：[需求规格](docs/taskhub/grab-mode/requirement.md) | 下游：[状态模型](docs/taskhub/grab-mode/data-model.md)  
> 约定：正文**分点 + 换行**（每点一行，`-` 列表）；指引注释处填内容，未用章节删除。

> ## 设计完成、暂缓实现（需求证据不足）
>
> **本文档已完成设计并通过全量评审，但不实现。**
>
> 证伪依据（2026-10-07 生产数据）：195 个任务中历史 `attempt > 1` 为 **0**（从未发生竞争）；
> 当前在线 Agent **0** 个、执行中 **0** 个；ready 待领 26 个（供给过剩）。
> `target_agent_type` 已覆盖 74/195，能力匹配机制已存在。
>
> 因此本文档设计的**悬赏递增 / 匹配评分 / 拒单额度 / 失约惩罚与冷却 / 竞争人数统计 /
> GrabAttempt 表 / 专用抢单并发内核 / 复杂 Recovery / 抢单经济模型**九项均**需求证据不足**，暂缓实现。
>
> **保留价值**：一旦出现多Agent 竞争（触发条件见 requirement.md §7），本设计可直接启用。
>
> **当前实际实施范围**为5 条 FR（见 [需求规格 v0.2](docs/taskhub/grab-mode/requirement.md)）：
> `grab_mode` 标记 +只读大厅 + 复用现有 `claim()` + 派单让位 + 能力匹配沿用。
> 裁剪裁定见 [需求裁剪裁定](docs/taskhub/3fa41beb/decision.md)。

# 模块 Spec（Module Specification）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v0.3 |
| 最后更新 | 2026-10-07 11:20 |
| 状态 | draft |
| 负责人 | 高志谦 |
| 适用范围 | mio-taskhub 抢单内核：`grab_mode` 分流、抢单池排序、原子抢单与留痕、悬赏爬升、开工窗口释放、拒单限额、抢单统计 |
| 明确不含 | 前端渲染与交互、真实货币结算、地理位置计算、`TaskState` / `TaskStage` 枚举语义变更、Run 级凭证（见 §4.14 已知限制） |
| **实施状态** | **设计完成、暂缓实现（需求证据不足）** —— 实际实施范围见 requirement.md v0.2 的 5 条 FR |

> **v0.3 修订说明**：本版依据 v0.2 全量评审结论 + 源码逐项核验修订。
> 关键修订：① 回收 CAS 判据从 `progress==0` 改为**开工标记 + 心跳时窗 + 截止时间**三重条件（§4.7）；
> ② 拒单额度把**UTC 日切 + 额度检查 + 计数递增**纳入同一事务（§4.8）；
> ③ 广播可靠性**明确接受限制**，不再承诺后台补发（§4.10）；
> ④ 统一模块归属（§1.3）；⑤ 排序改五级键、冷却独立成维度（§4.4）；
> ⑥ 补跨对象不变量表（§3.4）、错误分类表（§4.11）、SQLite 基线（§4.12）、统计口径（§4.13）。

## 1. 模块职责与边界

### 1.1 做什么

| 职责 | 说明 |
|---|---|
| 任务级调度模式声明与生效 | `grab_mode` 三值分流，抢单模式不被派单截胡 |
| 抢单池查询与多级排序 | 悬赏、等待时长、竞争人数、匹配分、过期倒计时 |
| 原子抢单与抢单留痕 | 条件更新抢占 + `GrabAttempt` 全量留痕 |
| 悬赏阶梯爬升 | 按等待时长阶梯加价，封顶且幂等 |
| 开工窗口释放与失约记录 | 三重判据 CAS 回收，失约计冷却 |
| 拒单与每日额度 | 原子额度检查，失败不扣额度 |
| 抢单统计与排行榜 | 响应时长分位、失约率、成功率 |

### 1.2 不做什么

- 不做前端渲染与交互（另立任务）。
- 不修改 `TaskState` / `TaskStage` 的取值与转移表。
- 不做真实货币结算，`bounty` 只是排序权重与展示值。
- 不改变 `grab_mode=dispatch` 任务的既有派单行为（见 §5.3 可验证兼容性清单）。
- 不做鉴权体系改造，沿用现有 `--auth` 开关与 `agent` 标识。
- **不引入 Run 级凭证**（见 §4.14 已知限制）。

### 1.3 模块归属（v0.3 统一，消除 v0.2 的自相矛盾）

v0.2 的 §1.1 把抢单入口写作 `api/claim.py::grab_task`，§4.10 又把 `grab` 端点列在 `api/hall.py`，两处不一致。v0.3 统一为四层，**一个操作只有一条实现路径**：

| 层 | 文件 | 职责 | 禁止 |
|----|------|------|------|
| HTTP 读层 | `api/hall.py` | `GET /tasks/hall`、`GET /grab/stats`（只读查询与聚合） | 不得写业务状态 |
| HTTP 写层 | `api/claim.py` | `POST /tasks/grab`、`POST /tasks/{id}/decline`、`POST /runs/{id}/release`；并**持有唯一的原子占用原语** | 不得重复实现领域规则 |
| 领域层 | `grab.py` | `GrabMode`、`enter_pool`、匹配分、排序键、悬赏计算、窗口回收、额度计算 | 不得直接依赖 FastAPI |
| 调度层 | `background.py` | 线程注册、周期 tick、心跳与生命周期 | 不得内嵌业务规则 |

- `api/claim.py` 现有的 `atomic_claim` 被抢单**复用**而非重写，保证两条入口互斥（同一 CAS）。
- 写层调用领域层拿决策，领域层不反调写层，避免循环依赖。

## 2. 输入 / 输出

### 2.1 输入

| 来源 | 形态 | 说明 |
|------|------|------|
| HTTP 查询参数 | `agent`（必填）、`agent_type`、`project_scope`、`limit`、`task_id` | 抢单身份与范围过滤 |
| HTTP 请求体 | `{"reason": "..."}`、`{"success": bool, "result": str}` | 拒单理由 / 结果提交 |
| DB 读取 | `Task` / `Agent` / `Run` / `GrabAttempt` | 池状态、身份能力、占用与留痕 |
| 环境变量 | `MIO_GRAB_*`（见 §4.9） | 窗口、阶梯、限额可调 |

### 2.2 输出

| 形态 | 内容 |
|------|------|
| HTTP 响应 | 大厅列表、抢单结果（`run_id` + `branch` + `required_reads`）、统计指标 |
| DB 写入 | `Task` 抢单字段、`GrabAttempt` 行、`Agent` 计数 |
| 事件流 | `task_bounty_surged` / `task_grabbed` / `task_grab_lost` / `grab_declined` / `grab_missed` / `grab_released` / `task_pool_entered` |
| 后台心跳 | 线程名 `grab-bounty` / `grab-window` 的健康状态 |

## 3. 依赖

### 3.1 上游模块

| 模块 | 复用点 | 核验结论（v0.3 实测） |
|------|--------|-------------------|
| `api/claim.py::atomic_claim` | 条件更新 `WHERE state='queued'` 的原子抢占 | 事务安全：`create_run` 只 `db.add` 不 commit（`runs.py:35`），`record_post_claim` 亦只返回待add 对象（`transitions.py:107`），全部写入由调用方单次 `db.commit()` 覆盖 |
| `background.py` | 调度 tick 骨架、`ThreadHeartbeat` 注册 | 已有 `consecutive_failures` 机制，需补「最近成功时间」（§4.10） |
| `models.py` / `db.py` / `migrations.py` | 表与列定义、会话、幂等迁移 | 迁移用 `inspect` 先查列再加列，天然幂等 |
| `events.py::emit_event` | 事件落库与广播 | `emit_event` 只 `db.add`（同事务）；广播在 `after_commit` hook 且**异常静默**（`events.py:117-135`）——可靠性限制见 §4.10 |
| `read_evidence.py::build_claim_context` | 抢单成功返回 `branch` / `required_reads` | 直接复用 |

### 3.2 外部库

- FastAPI、SQLModel / SQLAlchemy、SQLite（单机存储）。
- **无新增第三方依赖**。

### 3.3 事务边界硬约束（v0.3 新增，P0 级）

以下为**硬约束**，实现时违反即视为缺陷：

1. **禁止提前提交**：任务占用、Run 创建、留痕、任务字段更新必须在**同一次** `db.commit()` 内提交。任何函数若内部调用 `commit()`，外层异常将无法回滚占用操作。
2. **禁止读后写**：状态流转必须用条件更新（`UPDATE ... WHERE <前置状态>`）并检查 `rowcount`，不得先 `db.get()` 判状态再无条件写。
3. **禁止跨事务的配额递增**：额度检查与递增必须在同一事务（§4.8）。
4. **事件与业务同事务**：`emit_event` 只 `db.add`，随业务一起提交；广播只在 `after_commit` 发生。

### 3.4 跨对象不变量（v0.3 新增）

不修改枚举 ≠ 不需要不变量。以下组合**必须成立**，由后台线程与写层共同守护：

| Task.state | Task.stage | 有效执行 Run | 约束 |
|---|---|---|---|
| `queued` | `ready` | 无 | 可进入抢单池；`pool_entered_at` 必须非空 |
| `queued` | 非 `ready` | 无 | 不可抢单（阶段门槛） |
| `claimed` | `implementing` | 有，且`run.agent_name` 为唯一持有者 | 任务与执行者必须一致 |
| `running` | `implementing` | 有，同上 | 同上 |
| 任意终态（`completed`/`failed`/`cancelled`/`blocked_failed`） | 任意 | **无** | 不得被大厅或后台线程重新激活 |
| `queued` | `ready` | 有但已 `finished` | 允许重新入池（历史 run 保留，不覆盖） |

- **一个 Agent 同时至多一个有效执行 Run**（`claimed` 或 `running`），抢单不得突破此限制。
- **一个任务同时至多一个有效执行 Run**。
- 不变量被破坏即为 bug，对应回归测试见 §6。

## 4. 详细设计

### 4.1 Task 字段扩展

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `grab_mode` | enum `GrabMode` | **迁移列默认 `dispatch`**；建单 API 缺省 `hybrid` | `grab` 永不派单／`dispatch` 行为不变／`hybrid` 超期转派 |
| `bounty` | int | 0 | 当前悬赏（排序权重，非货币） |
| `base_bounty` | int | 0 | 初始悬赏，爬升基准 |
| `bounty_step` | int | 5 | 每阶梯涨幅 |
| `bounty_cap` | int | 100 | 悬赏封顶 |
| `surge_interval` | int | 300 | 阶梯间隔秒数 |
| `last_surge_level` | int | 0 | 已生效阶梯，幂等防重复加价 |
| `pool_entered_at` | datetime | null | 入池时刻 |
| `grab_deadline_at` | datetime | null | hybrid 可转派时刻 |
| `grab_window_seconds` | int | 600 | 抢中后开工窗口 |

> 迁移列默认值取 `dispatch`：取 `hybrid` 会让老任务在窗口期内不被派单，改变既有行为（FR-1）。

### 4.2 GrabAttempt 留痕表

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | str | 主键，8 位 hex | 尝试 ID（`uuid4[:8]`，碰撞处理见 §4.15 注） |
| `task_id` | str | 索引 | 目标任务 |
| `agent_name` | str | 索引 | 抢单方 |
| `outcome` | str | 索引，枚举 `won`/`lost`/`declined`/`missed` | 结果 |
| `reason` | str | 默认 `''` | 失败原因，取值见 §4.11 |
| `decision` | str | 默认 `''` | `won` / `reject` / `error`，分离业务拒绝与系统故障 |
| `latency_ms` | int | 默认 0 | 从请求进入到条件更新返回的毫秒数 |
| `bounty_at_grab` | int | 默认 0 | 抢中时悬赏，供复盘 |
| `created_at` | datetime | 索引 | 尝试时刻 |
| `claimed_at` | datetime | 可空 | 仅 `won` 填写；响应时长分位基准 |

- 建议复合索引：`(task_id, created_at)` 支撑竞争人数统计；`(agent_name, created_at)` 支撑拒单过滤与失约统计。
- 留痕表**只增不删**，须定保留周期（见 §4.14 注）。

### 4.3 调度模式、入池与派单让位

**入池是显式操作，不是后台补救**（v0.3 修正：v0.2 依赖悬赏引擎补齐 `grab_deadline_at`，而悬赏引擎又要求 `pool_entered_at` 非空，两者互锁）。

`enter_pool(task, now, reason)` 是唯一入池入口，负责：
- 写 `pool_entered_at = now`；
- `hybrid` 且 `grab_deadline_at` 为空时，按 `hybrid_dispatch_after` 补齐 deadline；
- 重置 `last_surge_level = 0`，重新计时；
- 发 `task_pool_entered` 事件。

必须走 `enter_pool` 的路径：首次转 `queued + ready`、失约回池、主动退单回池、依赖释放放行、重试退避重入。

派单让位谓词（追加到 `_assign_to_idle_agents` 原有候选条件）：

```python
dispatchable = or_(
    Task.grab_mode == GrabMode.DISPATCH,
    and_(
        Task.grab_mode == GrabMode.HYBRID,
        Task.grab_deadline_at.is_not(None),
        Task.grab_deadline_at <= now,
    ),
)
```

- `grab`：两分支均不满足 → 永不被指派（FR-2）。
- `dispatch`：首位分支恒真 → 行为与改造前一致。
- `hybrid`：仅 deadline 已过期才可指派（FR-3）。
- **兜底自检**：后台 tick 检测到 `hybrid` 且 `pool_entered_at IS NULL` 时调用 `enter_pool` 补齐并告警——是兜底，不是主路径。

### 4.4 匹配分与大厅排序（v0.3 修正）

**v0.2 的错误**：用 `match_score = -1` 表示冷却以排最后，但失约惩罚可低至 −20 < −1，冷却任务不一定排最后。v0.3 把冷却**独立成排序维度**。

排序键（五级，末级 FIFO）：

```
cooldown_active ASC, match_score DESC, bounty DESC, priority DESC, pool_entered_at ASC
```

- `cooldown_active`：`agent.grab_cooldown_until > now` 为 1（排后），否则 0。**冷却是软惩罚**——只影响排序，不禁止抢单（见 §4.7）。
- `match_score` 范围 `[0, 100]`：失约惩罚先截断到 0 再参与计分，不产生负分。

匹配分构成（各维度**互斥**，命中即止）：

| 维度 | 计分 | 依据 |
|---|---|---|
| 目标类型命中 | +50 | `task.target_agent_type == agent.agent_type` |
| 目标类型为空 | +30 | 开放任务，人人可抢（与上两态互斥） |
| 目标类型不匹配但fallback 已到期 | +10 |复用 `claim.should_fallback` |
| `project_scope` 命中 | +15 | 范围过滤命中，多值 OR |
| `agent_type` 命中任务 `labels` | +10 | 大小写不敏感精确匹配 |
| 悬赏 | `min(bounty, cap)/cap * 20` | 加价提升曝光 |
| 等待时长 | `min(waited/3600, 1) * 10` | 越等越靠前（FR-5） |
| 失约惩罚 | `−5 × min(miss_7d, 4)`，下限截断至 0 | 惩罚不使总分转负 |

边界约定：
- `bounty_cap <= 0` → 悬赏维度记 0 分，不做除法（防除零）。
- `pool_entered_at IS NULL` → 视为等待时长 0，排在同分任务末尾（以 `created_at` 兜底）。
- 评分**查询时计算**（不物化），但失约次数**批量聚合**一次，避免 N+1 查询。

### 4.5 原子抢单

沿用 `atomic_claim` 的条件更新，流程：

1. 校验 `agent` 已注册且非 `offline`，否则留痕 `grab.agent_offline` 返 `403`。
2. 校验任务存在 → 否则 `404` 留痕 `not_found`。
3. 校验 `state=queued` 且 `stage=ready` → 否则 `422` 留痕 `task_not_ready`。
4. 校验 `grab_mode != dispatch` → 否则 `422` 留痕 `not_grab_mode`（提示改用 `claim`）。
5. **Agent 在途 run 检查**（v0.3 修正）：若该 agent 已有在途 run：
   - 该 run 属于**同一任务** → 幂等返回该 run，`200`（不重复落 `won` 留痕、不重复发事件）。
   - 该 run 属于**其他任务** → 返 `409 grab.agent_busy`，**不返回不相关的 run**。
   - 理由：`find_existing_run`（`runs.py:16-20`）只按 `agent_name` 查、不按 task 过滤，直接复用其返回值会把别的任务的 run 塞给调用方。
6. 执行条件更新 `WHERE id=:task_id AND state='queued'`。
7. `rowcount == 1` → 落 `won` 留痕、创建 run、发 `task_grabbed`，`200`。
8. `rowcount != 1` → 落 `lost` 留痕（`reason=taken`），查当前 run 得 `winner`，`409`。

- 步骤 5–8 的全部写入 + Run 创建 + `won` 留痕在**同一次 commit**（§3.3 硬约束 1）。
- `grab` 强制要求 `task_id`（抢单是对具体订单下手，不支持盲领）；`claim` 保持可盲领。两者共用 `atomic_claim` 的 CAS，因此**互相天然互斥**。

### 4.6 悬赏爬升引擎

后台线程 `grab-bounty`，周期 30s：

1. 取 `state=queued AND stage=ready AND grab_mode != dispatch AND pool_entered_at IS NOT NULL`。
2. 阶梯判断与更新**在同一事务内以条件更新完成**（v0.3 修正，v0.2 的「先读 `last_surge_level` 再写」在并发下会重复加价）：

```sql
UPDATE task SET bounty = :new_bounty, last_surge_level = :level
WHERE id = :id
  AND state = 'queued' AND stage = 'ready'
  AND last_surge_level = :expected_old_level   -- CAS：阶梯未被他人推进
```

3. `rowcount != 1` → 跳过（他人已推进或任务已离池），**不发事件**。
4. `rowcount == 1` 且 `new_bounty != old_bounty` → 发 `task_bounty_surged`。
5. 达封顶后阶梯继续推进但不加价、不发事件（避免事件风暴）。

- 任务离池后 bounty 线程不再修改其 `bounty`（候选条件已含 `state=queued`）。
- 配置变更对已入池任务**立即生效**（不快照配置），故 `last_surge_level` 是唯一幂等依据。

### 4.7 开工窗口释放（v0.3 重点修正）

**v0.2 的错误**：判据是 `progress == 0`。但 `Run.progress` 默认值为 `0`，agent 可以**一直不传 progress** 而正常开工——现有 reaper 的 `never_started` 判据（`background.py:246`）就有这个误杀问题，抢单不能复制它。

**v0.3 判据（三重条件全部满足才回收）**：

1. `run.state == RunState.CLAIMED`（尚未被心跳推进到 running）；
2. **窗口内无任何心跳**：`run.last_heartbeat` 早于 `started_at + grab_window_seconds`；
3. **任务无开工标记**：`run.checkpoint` 为空**且**未收到过 `progress > 0` 的心跳。

> 心跳缺失只是超时证据之一，**不能单独证明任务尚未开始**——这是 v0.2 的核心漏洞。

**回收必须用条件更新（CAS）**，与心跳开工互斥：

```sql
UPDATE run SET state='finished', result='never_started_grab_window',
              finished_at=:now, exit_code=1
WHERE id=:run_id AND state='claimed'          -- 关键：CAS on state
```

- `rowcount != 1` → 心跳或正常完成已抢先，**放弃回收**（不记失约）。
- 唯一开工入口 = `heartbeat` 端点，它同样用 `WHERE state != 'finished'` 做 CAS（已在存量修复任务 `d1b54de0` 落地）。两侧都以 `state` 为 CAS 依据，**只有一方能成功**。
- 回收成功后：任务回 `queued/ready` 并走 `enter_pool`；`agent.grab_miss_count += 1`；`agent.grab_cooldown_until = now + cooldown_seconds`；落 `missed` 留痕；发 `grab_missed`。
- **不消耗 `attempt`**：未开工不是任务失败（与既有 `never_started` 语义一致）。
- **失约计数只在此处递增**，且由 CAS 保证同一 run 只被计一次（重复扫描时 `rowcount != 1`）。

### 4.8 拒单与每日额度（v0.3 修正）

**v0.2 的错误**：`decline_count_today` 读-改-写，且跨 UTC 日切与拒单操作不在同一事务——并发下可能超额（19+19 两笔都通过检查 → 实际 21 次），跨日重置也可能与拒单竞争。

**v0.3 原子额度方案**：把**日切 + 检查 + 递增**放进同一事务，并用条件更新串行化：

```sql
-- 步骤 1：惰性日切（仅当日期不同才重置，CAS 保证只重置一次）
UPDATE agent SET decline_count_today = 0, decline_day = :today
WHERE name = :agent AND decline_day != :today

-- 步骤 2：原子检查 + 递增（未达上限才成功）
UPDATE agent SET decline_count_today = decline_count_today + 1
WHERE name = :agent
  AND decline_day = :today
  AND decline_count_today < :limit      -- 上限检查即写入条件
```

- 步骤 2 `rowcount == 1` → 额度扣减成功，业务动作生效。
- 步骤 2 `rowcount == 0` → **额度已满**，返 `429 grab.decline_limit` + `Retry-After`（到次日 UTC 00:00 的秒数），**不写留痕、不动任务**。
- 两步在**同一事务**内完成，期间不接受任何对同一 agent 的并发额度写。
- **失败不扣额度**：因任务已被抢走（`409`）导致的拒单失败，步骤 2 不执行。
- **只有成功的拒单/退单才扣额度**。

退单语义（任务已被本人占用时）：run 置 `FINISHED(result='declined')`，任务回池走 `enter_pool`，**不记失约**（显式退单≠失约）。退单同样受额度限制（紧急退单可用 `release` 端点，不受额度约束）。

**当日跳过集**：复用 `GrabAttempt` 查询（`agent_name` + `task_id` + `outcome='declined'` + `created_at` 落在 UTC 当日），不引入内存缓存或独立表。若性能不足再评估物化表，但**事实来源必须是持久化数据**。

### 4.9 配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| `MIO_GRAB_DEFAULT_WINDOW_SECONDS` | 600 | 抢中后开工窗口 |
| `MIO_GRAB_SURGE_INTERVAL` | 300 | 悬赏阶梯间隔秒 |
| `MIO_GRAB_BOUNTY_STEP` | 5 | 每阶梯涨幅 |
| `MIO_GRAB_BOUNTY_CAP` | 100 | 悬赏封顶 |
| `MIO_GRAB_DECLINE_DAILY_LIMIT` | 20 | 每 agent 每日拒单上限 |
| `MIO_GRAB_COOLDOWN_SECONDS` | 120 | 失约后抢单冷却（软惩罚，只影响排序） |
| `MIO_GRAB_HYBRID_DISPATCH_AFTER` | 900 | hybrid 转派延时（入池时补齐 deadline） |
| `MIO_HALL_DEFAULT_LIMIT` | 50 | 大厅默认返回条数 |
| `MIO_GRAB_HALL_COMPETITOR_WINDOW` | 300 | 竞争人数统计窗口秒 |

### 4.10 事件与后台线程的可靠性边界（v0.3 明确接受限制）

**现状事实**（源码核验）：`emit_event` 只 `db.add`（`events.py:61`），与业务同事务；广播在 `after_commit` hook 且**异常静默**（`events.py:127-135` 逐事件 try/except）。

**v0.3 明确接受的限制**：

1. **广播失败仅记日志，不做后台补发**。v0.2 曾写「未广播事件可由后台补发」——该能力**不存在**，本版删除该承诺。
2. **不引入持久化 outbox**。引入 outbox 需新增表、补发 worker 与幂等消费方，属独立工程决策，不在本任务范围。
3. **一致性保证是「状态与事件同事务」，不是「事件必达」**。客户端若依赖 WS 实时性，必须自行周期轮询兜底（现有前端已有轮询）。
4. 广播事件的消费者必须能容忍**重复**与**丢失**——不得把 WS 事件当作唯一事实来源。

**线程健康指标**（在既有 `ThreadHeartbeat` 上扩充）：除存活与 `consecutive_failures` 外，补充「最近一次 tick 成功时间」「最近一次成功回收/加价时间」「单 tick 处理数与失败数」，避免「线程存活但长期无实际进展」被误判为健康。

### 4.11 错误分类表（v0.3 新增）

**业务拒绝**（正常竞争/规则限制，`decision='reject'`）与**系统故障**（异常，`decision='error'`）必须分离——否则统计会把系统故障当成正常竞争（v0.2 的 `taken` 语义过宽）。

| HTTP | 业务码 | 分类 | 触发条件 | 留痕 reason | 建议处理 |
|---|---|---|---|---|---|
| 400 | `request.invalid_param` | reject | 参数缺失/越界 | — | 修正参数 |
| 403 | `run.not_owner` | reject | 非 run 持有者 | — | 用正确 agent |
| 403 | `grab.agent_offline` | reject | agent 未注册/离线 | `offline` | 先注册并心跳 |
| 404 | `grab.task_not_found` | reject | 任务不存在 | `not_found` | 核对 ID |
| 409 | `grab.task_taken` | reject | CAS 未命中，他人已抢 | `taken` | 改抢别的；**禁止重试** |
| 409 | `grab.agent_busy` | reject | 该 agent 已有其他在途 run | `agent_busy` | 先完成在手任务 |
| 422 | `grab.not_grab_mode` | reject | 目标为 dispatch 模式 | `not_grab_mode` | 改用 `claim` |
| 422 | `grab.task_not_ready` | reject | 非 queued/ready | `task_not_ready` | 等阶段推进 |
| 429 | `grab.cooldown` | reject | 冷却中（软惩罚，仅排序） | `cooldown` | 按 `Retry-After` 等待 |
| 429 | `grab.decline_limit` | reject | 当日额度用尽 | `daily_limit` | 等次日 UTC 重置 |
| 429 | `rate_limited` | reject | 全局限流 | — | 退避重试 |
| 500 | `internal.error` | **error** | DB 异常、服务端错误 | `internal_error` | 可重试一次；持续失败须告警 |
| 503 | `db_locked` | **error** | SQLite 锁冲突重试耗尽 | `db_locked` | 退避重试 |

- `decision` 字段区分 `won` / `reject` / `error`，统计时系统故障不计入竞争分母。
- **HTTP 200 + `won`** 与 **409 + `taken`** 都是正常竞争，不视为错误。

### 4.12 SQLite 事实基线（v0.3 新增）

评审无法从文档得知的既有事实（`db.py:22-37`），实现时必须在此约束下工作：

| 项 | 实际配置 | 含义 |
|---|---|---|
| 模式 | `journal_mode=WAL` | 读写不互斥，但**写仍串行** |
| 锁等待 | `busy_timeout=5000`（5s） | 锁冲突最多等 5s，超时抛 `database is locked` |
| 连接池 | `QueuePool`，`pool_size=5`，`max_overflow=10` | 最多 15 条连接；后台两线程 + 请求线程会争抢 |
| 线程复用 | `check_same_thread=False` | 同一连接可跨线程使用，需注意事务边界 |
| 同步级别 | `synchronous=NORMAL` | 掉电可能丢最近事务（WAL 下不损坏库） |

**必须补充的策略**：

1. 锁冲突**有限重试**：`db_locked` 属可重试错误，重试 3 次、指数退避（起始 50ms，上限 1s），耗尽后按 `503 db_locked` 处理并计入 `decision='error'`。
2. 单次 tick 内**批量处理**（如悬赏一次 UPDATE 多行），避免长事务持锁。
3. 后台 tick 遇锁冲突**不阻塞后续 tick**（捕获异常、记日志、下一周期重试）。
4. 条件更新在锁冲突时可能抛出而非 `rowcount=0`，调用方须区分「CAS 未命中」（业务竞争）与「锁异常」（系统故障）。

### 4.13 统计口径（v0.3 新增）

v0.2 给了公式但未定义口径。v0.3 明确：

| 指标 | 口径 |
|---|---|
| 统计窗口 | `created_at` 落在 `[now - window_hours, now]`，**UTC 闭区间** |
| 分母来源 | `GrabAttempt` 中 `decision IN ('won','reject')` 的行（**系统故障不计入分母**） |
| `attempts_total` | 窗口内全部留痕行数（含 `declined`/`missed`，不含 `decision='error'`） |
| `wins` | `outcome='won'` 且 `decision='won'` |
| `success_rate` | `wins / (wins + losses)`，分母 0 → `0` |
| `miss_rate` | `missed / wins`，分母 0 → `0` |
| 响应时长 | `(claimed_at - pool_entered_at)` 秒 → 毫秒；**只统计 `won`** |
| 分位点 | P50 / P95，**最近邻法**（nearest-rank）：`P = sorted[ceil(q/100 × N) - 1]` |
| 去重规则 | 同一 `(task_id, agent)` 24h 内的多次 `won` **各自计入**（重试是真实行为）；`lost` 不进分子 |
| 排行榜排序 | `wins DESC, win_rate DESC, avg_latency_ms ASC`（并列按 `agent_name` 字典序，保证稳定） |
| 空样本 | 计数 `0`、比率 `0`、分位 `null`（**不得显示为 0ms**） |

### 4.14 已知限制（明确不做，留后续任务）

1. **未传 `agent` 时仍可凭 `run_id` 越权**。存量修复（任务 `d1b54de0`）把 `agent` 设为可选以不破坏兼容。彻底关闭需引入 **Run 级凭证**（claim 时下发 token，心跳/提交须带 token），涉及协议变更，另立任务。
2. **广播不保证送达**（§4.10），无 outbox 补发。
3. **不支持多实例部署**。当前无实例租约，若未来水平扩展，多个 hub 的后台线程会重复处理同一任务——需引入租约表或 `MIO_INSTANCE_ID` 抢占。本版本通过「所有后台操作走 CAS」把风险压到可接受，但**不等于多实例安全**。
4. **无距离/地理排序**，`bounty` 非货币。
5. **`GrabAttempt` 无保留周期**：只增不删，长期运行会持续增长并影响统计扫描。按 `created_at` 定期归档或清理，需另立策略（不得在抢单实现里顺手删数据）。

### 4.15 预期新增 / 修改文件

| 文件 | 动作 | 内容 |
|---|---|---|
| `mio_taskhub/grab.py` | 新增 | `GrabMode`、`enter_pool`、`match_score`、`sort_key`、`surge_bounty`、`reap_no_show`、`competitors_count`、`decline_quota` |
| `mio_taskhub/api/hall.py` | 新增 | `GET /tasks/hall`、`GET /grab/stats`（只读） |
| `mio_taskhub/api/claim.py` | 修改 | 新增 `POST /tasks/grab`、`POST /tasks/{id}/decline`、`POST /runs/{id}/release`；抽出原子占用原语供三者共用 |
| `mio_taskhub/models.py` | 修改 | `Task` 加 10 字段、`Agent` 加 4 计数、新增 `GrabAttempt` |
| `mio_taskhub/migrations.py` | 修改 | `_migrate_task` 扩展、新增 `_migrate_agent`；`graattempt` 建表与复合索引 |
| `mio_taskhub/background.py` | 修改 | 派单让位谓词；注册 `grab-bounty` / `grab-window`；线程健康指标扩充；入池路径改走 `enter_pool` |
| `mio_taskhub/api/tasks.py` | 修改 | create / patch / detail 透传抢单字段 |
| `mio_taskhub/main.py` | 修改 | 注册 `hall.router` |
| `mio_taskhub/mcp_server.py` | 修改 | 新增 `taskhub_grab` / `taskhub_hall` 工具 |
| `tests/test_grab_mode.py` | 新增 | 并发抢单、派单让位、悬赏幂等、窗口回收、额度原子性、统计口径 |

> 注：`GrabAttempt.id` 用 `uuid4[:8]`（32bit），与既有 `Task` / `Run` 同风格。撞库概率随留痕量增长，故§4.14-5 要求定保留周期；主键冲突时须重试生成而非直接失败。

## 5. 异常与边界情况

### 5.1 并发与竞态

| 场景 | 处理方式 |
|---|---|
| N 个 agent 并发抢同一任务 | 条件更新 `rowcount==1` 恰一人成功，其余 `409 taken` + `winner` |
| `grab` 与 `claim` 争抢同一任务 | 共用同一 CAS，天然互斥，恰一方成功 |
| 心跳开工与窗口回收竞争 | 双方均以 `run.state` 为 CAS 依据，只有一方 `rowcount==1`；败方放弃且不记失约 |
| 两个回收线程处理同一 run | 回收 CAS `WHERE state='claimed'`，第二次 `rowcount=0`，不重复计失约 |
| 同一 agent 并发抢不同任务 | 在途 run 检查 + `409 agent_busy`；两者都通过检查时由 `agent_busy` 分支与最终 CAS 兜底 |
| 悬赏阶梯并发推进 | `WHERE last_surge_level = :expected` CAS，只有一方推进并发事件 |
| 拒单额度并发 | `WHERE decline_count_today < limit` 条件更新，只有一方 `rowcount==1` |
| UTC 跨日与拒单竞争 | 惰性日切与检查同事务，`WHERE decline_day != today` 只重置一次 |
| 正常完成与窗口回收竞争 | 回收 CAS 含 `state='claimed'`，完成侧已置 `finished` → 回收 `rowcount=0` |
| SQLite 锁冲突 | 有限重试 3 次（§4.12），耗尽返 `503 db_locked`，记 `decision='error'` |

### 5.2 业务边界

| 场景 | 处理方式 |
|---|---|
| 抢单时 agent 已有其他任务的在途 run | `409 grab.agent_busy`，**不返回不相关 run**（v0.3 修正） |
| 抢单时任务是 `dispatch` 模式 | `422 not_grab_mode`，提示改用 `claim` |
| `hybrid` 任务 `grab_deadline_at` 为空 | `enter_pool` 主路径补齐；后台兜底检测并告警 |
| 悬赏已达 `bounty_cap` | `last_surge_level` 继续推进但不加价、不发事件 |
| 失约 agent 反复抢同一任务 | 冷却期内 `cooldown_active=1` 排最后，但**仍可抢**（软惩罚，避免池子僵死） |
| 统计窗口内无任何样本 | 计数/比率 `0`，分位 `null` |
| 拒单时任务已被别人抢走 | `409`，**不扣额度**、不写留痕 |
| 旧任务 `grab_mode` 为 NULL | 读路径统一 `or GrabMode.DISPATCH` 兜底；迁移已填默认值 |
| 冷却中的 agent 抢单成功 | **允许**（软惩罚）；接口不得在其他入口又硬拦，否则与排序规则自相矛盾 |

### 5.3 兼容性承诺（可验证，非「逐字节等价」）

v0.2 的「逐字节等价」表述过强（新增字段会改变查询计划与时序）。v0.3 改为**可验证的兼容性清单**：

1. 存量任务迁移后 `grab_mode = dispatch`，按原语义运行。
2. `dispatch` 任务的候选集、派单优先级、Agent 选择与超时回收语义不变。
3. 新增后台线程**不改变** `dispatch` 任务的字段取值与事件序列。
4. 存量 API 的请求/响应结构**只增不改**（新增字段为可选）。
5. 回归验证：对同一数据集，改造前后 `dispatch` 任务的派单结果集一致；差异须逐条解释。

## 6. 验收测试要点

| 编号 | 覆盖 | 断言 |
|---|---|---|
| TC-1 | FR-6 | 20 并发抢同一任务，恰 1 成功，其余 409 且带 `winner` |
| TC-2 | FR-6 | `grab` 与 `claim` 并发争抢，恰一方占用成功 |
| TC-3 | §3.3 | Run 创建失败时 Task 占用**回滚**（不留占用无 Run） |
| TC-4 | §4.5 | 重复 `won` 请求不重复建 Run、不重复发 `task_grabbed` |
| TC-5 | §4.5 | 已有其他任务在途 run 时返 409 `agent_busy`，不返回不相关 run |
| TC-6 | §3.4 | 跨对象不变量表逐行验证（含终态不被重激活） |
| TC-7 | FR-9/§4.7 | 窗口超时但**已开工（有 checkpoint 或 progress>0）** → **不得回收** |
| TC-8 | §4.7 | 心跳与回收并发，只有一方成功；败方不记失约 |
| TC-9 | §4.7 | 两个回收者处理同一 run，失约只计一次 |
| TC-10 | §4.7 | 正常完成后回收不得重入池 |
| TC-11 | FR-2 | `grab` 任务跑一轮调度 tick 仍为 `queued` |
| TC-12 | FR-3 | `hybrid` deadline 前不派单、过期后可派单 |
| TC-13 | §4.3 | `hybrid` 缺 deadline 时被 `enter_pool` 可靠补齐 |
| TC-14 | FR-1 | 存量任务迁移后为 `dispatch`，行为与改造前一致（§5.3 清单） |
| TC-15 | §4.6 | 同一阶梯并发处理只产生一次有效更新与一次事件 |
| TC-16 | FR-8 | 封顶后不加价、不重复发事件；重启后不重复处理已完成阶梯 |
| TC-17 | FR-11 | 每日上限并发不超额（19 并发 → 恰 1 次成功） |
| TC-18 | §4.8 | UTC 跨日额度正确重置，且与拒单并发不竞争 |
| TC-19 | §4.8 | 任务已被抢走时拒单不生效且不扣额度 |
| TC-20 | §4.4 | 冷却 agent 排序在后但仍可抢单成功（软惩罚一致） |
| TC-21 | §4.11 | DB 异常记 `decision='error'` 且不进竞争分母 |
| TC-22 | §4.13 | 统计口径：分位最近邻法、去重、空样本返回 `null` |
| TC-23 | §3.3 | 业务回滚时不产生已提交的成功事件 |
| TC-24 | §4.12 | 迁移幂等；锁冲突有限重试 |

- **并发测试与单元测试分开统计**：单测通过**不能替代**真实 SQLite 并发验证（锁行为、事务边界、多线程交互）。
