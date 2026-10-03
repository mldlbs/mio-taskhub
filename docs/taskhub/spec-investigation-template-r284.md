# Investigation 模板 Spec（R284 · design/spec-only）

> 上游：`design-investigation-template-r283.md`（字段/状态约束/完成门控三项评审）
> 裁定（R284）：**Q1=A、Q2=A、Q3=B、Q4=B、Q5=A**
> 红线：**不新增表 / 不新增状态 / 不新增常驻进程 / 不实现 observe 环 / 不改 TaskState·TaskStage。**
> 本文为**契约（spec）**，描述「应该怎样」；**不含实现**。

---

## 0. 一句话定义

`investigation` 是 TaskHub 的**证据约束执行模板**：在既有 Task 生命周期之上，为"完成"这一步附加一条**裁定合法性门控**——强门控的是「**有没有合法裁定**」，不是「裁定必须是阳性/阴性」。

---

## 1. 裁定汇总（对本 spec 具有约束力）

| # | 问题 | 裁定 | 本 spec 的落地含义 |
|---|---|---|---|
| Q1 | inconclusive 后能否直接 close | **A** | `next_action` 与 `verdict` **解耦**；`inconclusive` 是合法终局，允许 `next_action=close` |
| Q2 | task_kind 能否回退 | **A** | `investigation → normal` 必须 `force`，且**留痕**（TaskEvent） |
| Q3 | review 是否前置校验 | **B** | review 阶段 **warning 提示、不阻断**；阻断只在 done |
| Q4 | evidence_source 校验强度 | **B** | 软校验：来源枚举合法 + basis 存在是**硬**要求；`evidence_required` 未满足是 **warning** |
| Q5 | 是否引入 observe 环 | **A** | **不做**；investigation 是一次性协议，不形成循环生命周期 |

---

## 2. 字段契约

### 2.1 模板字段

| 字段 | 类型 | 合法值 | 硬/软 | 存储 |
|---|---|---|---|---|
| `hypothesis` | string | 非空、可证伪陈述 | 硬（创建时） | `event_metadata` |
| `evidence_required` | list[string] | 证据类型清单 | 软（计划，见 §4） | `event_metadata` |
| `evidence_source` | enum | `taskhub_event` / `agent_local_log` / `external_observation` / `human_observation` | **硬**（枚举合法） | `event_metadata` |
| `verdict` | enum | `confirmed` / `rejected` / `inconclusive` | **硬**（三选一，禁空） | TaskEvent `investigation_verdict` |
| `verdict_basis` | string | 非空 | **硬** | 同上 |
| `next_action` | enum | `fix` / `observe` / `close` | 软（不校验组合，见 Q1） | 同上 |

### 2.2 字段合法性规则（硬）

- **H1**：`verdict` 必须 ∈ {`confirmed`,`rejected`,`inconclusive`}，禁空。
- **H2**：`verdict_basis` 非空。
- **H3**：`evidence_source` 必须是 §2.1 的合法枚举之一。
- **H4**：`investigation` 任务的 `done` 必须存在**至少一条** `event_type=investigation_verdict` 的 TaskEvent。

### 2.3 字段合法性规则（软 · warning，不阻断）

- **S1**：`evidence_required` 未满足 → warning（见 §4）。
- **S2**：`verdict=inconclusive` → 允许；但 basis 必须说明「缺什么证据、为何当前不足以判断」，否则降级为 warning。
- **S3**：`verdict=confirmed/rejected` 而 `evidence_required` 有未满足项 → warning（**不静默通过**，也不阻断）。
- **S4**：`next_action` 与 `verdict` **不做组合校验**（Q1 解耦）。

### 2.4 `evidence_source` 的反幻觉约束

- **H5**（软性但写入契约）：若一条 verdict 的 basis **只引 `taskhub_event`**，则它**不能独立裁定 "Hub 自身缺陷"**——因 Hub 故障时其 event 本身不可信。此类 verdict 应至少辅以 `agent_local_log` 或 `external_observation`。
- 实现上 H5 作为 **warning**（不阻断），但 spec 明确其语义。

---

## 3. 状态约束契约

### 3.1 不侵入原则

- **不动 `TaskState`**（queued/claimed/running/retrying/completed/failed/cancelled/blocked_failed）。
- **不动 `TaskStage`**（brainstorming/design/planning/ready/implementing/review/done）。
- **不新增 stage / state**。

### 3.2 调查语义 → 复用现有 stage

| 调查语义 | 复用 stage | 既有门控 |
|---|---|---|
| 提出 hypothesis | brainstorming / design | LIFECYCLE_GATE（requirement/spec） |
| 采集 evidence | implementing | changelog 门控 |
| 出 verdict + 完成 | done | **本 spec §4 追加的 verdict 门控** |
| （可选）提前提示 | review | **warning 提示，不阻断**（Q3=B） |

### 3.3 协议完整性约束

- **C1**（Q2=A）：`task_kind=investigation` **不可静默改回 `normal`**。变更必须 `force=true`，并写入一条 TaskEvent 留痕（`event_type=kind_changed`，metadata 记 from/to/reason/actor）。
- **C5**：`investigation → normal` 后，`done` 门控即降为普通任务（无 verdict 要求）——**这正是必须 force+留痕的原因**。

---

## 4. 完成门控契约

### 4.1 挂载点（唯一：done）

在 `_apply_stage_requirements`（task_stages.py）中，**当且仅当** `task_kind==investigation ∧ dst==done` 时，追加 verdict 校验。普通任务路径**逐字节不变**。

### 4.2 门控行为矩阵

| 条件 | 行为 |
|---|---|
| 无 `investigation_verdict` 事件 | **422 阻断**（H4） |
| `verdict` 非法/空 | **422 阻断**（H1） |
| `verdict_basis` 空 | **422 阻断**（H2） |
| `evidence_source` 非法枚举 | **422 阻断**（H3） |
| `evidence_required` 未满足 | warning |
| `verdict=inconclusive` | 允许；basis 缺"为何不足"说明 → warning |
| `verdict=confirmed/rejected` 且证据缺 | warning（不阻断） |
| `next_action` 任意组合 | 放行（Q1 解耦） |

> **核心**：阻断只发生在「裁定不合法」（H1–H4）；「裁定为 inconclusive」或「证据不全」一律 warning，不阻断。

### 4.3 review 阶段前置提示（Q3=B）

- 在 `review` stage：若 investigation 且尚无合法 verdict → 返回 **warning 列表**，**不抛 422**。
- 提示内容：还缺哪些硬字段（H1–H4），供 agent 在到 done 前补齐。
- **不新增状态机分支**，仅在门控检查函数里以 `dst==review` 返回 warning。

### 4.4 force 语义

- `force=true` 可绕过阻断，但**留痕**（沿用现有 force 留痕机制）。
- force **不改** investigation 的 kind，也不自动补 verdict。

---

## 5. 明确不做（Non-Goals）

| 不做 | 依据 |
|---|---|
| ❌ observe 环 / 二次采证 / 循环生命周期 | Q5=A |
| ❌ 新 Task 列 / 新表 / 新迁移 | R284 锁定 |
| ❌ 新状态 / 新 stage / 改转移函数 | R284 锁定 |
| ❌ 新常驻进程 / 新工作流引擎 | R284 锁定 |
| ❌ 默认三轮 / 多角色评审 | R284 锁定 |
| ❌ 自动生成下一任务 | R284 锁定 |
| ❌ 自动形成 Insight | "Evidence→Insight 不建" |
| ❌ `evidence_required` 强校验阻断 | Q4=B |
| ❌ `next_action` 与 verdict 组合约束 | Q1=A |

---

## 6. 与既有系统的兼容核对

| 既有机制 | 兼容性 |
|---|---|
| `Task.task_kind: TaskKind` | 加枚举值 `investigation`，无迁移风险（列已存在） |
| `TaskEvent` 只追加日志 | verdict/kind_changed 均为 TaskEvent，零 schema 变更 |
| `STAGE_ARTIFACT_REQUIREMENTS["done"].accepts_text` | 复用；investigation 分支叠加校验 |
| LIFECYCLE_GATE | 复用，不替代 |
| force 留痕机制 | 复用 |
| R282（A6 纯观测） | 无冲突：本协议不改 reaper/timeout/门控语义（只在 done 按 kind 加条件分支） |

---

## 7. 验收（spec 层，供后续实现任务引用）

| # | 验收项 | 方式 |
|---|---|---|
| A1 | 普通任务 done 门控**行为不变** | 回归测试 + diff 审查 |
| A2 | investigation 无 verdict → done 阻断 | 单测 |
| A3 | verdict 非法/空 → 阻断；合法三值 → 放行 | 单测 |
| A4 | `inconclusive` + basis 缺说明 → warning 不阻断 | 单测 |
| A5 | `evidence_required` 未满足 → warning 不阻断 | 单测 |
| A6 | review 阶段 → warning 不阻断（Q3=B） | 单测 |
| A7 | `investigation → normal` 无 force → 阻断 | 单测 |
| A8 | 无新表/新状态/新进程（红线） | schema diff = 空；进程清单无新增 |

---

## 8. 下一步

本 spec 通过后，实施应**分阶段、每阶段不改状态机**：
1. 枚举值 `investigation`（纯数据）
2. done 条件门控分支 + review warning（Q3=B）
3. kind 变更 force+留痕（Q2=A）
4. 测试（A1–A8）

R284 · spec-only，未实现。
