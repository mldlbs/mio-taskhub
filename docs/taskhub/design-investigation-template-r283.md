# Investigation 模板设计评审（R283 · design-only）

> 状态：**设计评审，不落码、不加表、不改现有 Task 状态机。**
> 定位：**Investigation 是 TaskHub 的「证据约束执行模板」，不是第二套工作流引擎。**
> 本文回答 R283 要求的三项：**① 字段 ② 状态约束 ③ 完成门控。**

---

## 0. 一句话结论

`investigation` 不新增大任务类型、不新增引擎，而是复用**已存在的三个挂载点**：

| 需要的东西 | 已存在的挂载点（本仓库实测） | 结论 |
|---|---|---|
| "这是调查任务" | `Task.task_kind: TaskKind`（models.py:142，现 `normal/change_tracking/idea_review`） | **加一个枚举值 `investigation`**，零表变更 |
| hypothesis/verdict/basis | `TaskEvent.event_metadata: JSON`（models.py:189，只追加） | **verdict 作为一条 TaskEvent**，零 schema 变更 |
| 完成门控 | `STAGE_ARTIFACT_REQUIREMENTS["done"]`（task_stages.py:52，已 `accepts_text`） | **在 done 门控上按 kind 条件加一条校验** |

→ 三处都是**已有结构的扩展**，没有一处需要新表 / 新进程 / 新状态机。

---

## 1. 字段设计（评审项 ①）

### 1.1 模板字段（最小集，R283 已定）

| 字段 | 语义 | 存储位置 | 必填时机 |
|---|---|---|---|
| `hypothesis` | 当前要验证的假设（一句话、可证伪） | Task 描述或 `event_metadata` | 任务创建/进入调查时 |
| `evidence_required` | 必须采集什么证据（清单） | `event_metadata` | 同上 |
| `evidence_source` | 证据来源频道 | `event_metadata` | 采集时 |
| `verdict` | `confirmed` / `rejected` / `inconclusive` | **一条 TaskEvent** | 完成时 |
| `verdict_basis` | 裁定依据（引用证据 + 推理） | 同 verdict 事件 | 完成时 |
| `next_action` | `fix` / `observe` / `close` | 同 verdict 事件 | 完成时 |

### 1.2 `evidence_source`（R283 强调的防幻觉字段）

必须显式声明来源，**防止"TaskHub 自己证明 TaskHub 自己"的闭环**：

```
evidence_source ∈ {
  taskhub_event,        # 来自 Hub event 表（含 reaper_decision 等）
  agent_local_log,      # 来自执行侧本地 jsonl（如 lifecycle_probe）
  external_observation, # 外部系统/网络/OS 层观测
  human_observation,    # 人工判断
}
```

**评审要点**：`taskhub_event` 与 `agent_local_log` **必须能互相独立**。
若一条 verdict 的 basis 只引 `taskhub_event`、无任何 `agent_local_log`/`external`，则它**不能独立裁定"Hub 自身缺陷"**——因为 Hub 故障时 Hub event 本身不可信。这与 A6「本地 jsonl 是真相源」同源。

### 1.3 字段落在哪：**不新增 Task 列**（关键约束）

`hypothesis / evidence_required / evidence_source` 走 `TaskEvent.event_metadata`，`verdict / verdict_basis / next_action` 走**一条 `event_type=investigation_verdict` 的 TaskEvent**。

**理由**（架构边界）：
- 一旦把它们加成 Task 的独立列，就等于把"调查语义"焊进通用任务模型 → 退化为第二套引擎。
- 走 TaskEvent 则是**可追加、可回放、可裁决后归档**——与现有 M1 事件日志一致。
- 零迁移、零 schema 风险。

---

## 2. 状态约束（评审项 ②）

### 2.1 核心原则：**不侵入 TaskState / TaskStage**

```
普通任务：Task → Execute → Evidence → Done
调查任务：Hypothesis → Evidence → Verdict → Next Action
```

`investigation` **不新增 state、不新增 stage、不改 `TaskState.can_transition` / `TaskStage.can_advance`**。
它只是在既有 `brainstorming → ... → done` 流程上，**给"完成"这一步附加一条证据约束**。

### 2.2 与现有 stage 的映射（复用，不新建）

| 调查语义 | 复用现有 stage | 约束 |
|---|---|---|
| 提出 hypothesis | `brainstorming`/`design` | 已有 requirement 门控（LIFECYCLE_GATE） |
| 采集 evidence | `implementing` | 已有 changelog 门控 |
| 出 verdict + 完成 | `done` | **新增：investigation 专属 verdict 门控（见 §3）** |

→ **零新 stage**。区别只在"完成门控对 investigation 更严"。

### 2.3 状态约束清单

| 约束 | 内容 |
|---|---|
| C1 | `investigation` 任务的 `done` **必须**有 `investigation_verdict` 事件；普通任务不需要 |
| C2 | verdict 三选一，**禁止空 verdict** |
| C3 | `verdict_basis` 非空，且**至少引一个 `evidence_source`** |
| C4 | `verdict=confirmed/rejected` 时，basis 必须引到 `evidence_required` 里列过的证据类型；`inconclusive` 可豁免但仍需写"为何不足" |
| C5 | `task_kind` 一旦设为 `investigation` **不可中途改回 normal**（避免"降级逃逸门控"）；如确需改，走 `force=true` 留痕 |

---

## 3. 完成门控（评审项 ③）

### 3.1 挂载点（复用现有门控，零新引擎）

现有 `done` 门控（task_stages.py:52）：

```python
"done": {
    "document_kinds": ["review"],
    "accepts_text": True,
    "text_field": "review_result",
    "error": "done stage requires review_result or a review document",
}
```

**扩展方式**：在 `_apply_stage_requirements` 里，当 `t.task_kind == investigation` 且 `dst == done` 时，**追加**一条 verdict 校验。普通任务走原逻辑完全不变。

伪代码（**示意，非实现**）：

```python
def _check_investigation_verdict(t, dst, force=False):
    if getattr(t, "task_kind", None) != TaskKind.INVESTIGATION:
        return []                      # 普通任务：零影响
    if getattr(dst, "value", dst) != "done":
        return []
    verdict_events = [e for e in task_events(t.id)
                      if e.event_type == "investigation_verdict"]
    if not verdict_events:
        raise HTTPException(422, {
            "message": "investigation 任务完成需要 verdict 事件",
            "gate": [{"require": "verdict", "missing": True}]})
    v = verdict_events[-1].event_metadata
    if v.get("verdict") not in ("confirmed", "rejected", "inconclusive"):
        raise HTTPException(422, {...})     # C2
    if not v.get("verdict_basis"):
        raise HTTPException(422, {...})     # C3
    return []
```

### 3.2 门控的"轻量"边界（明确不做的）

R283 明确：**不默认**做以下能力（它们是**可选**，不是 investigation 的默认行为）：

| 不默认做的事 | 理由 |
|---|---|
| ❌ 三轮评审 / 多角色评审 | 普通评审已有；investigation 不强制 |
| ❌ 棘轮（ratchet） | 只在"指标类调查"可选挂 |
| ❌ 自动生成下一任务 | 越界成工作流引擎 |
| ❌ 自动形成 Insight | 与"Evidence→Insight 不建"裁定一致 |
| ❌ 证据采集的自动触发 | investigation 只**约束**证据，不**驱动**采集 |

### 3.3 门控触发粒度

**只有 `verdict` 有明确证据要求时才允许完成** —— 即：
- 有 `evidence_required` 且 verdict ≠ inconclusive → 严格执行 C4
- `evidence_required` 为空 → 只校验 verdict/basis 非空（C2/C3），不校验来源（避免空清单自锁）

---

## 4. 架构边界（R283 的核心约束）

### 4.1 正确形态 ✅

```
Task
 ├── normal          （现状）
 ├── change_tracking （现状）
 ├── idea_review     （现状）
 └── investigation   （新增枚举值，复用事件+门控）
```

### 4.2 禁止形态 ❌（"任务自己变成流程表演"）

```
Task → Investigation Engine → Review Engine → Ratchet Engine → Decision Engine
```

### 4.3 三条不可越界的红线

1. **不新增表 / 不新增迁移**（复用 `TaskEvent.event_metadata`）
2. **不新增常驻进程**（verdict 是数据，不是运行时引擎）
3. **不改 `TaskState` / `TaskStage` 枚举与转移函数**（只在 `done` 门控加条件分支）

---

## 5. 三项评审的待决问题（需你裁定）

| # | 问题 | 选项 |
|---|---|---|
| Q1 | `investigation` 是否允许 `verdict=inconclusive` 后直接 `close`（不 fix/observe）？ | A) 允许，next_action 可与 verdict 解耦；B) inconclusive 必须配 `observe` |
| Q2 | `task_kind` 不可回退（C5）是否太硬？ | A) 硬（force 才能改）；B) 允许回退但留痕 |
| Q3 | verdict 门控是否也在 `review` stage 前置校验一次？ | A) 只在 `done`；B) review 就提示（不阻断） |
| Q4 | `evidence_source` 校验强度（C4） | A) 强校验 basis 必须匹配 evidence_required；B) 软校验（仅告警） |
| Q5 | 是否需要"investigation 完成不自动关闭，转 `observe` 环"？ | A) 不做，保持一次性；B) 引入 observe 状态（**会碰状态机，慎**） |

> **Q5 提示**：选项 B 会触碰状态机，违反 §4.3 红线。建议 A。

---

## 6. 与既有裁定的兼容性核对

| 既有裁定 | 本设计是否相容 |
|---|---|
| R280：措辞收紧，不写成已证明 | ✅ verdict 三选一（含 inconclusive），强制 basis |
| R282：A6 纯观测、不修生命周期问题 | ✅ investigation 是模板，不改 reaper/timeout/门控语义（只在 done 加条件） |
| "Evidence→Insight 不建" | ✅ 不自动生成 Insight（§3.2） |
| "TaskHub = 可验证执行与执行留痕" | ✅ verdict 是"执行留痕"的一种，不是新能力面 |
| 文档链门控（spec/api/plan approved） | ✅ investigation 复用同一套 LIFECYCLE_GATE，不替代 |

---

## 7. 下一步（等你裁定）

**不实现。** 等 Q1–Q5 裁定后，若通过，再开：
1. 一份 `spec`（investigation 模板的正式契约：字段 JSON schema + 门控行为矩阵）
2. 分阶段实施任务（枚举值 → 门控分支 → 测试），且**每一步不改状态机**。

R283
