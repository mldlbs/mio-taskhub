# mio-taskhub 系统级评估报告

> **评估日期**：2026-09-30
> **评估基准**：生产库 `~/.mio_taskhub/taskhub.db`（3.5MB，2026-08-12 ~ 2026-09-30，49 天真实数据）+ 29198 行 Python / 45 个前端文件源码审计
> **证据标注**：**事实**＝代码或数据直接证明；**推断**＝由实现推断；**假设**＝缺证据
> **评估方法**：只信实际代码/数据/运行结果，不因 README、Spec、注释声称"已支持"就认定已实现

---

## 0. 评估数据来源（原始证据）

| 数据 | 值 | 来源 |
|---|---|---|
| 表数量 | 32 | `sqlite_master` |
| Task 总数 | 278 | `task` |
| Task 状态 | CANCELLED 142 / QUEUED 74 / COMPLETED 57 / FAILED 5 | `task` |
| Run 总数 | 119（FINISHED） | `run` |
| Run 成功/失败 | 67 成功 / 52 失败（**43.7% 失败率**） | `run.exit_code` |
| 失败分类 | heartbeat_timeout 24 / other 24 / user_cancelled 4 | `run.result` |
| 时间跨度 | 2026-08-12 ~ 2026-09-30 | `task.created_at` |
| Agent 数 | 9（全部 OFFLINE） | `agent` |
| Agent 真实用量 | opencode 72 run / codex 19 run / hermes 5 | `run.agent_name` |
| Idea 数 | 40（NEW 20 / CANCELLED 13 / FERMENTING 3 / BROKEN_DOWN 3 / ACCEPTED 1） | `idea` |
| Insight 数 | **3** | `insight` |
| depmetricssnapshot | 19109 | `depmetricssnapshot` |
| outboxevent | **0** | `outboxevent` |
| readevidence | 30（成功 run 67） | `readevidence` |
| discussion | 52（47 有结论） | `discussion` |
| scheduledjobexecution | 110（109 ok / 1 error） | `scheduledjobexecution` |
| Python 代码 | 29198 行 | 源码统计 |
| 测试 | 84 个文件 / 969 passed + 1 skipped | `pytest` |

**最关键的三个异常发现**：
1. **142 个 CANCELLED 中 97 个来自同一标题**：`[定时] 自动生成创意想法`（其中仅 5 个被 claim，无一个带 idea_id 关联）。
2. **`outboxevent` 0 行**：`OutboxEvent` 表 + 状态枚举齐全，但主流程未写入。
3. **`insight` 仅 3 条，而 `depmetricssnapshot` 有 19109 条**：指标在算，洞察不产，消费断点。

---

## 1. 一句话结论

**工程化 MVP（自用工具），介于"可运行 MVP"与"工程化 MVP"之间。**

依据（事实）：
- 核心链路真实运行（278 任务 / 119 run），状态机与证据门控有数据支撑；
- 但 `outboxevent` = 0 → 事务性发件箱设计存在、未接入；
- 43.7% run 失败率，其中 24 次 `heartbeat_timeout`；
- 51% 任务是 CANCELLED，97/142 来自同一个定时任务（自我制造垃圾）；
- 969 个测试全是 `TestClient` 进程内桩，**无真实环境 E2E**。

距离"生产候选"的主要差距不是功能，而是**可靠性验证与数据可信度**。

---

## 2. 系统真实能力

### 2.1 已真正实现（有数据证明）

| 能力 | 证据 |
|---|---|
| 任务生命周期状态机 | `models.py:23 can_transition` 硬编码转换表；非法转换被拒 |
| 原子认领（乐观锁） | `claim.py:103` 条件 UPDATE + `rowcount!=1` 回滚 |
| 文档链门控（ReadEvidence） | `submit_result` 校验指纹，缺失/变更 → 422；30 条 evidence 记录 |
| 心跳超时回收 | `background.py:232 _on_timeout`；24 次真实触发 |
| 定时任务（cron） | 110 次执行、109 ok |
| 多 agent 接入 | opencode 72 run / codex 19 run |
| SQLite WAL + busy_timeout | `db.py` `PRAGMA journal_mode=WAL` / `busy_timeout=5000` |
| 阶段质量门 + 棘轮基线 | `ratchetbaseline` 25 行；`doc-gate` 实测生效（WARN 放行） |
| 讨论/评审闭环 | discussion 52 条，47 条有结论 |

### 2.2 部分实现

| 能力 | 实现状态 | 缺口 |
|---|---|---|
| 事务性发件箱 | `OutboxEvent` 表/枚举齐全，**0 行数据** | 未接入主流程；实际用"提交后广播"（`install_broadcast_hooks`） |
| Agent 注册发现 | 表在、9 条记录 | `session_count` 字段代码有库无（迁移漂移）；全部 OFFLINE |
| 经验复用/记忆 | `experience_reuse` 接口在 | 生产 `memory.jsonl` 仅 123 字节 |
| 指标→洞察 | `depmetricssnapshot` 19109 行 | `insight` 仅 3 条；无消费路径 |

### 2.3 仅设计 / 未证明

- 可扩展性、多租户、权限隔离、SLO 保障 —— **无证据**。
- 真正跨任务的价值闭环（统计反向驱动决策）—— **无证据**。
- 高并发下的正确性 —— **无测试证据**。

---

## 3. 核心价值判断

### 3.1 它解决什么问题

多 AI agent（opencode/codex/hermes）协作时，缺少"谁在干什么、干到哪、产出物在哪、能否验收"的统一**任务编排 + 证据留痕**层。

```
用户/定时器
 ↓ 建任务(title + acceptance_criteria + doc_paths)
待领取队列 (stage=ready)
 ↓ agent claim（原子抢占 + run_id）
执行（心跳保活）
 ↓ submit_result（**ReadEvidence 指纹门控** ← 真差异化机制）
阶段推进（doc approved + 质量分≥80 + 棘轮基线）
 ↓
完成 → 事件流 → 看板/拓扑
 ↓
memory/observer 记录（**这一环薄弱**）
```

### 3.2 是否形成价值闭环

**部分闭环，存在断点**（事实 + 推断）：

- **良性闭环**：ReadEvidence 门控确实改变 agent 行为——强制"先读 spec/api 再写码"，30 条 evidence 为证。
- **断裂闭环**：`统计 → 分析 → 洞察` 的产物没有回头改变行为——`insight` 3 条 vs `depmetrics` 19109 条。**这是"计算闭环 ≠ 价值闭环"的典型案例**：指标算出但无人消费。
- **自我制造垃圾**：97 个定时任务几乎无人认领即被取消（仅 5 个 claim）。

### 3.3 AI 是否真正必要

| 环节 | 判断 | 理由 |
|---|---|---|
| agent 写码 | **必要** | LLM 不可替代 |
| 定时想法生成 | **不必要却用了** | 97 任务空转；insight 3 条；idea 40 条中 20 条 NEW 无人处理 |
| `next_action` 规则引擎 | **好的确定性设计** | 8 条显式规则 |
| 409 重试三档 | **LLM 挤占确定性** | 素材重复本可用去重 + 门控 |

**方向性问题**：用 LLM 全自动链路解决本可用"确定性触发 + 人工确认"解决的问题，**这是当前最大的方向性偏差**。

---

## 4. 架构评估

### 4.1 分层

```
web (React 45 files)
 ↓
api (32 modules, FastAPI)
 ↓  ← 直接操作 ORM（db.commit/db.add）
workflow (state_machine / transitions)
 ↓
models.py (SQLModel 486 行) + db.py (SQLite WAL)
```

### 4.2 优点

- 模块职责可辨（32 个 api 文件按领域切分）；
- 状态机显式集中（`models.py` + `workflow/transitions.py`）；
- WAL + 乐观锁选型对单机合理；
- 中间件齐全（auth / rate-limit / request-id / charset）。

### 4.3 缺陷（结构性）

1. **API 层直连 ORM**：`db.commit()` 遍布 32 个 api 文件，**无 Repository/Domain 层**，事务边界散落（事实）。
2. **一进程多职责**：FastAPI + 托盘 UI + 后台线程池 + cron + git-sync + observer autostart 全在 `main.py:lifespan`。
3. **状态语义不一致**：`TaskStage.CANCELLED` 是"持久化专用 hack"（`models.py:43` 自认注释），读时映射回 `BRAINSTORMING`。
4. **迁移漂移**：`agent.session_count` 代码有库无。

### 4.4 最大结构性风险

**缺少统一事务边界（无 SSOT）**：乐观锁仅在 claim 路径正确；`execute_replace` / 多线程广播 / cron 三处并发写同一 SQLite，靠 5s busy_timeout 兜底（推断：高并发下会出现 `database is locked`）。

### 4.5 扩展风险

| 扩展维度 | 级别 |
|---|---|
| 新 agent | 配置级（`agent_type` 已支持） |
| 新任务类型 | 配置级 |
| 新工作流状态 | **代码级**（`can_transition` 硬编码 dict） |
| 新数据源 | 配置级（observer/collect） |
| 新 UI | 配置级（React 组件） |
| 新租户 | **架构重构**（无 tenant 维度） |

---

## 5. 核心链路审计

| 核心链路 | 当前状态 | 风险 | 证据 |
|---|---|---|---|
| 建单→认领→心跳→提交 | 已实现且真实运行 | 43.7% 失败，24 次心跳超时 | 278 task / 119 run / 52 fail |
| 提交→ReadEvidence 门控 | 已实现，真硬 | 30/67 成功 run 有 evidence | `readevidence`=30 |
| 阶段推进→文档质量门 | 已实现 | `force=true` 可绕过，纪律依赖 | `doc-gate` WARN 实测 |
| 定时任务→想法生成 | **空转** | 97 取消 / 5 认领；今天仍 409 | task title 分布 |
| 统计→洞察→决策 | **断点** | insight=3，指标无人消费 | insight/depmetrics |
| 事务性发件箱 | **未接入** | 一致性靠"提交后广播" | outboxevent=0 |

### 发现的断点/旁路

- **旁路**：`git push --no-verify` 绕过文档门控（"靠纪律"）；`force=true` 绕过质量门与棘轮。
- **隐式状态**：`CANCELLED` stage 双语义。
- **空转**：定时器无"下游消费意愿"检查。

---

## 6. 可靠性评估

> **如果系统出现异常，会不会进入错误状态？** —— **会。**

- **心跳超时大量误判**：24 次 `heartbeat_timeout`（占失败 46%）。**推断**：长任务（平均 run 时长 1339s）与 180s 心跳窗口不匹配，或 agent 进程被单实例守卫/文件锁误杀。
- **恢复机制**：有 Detect（超时回收）+ Classify（`_is_system_timeout_failure`）+ 部分 Recover（requeue 20 次），但**无 Verify 环节**——requeue 后不校验是否真的恢复。
- **并发**：乐观锁优于裸读写，但非全路径；19109 条指标写入与业务写共享 SQLite。
- **输入异常**：Pydantic 覆盖请求校验（好）；但前端曾 `JSON.parse` 静默吞异常——"静默失败"是系统性习惯。

---

## 7. AI / Agent 评估

### Agent 的价值在哪里

- **在执行**：让不同 CLI agent 共享任务队列与证据账本——真实价值，只有多 agent 场景才成立。
- **不在思考**：创意识别、指标分析目前被 LLM 承担但无价值证据。

### 应改为确定性机制的地方（直接建议）

1. **定时想法生成** → "确定性触发 + 人工确认后才建任务"，或停掉（97/102 空转）。
2. **指标→洞察** → 改用**阈值告警规则**（`alertrule` 表已在）。
3. **409 死循环** → 确定性去重 + 新素材门控。

### 应继续用 LLM 的地方

- agent 写码、代码审查建议、`next_action` 的自然语言解释。

---

## 8. 测试可信度

### 测试证明了什么
单元/集成级正确性——969 passed 证明"模块在进程内、桩数据下行为正确"。状态机、门控、API 契约有覆盖。

### 测试没有证明什么
- **无真实 E2E**：`test_integration.py` 是 `TestClient(app)` 进程内（事实），非真服务 + 真 agent。
- **无并发测试**：无多线程同时 claim 的用例。
- **无真实 LLM/agent 路径测试**：全 mock。
- **无数据规模测试**：生产 278 任务，测试用空库。
- **无对照实验/量化指标**：无 A/B、无成功率基线。

### 还缺的真实实验
1. 10 agent 并发 claim 100 任务的**不重复性**压测；
2. 长任务（>180s）心跳窗口适配验证；
3. 真实 agent 跑通"建单→完成"全链并核对 evidence 指纹。

---

## 9. P0 / P1 / P2 / P3 问题

### P0（致命）

**P0-1 定时任务空转制造垃圾**
- **问题**：定时任务无"下游消费意愿"检查，每天自动建任务又取消
- **影响**：97/142 取消任务、污染看板与指标、浪费 LLM 调用
- **证据**：task 表 title 分布；97 个仅 5 个被 claim
- **修复建议**：加"下游消费意愿"开关 / 改人工确认制；清理历史
- **优先级**：最高

### P1（严重）

**P1-1 43.7% run 失败，心跳超时占 46%**
- **影响**：核心可靠性质疑
- **证据**：run 52 fail / 24 heartbeat_timeout
- **修复建议**：心跳窗口自适应（按 est_duration）；排查进程误杀
- **优先级**：高

**P1-2 事务发件箱未接入 → 事件一致性无保障**
- **影响**：状态与事件可能不一致
- **证据**：outboxevent = 0
- **修复建议**：接入 outbox，或删除死代码并明确用"提交后广播"
- **优先级**：高

**P1-3 无真实 E2E / 无并发验证**
- **影响**：可靠性无法证明
- **证据**：test_integration.py 用 TestClient
- **修复建议**：新增真服务 + 并发 claim 测试
- **优先级**：高

### P2（重要优化）

- **P2-1** API 层直连 ORM，无事务边界 → 加 Repository/UnitOfWork
- **P2-2** `TaskStage.CANCELLED` 双语义 hack → 分离持久化状态与展示状态
- **P2-3** 迁移与 models 漂移（`session_count`）→ 加 schema 校验测试
- **P2-4** insight 产物不被消费 → 接 `alertrule` 阈值规则

### P3（普通）

- 前端静默 `catch{}` 习惯（部分已修）
- 中文编码历史包袱
- `console.log` 单文件 16MB 无轮转（推断）

---

## 10. 最关键的 5 个问题（按风险/依赖排序）

1. **定时任务空转（P0-1）**——先止血，否则后续所有指标被垃圾污染，是其他修复的前提。
2. **心跳超时误判（P1-1）**——直接制造假失败，是"状态是否可信"的核心。
3. **事件一致性 / outbox（P1-2）**——决定"数据是否可信"，可靠性地基。
4. **真实 E2E + 并发验证（P1-3）**——没有它，任何"可靠性"宣称都只是假设。
5. **API 事务边界（P2-1）**——长期结构性风险，不阻塞前四项。

**排序依据**：1→2→3 是**数据可信度**的递进依赖；4 是**验证能力**；5 是**长期可维护**，可延后。

---

## 11. 下一阶段路线

### 阶段一：止血 + 建立基线
- **目标**：停止制造垃圾，量出真实成功率
- **工作**：关停/改造定时想法生成；清理 97 条历史；新建成功率看板
- **验收**：连续 3 天 CANCELLED 任务 < 5；成功率达基线并可见

### 阶段二：修可靠性
- **目标**：消除假失败
- **工作**：心跳窗口自适应；outbox 接入或删除；requeue 后加 verify
- **验收**：heartbeat_timeout 复现归零；状态机与事件 100% 一致

### 阶段三：证明它
- **目标**：从"代码支持"到"运行证明"
- **工作**：真服务 E2E；10 agent × 100 任务并发 claim 压测；真实 agent 全链
- **验收**：并发零重复认领；E2E 通过；evidence 指纹覆盖 ≥ 90% 成功 run

### 阶段四：生产化
- **目标**：可交付他人
- **工作**：Repository 层；迁移治理；多租户评估；SLO 报告
- **验收**：新开发者 1 天理解架构；`MIO_DOC_GATE_STRICT=1` 全绿

---

## 12. 最终裁定

| 问题 | 裁定 |
|---|---|
| **A. 有没有价值？** | **有，但是"工具价值"不是"平台价值"。** 对你作为多 agent 编排者真实节省协调成本（278 任务证据），ReadEvidence 是真差异化；尚未证明对他人有价值 |
| **B. 核心技术路线是否成立？** | **成立。** "状态机 + 证据门控 + 原子认领"是正确骨架，非噱头 |
| **C. 当前最大风险？** | **自我制造垃圾 + 假失败掩盖真实可靠性。** 系统在空转（97 取消），且可能误判（44% 失败含 46% 超时误判），导致看板数字不可信 |
| **D. 当前最大价值？** | **ReadEvidence 文档门控。** 少数"改变了 agent 真实行为"的机制，30 条证据，值得保留推广 |
| **E. 已被证据证明的能力？** | 任务状态机、原子认领、心跳回收（24 次）、cron 执行（110 次）、多 agent 接入（opencode 72 run）、文档门控（30 evidence） |
| **F. 只是设计目标的能力？** | 事务发件箱（0 行）、洞察驱动决策（insight=3）、经验复用（memory 123B）、可扩展性/多租户/权限隔离 |
| **G. 最该投入哪里？** | **减法 + 验证**：① 停掉空转的定时想法生成；② 修心跳误判；③ 建真实 E2E 与并发测试。**在证明可靠性之前，任何新功能都是在流沙上盖楼** |

---

## 附：与前轮判断的偏差纠正

前几轮把"扩大素材池 / 跨域提示"当作 409 的解法——**评估后更正**：那是治标。真实数据证明 409 的根因是"同一批素材被反复消费 + 当天新素材不足"，而更上游的问题是**这条链本身该不该每天自动跑**。97 个取消任务是这个判断的最硬证据。
