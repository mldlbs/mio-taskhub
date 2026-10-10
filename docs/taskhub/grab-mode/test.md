> 软件项目文档链 · 7 件套  
> 上游：[接口契约](docs/taskhub/grab-mode/api.md) | 下游：[部署运维](docs/taskhub/grab-mode/runbook.md)  
> 约定：正文**分点 + 换行**（每点一行，`-` 列表）；指引注释处填内容，未用章节删除。

# 测试与验收（Test & Acceptance）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v0.1 |
| 最后更新 | 2026-10-07 11:45 |
| 状态 | draft |
| 负责人 | 高志谦 |
| 适用范围 | 抢单模式 A 批（后端内核）的验收标准、测试范围与用例清单；含 P0 越权修复（任务 `d1b54de0`）的回归用例 |
| 明确不含 | 前端大厅 UI（B 批）的用例；性能压测与多实例部署验证（见 §2.4 不测范围） |

## 1. 验收标准

| FR 编号 | 验收标准 | 验证方式 | 对应用例 |
|---|---|---|---|
| FR-1 | 建单/改单可写入 `grab_mode`；建单缺省 `hybrid`，存量迁移为 `dispatch`；两者行为分别符合语义 | 单元 + 迁移回归 | TC-14、TC-24 |
| FR-2 | `grab_mode=grab` 的任务跑一轮调度 tick 后仍为 `queued`（不被指派） | 集成 | TC-11 |
| FR-3 | `hybrid` 任务在 `grab_deadline_at` 前不被派单、过期后可派单 | 集成 | TC-12 |
| FR-4 | `GET /tasks/hall` 只返 `queued+ready` 任务，且含悬赏/等待/竞争数/匹配分/过期倒计时 | 单元 + 契约 | TC-6、TC-20 |
| FR-5 | 大厅按五级键稳定排序（`cooldown_active → match_score → bounty → priority → pool_entered_at`） | 单元 | TC-20 |
| FR-6 | 并发抢同一任务恰 1 成功；失败方得 409 且带 `winner` | 并发 | TC-1、TC-2 |
| FR-7 | 每次抢单尝试落 `GrabAttempt`（含 `decision` 分类），不因业务回滚丢失 | 单元 | TC-21、TC-23 |
| FR-8 | 悬赏按等待时长阶梯爬升、封顶幂等、不重复发事件 | 单元 + 集成 | TC-15、TC-16 |
| FR-9 | 抢中后超开工窗口且**未开工**才回收回池；已开工不回收 | 并发 | TC-7、TC-8、TC-9 |
| FR-10 | 失约多的 agent 排序靠后（软惩罚，不禁止抢单） | 单元 | TC-20 |
| FR-11 | 每日拒单上限并发不超额；跨 UTC 日正确重置 | 并发 | TC-17、TC-18 |
| FR-12 | 统计口径正确：分位最近邻法、去重、空样本返 `null`、系统故障不进分母 | 单元 | TC-21、TC-22 |
| SEC-1（P0） | 非 run 持有者调用 heartbeat/result 返 403，且不修改 Run/Task、不产生 `task_result` 事件 | 单元 + 线上 | TC-S1、TC-S2 |
| SEC-2（P0） | 所有者正常路径行为与修复前一致 | 回归 | TC-S3、TC-S4 |
| SEC-3（P0） | 迟到的结果提交仍返 200（不得被状态校验打断 `late_result_recovery`） | 回归 | TC-S6 |
| SEC-4（P0） | 所有权校验先于状态校验（不泄漏他人 run 状态） | 单元 | TC-S7 |
| DOC-1 | 「逐字节等价」改为可验证兼容性清单，回归验证 `dispatch` 任务行为不变 | 集成 | TC-14 |
| TRC-1 | 全部 FR-n 均被至少一条用例引用（追溯无缺口） | 报告 | 报告层校验 |

## 2. 测试范围

### 2.1 单元测试

- 领域函数：`match_score` 各维度互斥与边界、`sort_key` 五级排序、`enter_pool` 补齐 deadline、悬赏阶梯计算与封顶、额度原子递增 SQL。
- 守卫：`assert_run_owner`、`assert_run_submittable` 的判定矩阵（owner / intruder / 已结束 / 迟到提交）。
- 口径：分位最近邻法、去重规则、空样本 `null`。
- 迁移：加列幂等（重复执行不报错、不改已有数据）。

### 2.2 集成测试

- 派单让位：构造 `grab` / `dispatch` / `hybrid` 三种任务，跑真实调度 tick，核对候选集差异。
- 后台线程：悬赏引擎与窗口回收各跑一轮，核对字段变更与事件条数。
- 入池路径：失约回池 / 主动退单回池 / 依赖释放放行 / 重试退避重入，均写全 `pool_entered_at` 与 `grab_deadline_at`。

### 2.3 并发与 E2E

- **并发**：用**真实 SQLite + 真实多线程**（不 mock 事务），覆盖抢单、悬赏推进、额度扣减、开工与回收竞争。
- **E2E**：建任务 → 挂大厅 → 抢单 → 心跳 → 提交，全链路走 HTTP 接口。

### 2.4 明确不测的

| 不测项 | 原因 |
|---|---|
| 前端大厅交互 | B 批另立任务 |
| 性能 / 压测 | 单机小规模（池 < 1000 条），无压测环境 |
| **多实例部署** | 本版本不支持（无实例租约，见 Spec §4.14-3）；多 hub 并行跑后台线程的结果**不作保证**，故不写会误导的用例 |
| 跨机 / 非 loopback 暴露 | hub 默认仅监听 `127.0.0.1`，越权用例按本机威胁模型设计 |
| 广播必达性 | 已明确**不保证送达**（无 outbox 补发，Spec §4.10），故不断言「事件必达」，只断言「状态与事件同事务」 |

## 3. 用例清单

| 用例编号 | 覆盖需求 | 前置 | 步骤 | 预期 |
|---------|---------|------|------|------|
| TC-1 | FR-6 | 任务 queued+ready，8 个在线 agent | 8 线程同时 `POST /tasks/grab` 同一 task_id | 恰 1 个 200；其余 409 `grab.task_taken` 且响应含 `winner` 与 `grabbed_at`；任务仅 1 条有效 run |
| TC-2 | FR-6 | 同上 | 一线程 `grab`、另一线程 `claim` 争抢同一任务 | 恰一方占用成功，另一方得 409；任务不重复占用 |
| TC-3 | 事务边界 | Run 创建注入异常 | 调 `grab` 触发 Run 创建失败 | Task 占用**回滚**为 `queued`；不留「已占用但无 run」状态 |
| TC-4 | FR-6 | 同一 agent 已持有该任务 run | 对同一 task_id 重复 `grab` | 幂等返同一 run；**不**新增 `won` 留痕、**不**重复发 `task_grabbed` |
| TC-5 | FR-6 | agent A 持有任务 X 的 run | agent A 抢任务 Y（`grab_mode=grab`） | 409 `grab.agent_busy`；**不**返回任务 X 的 run |
| TC-6 | FR-4、跨对象不变量 | 池中有 ready / 非 ready / 终态任务各一 | `GET /tasks/hall` | 只返 `queued+ready`；终态与未 ready 任务不出现；响应含 `bounty`/`waited_seconds`/`competitors`/`match_score`/`expires_in`/`cooldown_active` |
| TC-7 | FR-9 | run 处于 `claimed`，窗口已过，但有 `checkpoint` 或曾上报 `progress>0` | 跑窗口回收线程 | **不回收**；run 仍 `claimed`；不记失约 |
| TC-8 | FR-9 | run 处于 `claimed` 且无任何开工标记 | 心跳线程与回收线程并发触发 | 恰一方 `rowcount==1`；败方放弃且**不**记失约；不出现同任务双执行 |
| TC-9 | FR-9 | 同一 run 被两个回收执行者处理 | 并发跑两轮回收 | 仅第一次生效；`miss` 留痕 1 条、失约计数 +1；第二次 `rowcount=0` |
| TC-10 | FR-9 | 任务已正常完成 | 跑窗口回收 | 任务不被重入池；无 `grab_missed` 事件 |
| TC-11 | FR-2 | `grab_mode=grab` 的 ready 任务 + 空闲在线 agent | 跑一轮 `_assign_to_idle_agents` | 任务仍 `queued`；`task_assigned` 事件不含它 |
| TC-12 | FR-3 | `hybrid` 任务分别在 deadline 前 / 后 | 各跑一轮调度 tick | 前：不派单；后：可被指派 |
| TC-13 | FR-3 | `hybrid` 任务缺 `grab_deadline_at` | 跑后台 tick | `enter_pool` 补齐 deadline 与 `pool_entered_at`；告警可查 |
| TC-14 | FR-1、DOC-1 | 含存量任务（迁移后 `dispatch`） | 跑兼容性清单 §5.3 全部 5 条 | 候选集/优先级/agent 选择/超时回收语义与改造前一致；差异逐条可解释 |
| TC-15 | FR-8 | 任务等待时长跨 2 个阶梯 | 两个线程并发跑悬赏引擎 | 仅一次有效加价；`task_bounty_surged` 恰好 1 条 |
| TC-16 | FR-8 | 任务 bounty 已达 `cap`；另有已完成阶梯的任务 | 跑悬赏引擎 | 不再加价、不发事件；已完成阶梯不重复处理（重启后同样） |
| TC-17 | FR-11 | agent 当日额度为 19 | 5 线程并发拒 5 个任务 | 恰 1 次成功（达 20）；其余 429 `grab.decline_limit`；计数恰为 20 |
| TC-18 | FR-11 | agent `decline_day` 为昨日、计数 20 | 跨 UTC 日并发拒单 | 额度重置为 0 后成功 1 次；无重复重置 |
| TC-19 | FR-11 | 任务已被他人抢走 | 调 `decline` | 409 `grab.task_taken`；**不扣额度**、不写留痕 |
| TC-20 | FR-4、FR-5、FR-10 | agent 处于冷却；池中任务条件各异 | `GET /tasks/hall` | 冷却任务排位靠后但**仍出现在列表**；五级排序稳定；同分按 `pool_entered_at` FIFO |
| TC-21 | FR-7、FR-12 | 注入 DB 异常 + 正常竞争各若干 | 跑抢单与统计 | 异常记 `decision='error'` 且**不进** `success_rate` 分母；正常竞争记 `reject` |
| TC-22 | FR-12 | 窗口内样本数已知（含 0 样本场景） | `GET /grab/stats` | 分位符合最近邻法；`lost` 不进分子；无样本时分位返 `null` 而非 0 |
| TC-23 | 事务边界 | 构造提交后回滚 | 检查事件表 | 不产生已提交状态对应的成功事件 |
| TC-24 | FR-1 | 存量库 | 迁移连跑两次 + 旧行字段为 NULL | 幂等；读路径、大厅排序、派单候选均按 `dispatch` 兜底 |
| TC-S1 | SEC-1 | run 属 owner | intruder 调 heartbeat | 403 `run.not_owner`；run 状态与 progress **未变** |
| TC-S2 | SEC-1 | run 属 owner | intruder 调 result | 403；任务未完成；**无** `task_result` 事件；run 未终结 |
| TC-S3 | SEC-2 | run 属 owner | owner 心跳 + 提交 | 心跳 200 `running`；提交 200 且任务 `completed`；恰好 1 条 `task_result` |
| TC-S4 | SEC-2 | run 属 owner | **不传 agent** 调心跳 | 200（向后兼容，不被打挂） |
| TC-S5 | SEC-3 | run 已 `finished` | 调心跳 | 409 `run.not_active`；**终态不得被改回 running** |
| TC-S6 | SEC-3 | 已成功提交（任务 completed） | 再次提交成功 | 409 `run.result_already_submitted`；不产生第二条 `task_result` |
| TC-S7 | SEC-4 | run 已 finished 且属他人 | intruder 调 result | 403（所有权先判），**不是** 409（不泄漏 run 状态） |
| TC-S8 | SEC-1 | run 不存在 | 任意 agent 调 result | 404（不存在性先于所有权暴露） |
| TC-S9 | SEC-3 | reaper 判超时后 run 已 finished，任务已 FAILED（系统超时） | 迟到提交成功 | **200**；按 `_is_system_timeout_failure` 决定是否纠正——护栏见 `tests/test_timeout_misjudge.py` |
| TC-S10 | SEC-3 | 同上，但任务 FAILED 源于 agent 主动报失败 | 另一路迟到成功 | 200 但**任务仍 FAILED**（不得翻案） |

## 4. 验收流程

- **谁验收**：任务负责人（高志谦）；安全相关条目（SEC-*）额外需人工确认。
- **何时验收**：A 批代码合入前跑全量；合入后打 exe + 线上抽查（拆包验标识符 + 打真实接口）。
- **通过条件**：
  1. TC-1..TC-24 全部通过；
  2. TC-S1..TC-S10 全部通过（其中 TC-S9/S10 已有存量护栏，**不得为了让新校验通过而放宽 `late_result_recovery`**）；
  3. 全量测试**不新增**失败——已知既存失败为 `tests/test_spinning_guard.py` 的 4 个 `webhook_guard`（改动前即失败，`AttributeError`，与本任务无关）；
  4. 质量门：requirement / spec / api / test 四份均 `errors = 0` 且 `score ≥ 80`。
- **不通过的回退路径**：
  - 并发类失败（TC-1/2/8/9/17/18）→ 判定 CAS 失效，**回退该批实现**，不得改测试迁就；
  - 口径类失败（TC-22）→ 修实现口径，不得在测试里放宽断言；
  - 安全类失败（TC-S*）→ **阻断合入**，先修再谈进度。

## 5. 已知失败与处置

| 失败用例 | 性质 | 处置 |
|---|---|---|
| `test_spinning_guard.py::test_webhook_guard_*`（4 个） | **既存失败**，与抢单无关（`AttributeError`，`git stash` 验证过基线） | 不在本任务范围；单独记 issue，不阻塞 A 批 |
| `test_night_runner.py::test_spawn_and_reap` | 已知 flaky（进程回收时序） | 单跑必过；不计入阻塞 |
| M 批新增的越权残余风险 | **已知限制**：不传 `agent` 时仍可凭 `run_id` 越权 | 已在 Spec §4.14-1 声明；彻底关闭需 Run 级凭证，另立任务 |
