> 软件项目文档链 · 7 件套  
> 上游：[需求规格](docs/taskhub/grab-mode/requirement.md) | 下游：[模块 Spec](docs/taskhub/grab-mode/spec.md)  
> 约定：正文**分点 + 换行**（每点一行，`-` 列表）；指引注释处填内容，未用章节删除。

# 架构设计（Architecture Design）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v0.1 |
| 最后更新 | 2026-10-07 11:55 |
| 状态 | draft |
| 负责人 | 高志谦 |
| 适用范围 | 抢单模式在现有 mio-taskhub 单体内的分层、依赖方向与关键技术决策 |
| 明确不含 | 微服务拆分、消息中间件、多实例部署方案（明确不做，见 §6） |

## 1. 系统上下文

- **谁调用本系统**：各 agent 运行时（claim/grab/心跳/提交）、Web UI（看板与后续大厅）、运维脚本。
- **本系统调用谁**：无外部服务依赖。存储用本地 SQLite（MIO_TASKHUB_DB），事件广播走进程内 WebSocket。
- **数据从哪来、到哪去**：任务由人或上游 agent 创建 → 落 `task` 表 → 抢单/派单两条通道分配给 agent → 结果与事件回写同一库。**无跨进程/跨机数据流**。
- **关键外部依赖**：仅 Python 运行时与 SQLite；WAL 模式下读写不互斥但写串行。

## 2. 总体架构

### 2.1 分层与依赖方向

```
HTTP 读层  api/hall.py        GET /tasks/hall · GET /grab/stats      （只读，不写业务状态）
    ↓
HTTP 写层  api/claim.py       POST /tasks/grab · /tasks/{id}/decline · /runs/{id}/release
    ↓                          持唯一原子占用原语（CAS）
领域层      grab.py            GrabMode · enter_pool · 匹配分/排序 · 悬赏 · 窗口回收 · 额度
    ↑
调度层      background.py      线程注册 · 周期 tick · 心跳与生命周期
```

- **依赖单向向下**：调度层 → 领域层 → 存储；领域层**不依赖 FastAPI**，便于单测。
- **写层唯一**：一个业务操作只有一条实现路径（这是 v0.2 的缺陷之一：v0.2 把 `grab` 同时写在 §1.1 的 `claim.py` 与 §4.10 的 `hall.py`，已修正）。
- **读层不写**：大厅与统计只查不改，避免「查询顺带改状态」这类隐性副作用。

### 2.2 与既有模块的关系

| 既有模块 | 复用 /改动 |
|---|---|
| `api/claim.py::atomic_claim` | **复用**（不重写）。抢单与盲领共用同一 CAS，因此两条入口天然互斥 |
| `background.py::_assign_to_idle_agents` | **改一处**：候选查询加 `dispatchable` 谓词（见 §3ADR-1） |
| `background.py::_on_timeout` | **不改**：抢单窗口回收走独立线程 `grab-window`，两者以 `run.state` 为 CAS 互斥 |
| `events.py::emit_event` | 直接复用（同事务落库 + 提交后广播） |
| `read_evidence.py::build_claim_context` | 直接复用（抢单成功返回 branch / required_reads） |

### 2.3 新增后台线程

| 线程 | 周期 | 职责 | 与既有线程的关系 |
|---|---|---|---|
| `grab-bounty` | 30s | 悬赏阶梯爬升 | 独立，只动`state=queued` 的任务 |
| `grab-window` | 30s | 开工窗口回收 | 与 `grab-bounty` 争抢同批任务但**字段不相交**（前者只写 bounty，后者只在回收成功时改 task） |

- 两个线程与既有 `_requeue_retries` / `_release_dependencies` / `_assign_to_idle_agents` / `_mark_stale_agents` 同频（30s），**不新增高频轮询**。
- 线程失败**不阻塞后续 tick**；异常记日志并计入健康指标。

## 3. 关键技术决策（ADR 摘要）

| 决策 | 备选 | 结论与理由 |
|------|------|-----------|
| ADR-1 `dispatch` 任务改由 `dispatchable` 谓词显式放行 | ① 在 `_assign_to_idle_agents` 里写 if 判断<br>② 用新枚举值 `manual` 区分 | **谓词**。理由：谓词可被未来其他派单入口复用；if 判断会在新增入口时漏掉。代价是需回归验证 `dispatch` 行为不变（TC-14） |
| ADR-2 抢单与盲领共用 `atomic_claim` 的 CAS | ① 抢单另写一套<br>② 加 `FOR UPDATE` | **复用 CAS**。理由：SQLite 无 `FOR UPDATE`；同一 CAS 复用天然保证「grab 与 claim 互斥」，另写一套则要额外论证两者互斥 |
| ADR-3 窗口回收用**条件更新**而非读后写 | ① 先读 `state`/`progress` 再写<br>② 加悲观锁 | **条件更新**。理由：读后写存在 TOCTOU——回收线程读到 `progress=0` 后 agent 可能刚好开工，导致同任务双执行。SQLite 写锁本身串行化写，但**读不锁**，故必须在写语句的 WHERE 里带上前置状态 |
| ADR-4 开工判据用「心跳时窗 + 开工标记」而非 `progress == 0` | ① `progress == 0`<br>② 只看 `last_heartbeat` | **三重条件**。理由：`progress` 默认 0，agent 可全程不传而正常开工（现有 reaper 的 `never_started` 判据就有此误杀问题）；心跳缺失只能证明「没在报活」，不能证明「没开工」 |
| ADR-5 入池是**显式操作**（`enter_pool`），非后台补救 | ① 后台 tick 检测到缺失再补<br>② 完全不补，缺失即不派单 | **显式入口 + 兜底自检**。理由：v0.2 让悬赏引擎补 `grab_deadline_at`、而悬赏引擎又要求 `pool_entered_at` 非空，两者互锁会造成 hybrid 永久滞留 |
| ADR-6 额度用「日切 + 检查 + 递增」同事务条件更新 | ① 读-改-写加乐观锁版本号<br>② 进程内锁 | **条件更新**。理由：单条 `UPDATE ... WHERE count < limit` 天然原子，且跨进程安全；进程内锁在多实例下失效（虽本版本不支持多实例） |
| ADR-7 冷却做成**独立排序维度**，不用特殊分数 | ① `match_score = -1`<br>② 硬门控直接拒绝 | **独立维度**。理由：失约惩罚可低至 −20 < −1，用 −1 排最后不成立；硬门控会让池子僵死（失约 agent 再也没法接单） |
| ADR-8 **不引入**持久化 outbox | ① 新增 outbox 表 + 补发 worker<br>② 承诺「广播可补发」 | **明确不做**。理由：outbox 需新增表、补偿 worker、幂等消费方，属独立工程决策；v0.2 曾写「可后台补发」但该能力**并不存在**，属虚假承诺。v0.3 改为明确接受「状态与事件同事务」而非「事件必达」 |
| ADR-9 `agent` 参数在 run端点设为**可选** | ① 改必填<br>② 上Run 级凭证 | **可选**。理由：改必填会打断存量调用方（`packaging/*.py`、旧版客户端）。**残余风险已知**：不传时仍可凭 `run_id` 越权，彻底关闭需凭证方案（Spec §4.14-1） |
| ADR-10 新增字段迁移默认 `dispatch`，建单 API 缺省 `hybrid` | ① 统一用 `hybrid`<br>② 统一用 `dispatch` | **刻意不同**。理由：统一 `hybrid` 会让老任务在窗口期内不再被派单，**改变既有行为**；统一 `dispatch` 则新任务永远进不了大厅 |

## 4. 关键数据流

### 4.1 抢单主链路

```
agent ──POST /tasks/grab?agent=A&task_id=T
  └─ api/claim.py
       ├─ ① 校验 agent 在线 / 任务存在 / queued+ready / grab_mode≠dispatch
       ├─ ② 在途 run 检查：同任务→幂等返回；异任务→409 agent_busy
       ├─ ③ CAS: UPDATE task SET state='claimed' WHERE id=T AND state='queued'
       │     rowcount==1 ↓                     rowcount≠1 →409 taken + winner
       ├─ ④ 建 Run、写留痕won、发 task_grabbed
       └─ ⑤ 单次 commit（**禁止中途 commit**）→ 提交后广播
```

**事务边界**：①–⑤ 全在**同一次 `db.commit()`**。任何函数内部提前 commit 都会让外层异常无法回滚占用（Spec §3.3 硬约束）。

### 4.2 窗口回收链路（与心跳竞争）

```
grab-window tick                heartbeat（agent 开工）
  │                                │
  ├─ 读 run（快照）                ├─ CAS: UPDATE run SET state='running'
  │                                │      WHERE id=R AND state≠'finished'
  ├─ CAS: UPDATE run SET state='finished'  WHERE id=R AND state='claimed'
  │                                │      rowcount==1 → 200
  └─ rowcount==1 才继续：          └─ rowcount==0 → 409 race_lost
     ├─ task 回 queued/ready + enter_pool
     ├─ agent 失约 +1、进冷却
     ├─ 留痕 missed + 发 grab_missed
     └─ 不消耗 attempt
```

**互斥点**：两侧都以 `run.state` 为 CAS 依据，故**恰一方 `rowcount==1`**，不会出现「同任务双执行」。

### 4.3 悬赏爬升链路

```
grab-bounty tick
  ├─ 候选：state=queued AND stage=ready AND grab_mode≠dispatch AND pool_entered_at NOT NULL
  ├─ level = int(waited / surge_interval)
  ├─ CAS: UPDATE task SET bounty=:new, last_surge_level=:level
  │        WHERE id=:id AND last_surge_level=:expected   ← 阶梯未被他人推进
  ├─ rowcount==1 且 bounty 变化 → 发 task_bounty_surged
  └─ 达bounty_cap → 阶梯推进但不加价、不发事件（防事件风暴）
```

## 5. 技术选型

- **语言 / 框架**：Python 3.12 + FastAPI（沿用既有）；SQLModel / SQLAlchemy 2.x。
- **存储**：SQLite（WAL）。单机、无外部 DB 依赖。
- **并发控制**：**条件更新（CAS）+ `rowcount`** 为主，不引入行锁；锁冲突靠 `busy_timeout=5000` + 有限重试。
- **第三方依赖**：**无新增**。事件、状态机、读证门控全部复用既有模块。
- **打包**：PyInstaller（沿用 `mio-taskhub.spec`）。

## 6. 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 派单让位改动影响既有 52 项任务 | 高 | 谓词改动 + TC-14 兼容性清单逐条回归；`dispatch` 行为不可变是硬约束 |
| 后台线程与既有线程争锁 | 中 | 单tick 批量处理缩短持锁；锁冲突有限重试；失败不阻塞下一 tick |
| 悬赏/回收 CAS 未生效导致重复处理 | 中 | 全部以状态/阶梯为 CAS 条件；重复扫描第二次必然 `rowcount=0`（TC-9、TC-15、TC-16锁这条） |
| 留痕表无保留期，长期增长影响统计 | 中 | 已声明为已知限制（Spec §4.14-5）；按 `created_at` 归档另立策略，**不在本实现里顺手删数据** |
| 广播丢失导致前端状态滞后 | 低 | 明确不保证送达；前端轮询兜底；不把 WS 事件当唯一事实来源 |
| **不支持多实例部署** | 中 | 已声明（Spec §4.14-3）；若未来水平扩展须引入租约或 `MIO_INSTANCE_ID` 抢占 |
| Run 所有权残余越权（不传 `agent`） | 中 | 已在 Spec §4.14-1 与本文件 ADR-9 声明；彻底关闭需 Run 级凭证 |
| 高风险词表类内容误触 | 低 | 本任务不涉及内容审查逻辑 |
