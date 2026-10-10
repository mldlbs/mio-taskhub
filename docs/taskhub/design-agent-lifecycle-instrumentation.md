# 设计：Agent 生命周期埋点（task 5b4fa957 / P1-OBS-2）

> **状态：仅设计，不落代码。** 评审埋点覆盖后再决定是否实施。
> 裁定来源：R280 / R281。**唯一目标**：区分
> **① 启动失败 / ② 启动成功后死亡 / ③ Agent 活着但心跳链路失效**。
> 纪律：不改 reaper 判定 / heartbeat timeout / Evidence 门控 / 事务架构；不做 Evidence→Insight；不盲目加启动重试。

---

## 1. 问题定义

现有观测（`reaper_decision`，首批 5 条）只能证明「超时后被正常回收」，**无法解释为什么 Agent 没有心跳**。
根因分叉三选一，当前无数据可判：

```
A. 启动失败          —— Popen 失败 / opencode 未起 / 秒退
B. 启动成功后死亡    —— 起来过、发过心跳、中途死
C. 活着但心跳链失效  —— 进程还在，Hub 却收不到心跳
```

**证据缺口**：`progress=50` 恒定 + `never_started(progress=0)` 与 `agent_offline(progress=50)` 并存，
说明我们既不知道进程有没有起来、也不知道起来后活了多久、更不知道进程何时退出。

---

## 2. 关键语义修正（R281 强制）

| 概念 | ❌ 错误定义 | ✅ 正确定义 |
|---|---|---|
| `startup_success` | `subprocess.Popen` 返回成功 | **Agent 已完成启动并进入「可执行状态」** |

**为什么**：`Popen 成功 ≠ Agent 真正启动成功`。opencode 是个 CLI，`Popen` 后它可能：
- 秒退（参数错/环境错）——Popen 成功但启动失败；
- 卡在初始化（加载模型/登录）——Popen 成功但还没到可执行状态。

### 两级证据（A1 修订 · 承接评审阻断项 1）

**单一锚点不成立**：若只用"首心跳"判 startup_success，则**心跳链失效（C）时首心跳永不到 → 被误判为启动失败（A）**。
本轮评审（技术+红队）要求拆成**两级独立证据**：

| 证据 | 含义 | 观测方式 | 权威来源 |
|---|---|---|---|
| `process_alive` | **进程层面**：从 Popen 到退出/回收的存活时长 > `grace` | **结果态回填**（非阻塞，R2-D1） | 执行侧（时长）|
| `agent_ready` | **执行层面**：Agent 真正进入执行循环 | **Hub 侧 `run.last_heartbeat` 存在且晚于 `run.started_at`**（R2-D2） | **Hub 侧为准** |

> **判定**：
> - `startup_success` = **`agent_ready=yes`（Hub 侧收到首心跳）**
> - `startup_failure` = 进程秒退 / 存活≤grace 且**从未 agent_ready**
> - **C（心跳链失效）= `process_alive=yes` ∧ `agent_ready=no` ∧ 本地 `heartbeat_sent_count>0`**
>   - **注意（红队 R2-3）**：仅 `alive∧¬ready` **不足以**判 C——"网络分区"同样表现为 Hub 无记录。
>   - **坐实 C 的必要条件**：本地记录了"我发了心跳"（`heartbeat_sent_count>0`）而 Hub 无 `last_heartbeat` ⟹ 链路问题；本地 sent=0 ⟹ agent 根本没工作（A 的延伸）。
>
> **R2-D1 · 非阻塞回填**：`process_alive` **不在主线程 sleep 等 grace**（那会阻塞 15s，违反 A2）。它是**结果态字段**：在 `process_exit` 或 reaper 回收时，用「`finished_at/回收时刻 − started_at` > grace」回填。
> **R2-D2 · Hub 侧为准**：`agent_ready` 不能是 worker 本地标志（本地无法区分"没工作"vs"发了没到"）。以 **Hub 侧该 run 是否收到过心跳**为准。

**为什么 `process_alive` 不可作 success**（红队）：僵尸/卡死进程"活着"无意义；opencode 若 daemon 化脱离，Popen 立即返回也误判 alive。故 alive 只作 C 分母/辅助，success 必须等 `agent_ready`。

---

## 3. 最小事件模型

### 3.1 事件类型（**统一挂在既有 `event` 表，`type` 前缀 `agent_lifecycle.`**）

```
agent_lifecycle.process_start      # 进程已 launch（Popen 返回）
agent_lifecycle.startup_success    # 观察到 agent_ready（首个 run 心跳）
agent_lifecycle.startup_failure    # 未进入可执行状态（秒退 / 超 grace 且进程已退出 / launch 异常）
agent_lifecycle.process_exit       # 进程结束（正常/异常）
```

**不新增** `heartbeat_lost` 主动事件——**交给 reaper 关联**（`reaper_decision` 已有 `hb_lag`）。
**不每次心跳写 Event**——聚合计数在 `process_exit` / `reaper_decision` 时汇总，避免污染 1899 条 event 表。

### 3.1.1 命名空间隔离（A3 · 承接评审补充项 7）

`agent_lifecycle.*` 是**独立 type namespace**。裁决/统计脚本**必须显式排除**此前缀，否则自加埋点会污染"任务事件密度"等指标（红队攻击点 2）。
> 权衡：红队建议单列 `agentlifecycle` 表最干净；但**改 schema 违反本轮边界**，故采 **namespace 隔离 + 裁决显式排除**方案，接受"同表不同前缀"的折中。

### 3.2 字段（payload，JSON）

| 字段 | 出现在 | 说明 | 数据来源 |
|---|---|---|---|
| `run_id` | 全 | run 标识 | idle_worker |
| `task_id` | 全 | 任务标识 | idle_worker |
| `agent_id` | 全 | agent 名 | idle_worker |
| `ts` | 全 | 事件时间（UTC ISO） | 客户端 |
| `event_type` | 全 | 上述 4 种 | — |
| `pid` | start/exit | 子进程 pid | `proc.pid` |
| `exit_code` | exit | 退出码 | `proc.returncode` |
| `startup_error` | failure | 失败原因 | 异常/超时描述 |
| `startup_latency_ms` | success | Popen→首心跳耗时 | 计时 |
| `process_alive` | exit/failure | **进程层面**是否存活超 grace（**结果态回填**）| 时长计算（R2-D1）|
| `agent_ready` | exit/success | **执行层面**是否进执行循环 | Hub 侧 last_heartbeat（R2-D2）|
| `heartbeat_sent_count` | exit | **本地发出**的心跳次数（坐实 C）| worker 计数（R2-D5）|
| `heartbeat_count` | exit | 本 run 心跳总数 | 计数器 |
| `first_heartbeat_at` | exit | 首心跳时刻 | 计时 |
| `last_heartbeat_at` | exit | 末心跳时刻 | 计时 |
| `alive_seconds` | exit | 进程存活时长 | 计时 |
| `grade` | exit/failure | 证据完整度（见 §4.2） | 推断 |
| `launched` | start | 实际执行的 argv（截断） | idle_worker |

> `run_id/task_id/agent_id` 复用 `event` 表已有列（`entity_id`/`run_id`），其余入 `payload`。

### 3.3 复用既有列（避免新表）

`Run` 已有 `started_at / last_heartbeat / exit_code / progress`；
`Event` 已有 `type / entity / entity_id / run_id / payload`。
**本设计不新增表、不改 schema**——纯写 `event`，故此改动**不触碰迁移/事务架构**。

---

## 4. 埋点位置（执行侧 = `packaging/idle_worker.py`）

> 这里是真正 launch agent 进程的地方（当前唯一生产执行路径）。**不碰 hub 侧调度**。

```
claim_once 成功
  ↓
[埋点] emit(process_start, pid=None, launched=argv)        # 紧接 wire_argv 后
  ↓
Popen (subprocess.run 内部)
  ├─ 首心跳到达 → 置 flag=ready，记录 first_heartbeat_at   # 在 _hb() 里
  │     → [埋点] emit(startup_success, startup_latency_ms)
  ├─ 进程秒退(exit≠0 且 flag≠ready) → [埋点] emit(startup_failure, startup_error)
  └─ 超 grace 无心跳(仍存活)        → [埋点] emit(startup_failure, startup_error="no heartbeat within grace")
  ↓
进程结束
  → [埋点] emit(process_exit, exit_code, heartbeat_count, first/last_heartbeat_at, alive_seconds)
  ↓
submit result（不变）
```

**实现约束（A2 修订 · 承接评审阻断项 2/3）**：

1. **异步 fire-and-forget**：埋点**不得进执行关键路径**。每次 emit 走独立 daemon 线程 + 短超时（≤2s）+ **丢包不重试**。
   - 理由（红队）：同步上报若拖慢启动，会**制造它要观测的"启动慢/失败"**——观测者成故障源。
2. **执行侧本地落盘（唯一真相源）**：每条 lifecycle **先 append 到本地 jsonl**，再异步尝试上报 Hub。
   - **R2-D4 · 按 pid/run 分文件**：`~/.mio_taskhub/lifecycle_probe/{pid}.jsonl`（或 `{run_id}.jsonl`），**天然免锁**，避免并发写坏。
   - 理由（运维）：**Hub 是单点**，故障时刻恰可能 Hub 不可用 → 纯 Hub 依赖会**连带失明**。
   - **R2-D7 · 开关语义**：`MIO_LIFECYCLE_PROBE` 关闭时**本地也不落盘**（彻底零副作用）。
3. **`process_exit` 允许缺失 + 交叉推断**（阻断项 3）：worker 被杀/卡时 exit 发不出——**而这正是要观测的场景**。
   - 因此 `process_exit` **非必需**；Hub 侧在 reaper 回收时按 run_id 交叉推断：`有 process_start ∧ 无 process_exit ∧ 被回收 ⇒ 进程未正常退出`（payload `grade=inferred_exit`）。
4. **R2-D3 · 放弃"worker 补传"（worker 短命，无人补传）**：
   - 改为**非实时 ingest**：Hub 启动时或人工运行 `scripts/ingest_lifecycle.py` 扫描本地 jsonl 导入。
   - **幂等键 = `run_id + event_type + ts`**（运维红线：重复 ingest 不得让指标翻倍）。
   - **不新增常驻守护进程**（遵守边界）；诊断脚手架不需要实时。
   - **R2-D6 · TTL 边界**：TTL 只清 **Hub 侧 event**；**本地 jsonl 不清**（原始真相源，裁决后人工清）。
4. **所有埋点 best-effort**：上报/落盘失败 try/except 吞掉，**绝不影响任务执行/提交**（与 `reaper_decision` 同纪律）。
5. **`agent_ready` 信号**：现有 `_hb()` 线程已知每次心跳，加一个**按 run 隔离**的 `first_hb` 标志（**勿用全局变量**，worker 循环跑多任务会串）。
6. **env 开关**：`MIO_LIFECYCLE_PROBE=1` 才启用；默认关闭（见 §4.3）。

### 4.1 上报通道（D3 决议）

**最小化 Hub 改动，优先复用现有通道**：不新增 `POST /experiments/...` 写路径（避免加剧 api 层 59 commit 问题）。
- 首选：复用已有**通用事件口**（若存在）；
- 否则：执行侧落盘为主，Hub 侧仅在 reaper 回收时做**交叉推断**（读本地/已有 event 写入推断结果）——Hub 改动压到最小。
- **明令**：不新增 api 写路径、不改事务边界。

### 4.2 证据完整度 `grade`（A2）

| grade | 含义 |
|---|---|
| `complete` | start + success/failure + exit 齐全（正常路径） |
| `inferred_exit` | 有 start 无 exit，由 reaper 交叉推断（进程未正常退出）|
| `no_start` | 连 start 都没（埋点自身失效 → 见 §4.3 自检）|

### 4.3 可运维性（A3 · 承接评审补充项 4/5/6）

- **可开关（R2-D7）**：`MIO_LIFECYCLE_PROBE=1` 启用；关闭时**本地也不落盘**、零网络调用、彻底零副作用。
- **埋点自检（R2-D8）**：探针存活/落盘失败计数**写入本地 jsonl**（不只 Hub，否则 Hub 挂则自检失明）；若本地 jsonl 长时间无新条目但**有任务在跑** → 判定**埋点静默失效**。
- **保留期/TTL（R2-D6）**：Hub 侧 event **TTL 14 天**；**本地 jsonl 不随 TTL 清理**，裁决后人工清。
- **定位**：这是**可撤退的诊断脚手架**，非产品功能；裁决后应可移除/降级（产品视角）。
- **ingest 工具（R2-D3）**：`scripts/ingest_lifecycle.py`，幂等（`run_id+event_type+ts`），人工/Hub 启动触发，非常驻。

---

## 5. 可判定矩阵（交付的核心价值）

| 观测结果 | 根因方向 | 判定依据（数据） |
|---|---|---|
| `process_start` → `startup_failure`(秒退) | **A. Agent 启动失败** | 有 start 无 success，exit 快 |
| `process_start` → 无 success、进程退出 | **A. 启动阶段崩溃** | start + exit，无 success |
| `startup_success` → 有心跳 → `process_exit` | **B. 启动后中途死亡** | success + hb_count>0 + exit |
| `startup_success` → 无心跳 | **C/异常**：起来但未工作/心跳链断 | success + hb_count=0 |
| 心跳正常 → 长时间消失 → reaper | **B.** 后续死亡或心跳链中断 | hb_count>0 + reaper hb_lag≫timeout |
| **进程仍存活 + Hub 无心跳** | **重点怀疑 C. 心跳链路** | 无 process_exit 但有 reaper |

> 最后一行是**唯一能证明"心跳链路自身问题"**的组合——需要 `process_exit` 与 `reaper_decision` 做**交叉关联**（同 run_id）。

---

## 6. 与现有观测的关联方式

```
agent_lifecycle.process_start ─┐
agent_lifecycle.startup_success ┤  按 run_id 串成一条生命周期时间线
agent_lifecycle.process_exit    ┤  ← 与 reaper_decision(run_id) 交叉
reaper_decision (已有)          ┘
```

**交叉查询示例（设计验证用，非本次实施）**：
- 对每个被 reaper 回收的 run，检查是否存在 `process_exit`：
  - 有 → Agent 确实死了（真死）；
  - 无 → 进程没退但被回收 → **可疑 C**（`grade=inferred_exit`）。
- 检查 `process_alive` 与 `agent_ready`（A1 两级证据）：
  - `process_alive=no` → 启动失败（A）；
  - `process_alive=yes ∧ agent_ready=no` → **心跳链失效 / 起来未工作（C）**；
  - `agent_ready=yes` 但后续消失 → 启动后死亡（B）。

---

## 7. 埋点覆盖评审清单（实施前必过）

| # | 检查项 | 通过标准 |
|---|---|---|
| 1 | 四类事件是否覆盖三选一全部分叉？ | 是（矩阵 6 行每行有唯一事件组合） |
| 2 | `startup_success` 是否锚定"可执行状态"而非 Popen？ | 是（**两级证据**：agent_ready 判定 success；process_alive 仅作 C 分母） |
| 3 | 是否新增表 / 改 schema？ | **否**（纯写 event）|
| 4 | 是否触碰 reaper/timeout/门控/事务？ | **否** |
| 5 | 是否污染 event 表（每心跳写）？ | **否**（聚合在 exit）+ namespace 隔离 |
| 6 | 上报失败是否影响执行？ | **否**（异步 best-effort，不进关键路径）|
| 7 | 能否按 run_id 与 reaper_decision 关联？ | 是（含 inferred_exit）|
| 8 | 是否有测试覆盖字段与分支？ | 待实施阶段 |
| 9 | 是否可"事后从数据还原一次完整生命周期"？ | 是（本地 jsonl + 补传）|
| 10 | 是否区分 opencode 启动失败 / 启动慢 / 中途死 / 心跳链断？ | 是（C=alive∧¬ready）|
| 11 | 观测是否可能反作用（拖慢启动）？ | 否（异步 fire-and-forget）|
| 12 | Hub 单点故障时是否连带失明？ | 否（本地落盘，恢复补传）|
| 13 | 埋点自身失效是否可发现？ | 是（自检指标）|

---

## 8. 风险与反问（评审后更新）

1. ~~`startup_success` 依赖首心跳会把 C 误判为 A~~ → **已解决（A1）**：两级证据 `process_alive` + `agent_ready`，**C = alive ∧ ¬ready**。
2. **观测反作用**（红队）→ **已对抗（A2）**：异步 fire-and-forget、不进关键路径、短超时、丢包不重试。
3. **Hub 单点连带失明**（运维）→ **已对抗（A2）**：执行侧本地 jsonl 落盘 + 恢复补传。
4. **`process_exit` 可能发不出**（技术）→ **已对抗（A2）**：允许缺失，reaper 侧交叉推断 `grade=inferred_exit`。
5. **grace 存活可被廉价伪造**（红队）→ alive 只作 C 分母，**不作 success**；success 必须 `agent_ready`。
6. **归因陷阱**（红队）：埋点只给 A/B/C **相关性**；**根因裁决必须配对照组实验**，不能仅凭埋点定因果。
7. **opencode 是长任务 CLI**：grace 默认 15s 可配置。
8. **多任务串行**：`first_hb` 标志**按 run 隔离**（勿全局）。
9. **执行侧埋点是否算"改 Agent 启动逻辑"？** → 不算：**只加旁路观测**，不改 launch/exec/submit 任何行为路径。

---

## 9. 明确不做（对齐 R280/R281）

- ❌ 不改 reaper 判定 / heartbeat timeout / Evidence 门控
- ❌ 不做 Evidence→Insight
- ❌ 不为降低 38% 加启动重试
- ❌ 不做事务架构大手术（59 commit）
- ❌ 不为此设计新增表或迁移

---

## 10. 决议项（首轮评审已裁定 · R281 修订）

| # | 议题 | 决议 |
|---|---|---|
| D1 | `startup_success` 锚点 | **改**：两级证据 `process_alive` + `agent_ready`；C=alive∧¬ready（阻断项1）|
| D2 | grace 秒数 | **15s 默认，可配置**（`STARTUP_GRACE_SECONDS`）|
| D3 | 上报通道 | **最小化 Hub 改动**，复用现有通道，不新增 api 写路径 |
| D4 | first_heartbeat 记录 | 执行侧计时为主 **+ 必须叠加本地落盘**（"hub 零改动"因单点不成立，阻断项2）|
| D5 | heartbeat_count | 入 exit payload，**不改 schema** |

---

## 11. 首轮评审记录与修订（R281）

**评审**：discussion `b767d447`（四视角 产品/技术/红队/运维），结论**有条件通过**。

| 阻断项 | 出处 | 落点 |
|---|---|---|
| 1. D1 两级证据 | 技术+红队 | §2 两级证据、§3.1、§6、§7 |
| 2. 异步+本地落盘+补传 | 红队+运维+技术 | §4 实现约束 |
| 3. process_exit 交叉推断 | 技术 | §4.2 grade、§6 |

**补充项**：env 开关 / 自检 / TTL / namespace 隔离 → §3.1.1、§4.3；定性可撤退脚手架 → §4.3。

**行动项**：A1–A3（本文档已修订）；A4 二次评审——**通过前不落码**。

---

## 11.1 二次评审（A4）修订 · R2 决议

discussion `12b29e53`（技术/红队/运维）：**A1–A3 方向正确、首轮 3 阻断项已堵**；暴露 R2 层面 4 阻断项，已全部修订（A5）：

| # | R2 阻断项 | 决议 | 落点 |
|---|---|---|---|
| R2-D1 | process_alive 判定时机与 A2 冲突 | **结果态回填**（非阻塞）| §2、§3.2 |
| R2-D2 | agent_ready 归属不明 | **以 Hub 侧 last_heartbeat 为准** | §2、§3.2 |
| R2-D3 | "补传"自相矛盾（worker 已死）| **弃补传，改非实时幂等 ingest** | §4.3 |
| R2-D4 | jsonl 并发写坏 | **按 pid/run 分文件** | §4 |
| R2-D5 | C 与网络分区不可分 | **新增本地 `heartbeat_sent_count`** | §2、§3.2 |
| R2-D6 | TTL 误清本地 | **TTL 只清 Hub event** | §4、§4.3 |
| R2-D7 | 开关语义 | **关闭时本地也不落盘** | §4、§4.3 |
| R2-D8 | 自检失明 | **自检写本地 jsonl** | §4.3 |

**关键认知（红队）**：`alive∧¬ready` 不足以判 C——必须叠加**本地 `heartbeat_sent_count>0`**（本地发了、Hub 没收到 = 坐实链路问题）。这是 C 与"网络分区"在单机上唯一的区分手段。

---

## 12. 交付裁定

> **首轮评审「有条件通过」→ A1–A3 修订 → 二次评审（A4）通过方向 → R2 阻断项已修订（A5，本文档）。**
> **下一步：A6 实施**（观测脚手架，不改逻辑）。实施前确认边界未越。
> `reaper_decision` 继续积累仅作**辅助**（不作主证据）。
>
> 逻辑链：**Agent 生命周期观测补齐 → 自然样本积累 → 根因裁决 → 再修。**
