# 技术设计：P1-1 区分 cancelled / failed 口径

## 文档信息

| 项 | 值 |
|----|-----|
| 任务 ID | `4497a790` |
| 设计版本 | v1.0 |
| 状态 | approved |
| 最后更新 | 2026-10-21 08:52 |
| 负责人 | hermes (agent) |
| 对应需求 | `docs/taskhub/4497a790/requirement.md` |
| 明确不含 | 窄门禁（d468e633）实现、前端 UI 改动、`max_retries` 语义变更、任务重跑 |

## 模块职责与边界

### 本模块负责

1. **消除绕过状态机写终态的路径** —— 所有终态变更必须经 `apply_transition`，
   保证 TaskEvent + 时间戳 + `last_transition_at` 同步落库
2. **还原历史脏数据** —— 把「有失败证据却标成取消」的任务还原为 FAILED，
   并补审计事件，让历史操作可追
3. **给出唯一权威统计口径** —— `GET /api/v1/board/terminal-stats`，
   并让 `observability/report.py` 与之对齐

### 本模块不负责

- **不改任何运行时门禁语义**：窄门禁、文档生命周期门控、ReadEvidence 门控一律不动
- **不删除 `cancelled` 状态**：它是独立终态（人为/自动作废），合法存在
- **不做数据重跑**：失败的任务不会自动重新排队
- **不引入新表/新列**：全部复用现有 `task` 表字段（`failed_at` / `cancelled_at` /
  `block_reason` / `labels`）与 `taskevent` 表

### 边界示意

```
   写入路径（修复前）                    写入路径（修复后）
   ─────────────────                    ─────────────────
   api/task_stages.py cancel_task   ✅   apply_transition → CANCELLED
   api/runs.py submit_result(fail)  ✅   apply_transition → FAILED
   background.py _on_timeout         ✅   _try_apply_event → FAILED
   ops/data_fixes.py                 ✅   显式 SQL 修复（规则 8/9）
   scripts/archive_spinning_tasks   ❌→✅ apply_transition → CANCELLED
      （直写 t.state，绕过状态机）
```

## 详细设计

### 1. 根因定位

**问题**：`scripts/archive_spinning_tasks.py` 直接执行 `t.state = TaskState.CANCELLED`，
绕过 `workflow.transitions.apply_transition`。

**后果**（生产库实测）：

| 后果 | 证据 |
|------|------|
| 审计链断裂 | 1490 条被洗的 CANCELLED 任务，`to_state='CANCELLED'` 的 TaskEvent 数为 **0** |
| 时间戳缺失 | 1550 条 CANCELLED 的 `cancelled_at` 为 NULL |
| **失败被清洗为取消** | 1490 条 CANCELLED 的 `failed_at` 非空（即它们**确实失败过**） |

**为什么这会污染口径**：`observability/metrics.py` 的 success_rate 分母是
`COMPLETED + FAILED`（cancelled 排除）。1490 条失败被移到 cancelled 后，
它们**从分母消失** —— 分母从 1705 缩到 214，成功率从 0.1050 虚高到 0.8364。

### 2. 改动设计

#### 2.1 `scripts/archive_spinning_tasks.py`（改走状态机）

**原实现**（绕过状态机）：
```python
t.state = TaskState.CANCELLED
if t.stage != TaskStage.CANCELLED:
    t.stage = TaskStage.CANCELLED
```

**新实现**：
```python
from mio_taskhub.workflow.transitions import apply_transition
from mio_taskhub.workflow.state_machine import State, Stage, ActorType
_cur_stage = t.stage if isinstance(t.stage, TaskStage) else TaskStage(t.stage)
_stage_arg = Stage.BRAINSTORMING if _cur_stage == TaskStage.CANCELLED else Stage(_cur_stage.value)
_, m1_event = apply_transition(
    t, State.CANCELLED, _stage_arg,
    ActorType.SYSTEM, "ops:archive_spinning_tasks",
    reason=f"空转任务归档[{ARCHIVE_LABEL}]",
)
if m1_event is not None:
    db.add(m1_event)
```

**失败处理**：转移被拒（如任务已是终态）时**不强改 state**，只打标签 +
写 `block_reason`。这比原实现**更保守** —— 原实现会强行改 state，
造成「state 与 TaskEvent 不一致」的新脏数据。

**特殊分支**：`TaskStage.CANCELLED` 不是合法的 `Stage` 枚举值
（`_orm_to_status_stage` 会把它映射回 BRAINSTORMING），故显式转换。

#### 2.2 `scripts/repair_cancelled_failed.py`（新建，历史数据修复）

**判定条件**（保守，只还原**有失败证据**的）：
```sql
state = 'CANCELLED' AND failed_at IS NOT NULL
AND 'repaired:p1-1-cancelled-to-failed' NOT IN labels
```

**修复动作**：

| 字段 | 原值 | 新值 | 理由 |
|------|------|------|------|
| `state` | CANCELLED | FAILED | 它确实失败过 |
| `stage` | CANCELLED | implementing | FAILED 的唯一合法 stage（见 `LEGAL_COMBOS`） |
| `cancelled_at` | 有值 | NULL | 它从未被合法取消过 |
| `failed_at` | 有值 | **保留** | 失败证据不能抹掉 |
| `block_reason` | 空 | 修复原因 | 可查 |
| `labels` | — | 追加 `repaired:p1-1-cancelled-to-failed` | 标记「这条被修过」 |
| `taskevent` | 无 | 补 `failed` / `actor_type=system` | 审计链可追 |

**为什么加 label**：这是**可逆性标记**。`--undo` 靠它精确定位被修过的任务，
也让人一眼看出「这条的成功率数据是修复后的」。

**幂等**：已带 label 的跳过 → 重复执行 `changed == 0`。

**回滚**：`--undo` → `state=CANCELLED`、`stage=CANCELLED`、去 label、
`cancelled_at` 还原为 `failed_at`。

#### 2.3 `mio_taskhub/api/board.py`（新增权威口径端点）

**新增** `GET /api/v1/board/terminal-stats`。

**核心口径**（与 `observability/report.py` 对齐）：

```
attempted     = COMPLETED + FAILED
success_rate  = COMPLETED / attempted      # cancelled 不进分母
failure_rate  = FAILED / attempted
cancel_rate   = CANCELLED / terminal_total  # 取消单独度量
```

**为什么 cancelled 不进分母**：取消是独立终态（用户主动取消 / 系统空转回收）。
计入分母会把「人为取消」误判为「执行失败」—— 这正是 0.0947 那个数的来源。

**data_integrity 自检**（P1-1 的核心产出）：

| 指标 | 含义 | 期望值 |
|------|------|--------|
| `cancelled_with_failure_evidence` | 有失败证据却标成取消的条数 | **0** |
| `cancelled_missing_timestamp` | 取消任务缺 `cancelled_at` 的条数 | 0 |
| `clean` | 两者皆 0 | true |

**by_state 零填充**：用 `TaskState` 枚举的 **name**（DB 原生，如 `COMPLETED`）
而非 `value`（小写 `completed`）—— SQLAlchemy 用 `Enum.name` 落库。

#### 2.4 `mio_taskhub/observability/report.py`（口径对齐）

**原实现**：`success_rate = completed / total_terminal`（cancelled 进分母）
**新实现**：`success_rate = completed / attempted`，与 2.3 一致。

同时新增 `attempted` / `definitions` / `data_integrity` 三个字段。

### 3. 时序：数据修复流程

```
备份 DB ──► dry-run 预览 ──► --apply 落库 ──► 查库验收 ──► 口径复核
   │            │                  │              │             │
   │            │                  ├─ state→FAILED               │
   │            │                  ├─ stage→implementing         │
   │            │                  ├─ cancelled_at→NULL          │
   │            │                  ├─ +label                     │
   │            │                  └─ +TaskEvent(failed/system)  │
   │            │                                     │
   │            └─ 只读，不写                        └─ 1490 条各 1 个事件
   └─ 可回滚
```

### 4. 涉及文件清单

| 文件 | 类型 | 改动 |
|------|------|------|
| `scripts/repair_cancelled_failed.py` | 新建 | 历史脏数据还原脚本 |
| `scripts/archive_spinning_tasks.py` | 改 | 归档改走 `apply_transition` |
| `mio_taskhub/api/board.py` | 改 | 新增 `/board/terminal-stats` |
| `mio_taskhub/observability/report.py` | 改 | `_collect_tasks()` 口径对齐 + data_integrity |
| `tests/test_p1_1_terminal_semantics.py` | 新建 | 11 个用例 |
| `docs/taskhub/4497a790/*.md` | 新建 | requirement / spec(plan) / api / test |

### 5. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 修复范围 | 只修**有失败证据**的 | 合法取消没有 `failed_at`，不会被误伤；保守优先 |
| stage 归一 | 全部 `implementing` | FAILED 的唯一合法 stage，避免产生新的非法组合 |
| 修复事件类型 | `event_type='failed'` | 语义与还原后的状态一致；`actor_type=system` 标明是系统修复 |
| 可逆性 | label 标记 + `--undo` | 生产改库必须可回滚 |
| 端点位置 | `api/board.py` 而非新模块 | 统计类端点已在 board.py（`/summary`、`/overview`），不新开模块 |
| 口径定义源 | `/board/terminal-stats` | 单一权威源，其余出口向它对齐 |

### 6. 遗留与后续

| 项 | 说明 |
|----|------|
| `cancelled_missing_timestamp = 62` | 剩余 62 条无 `cancelled_at` 的取消任务是**历史合法取消**（2026-09 前经 API 取消但时间戳未记录），非本 bug 产物。可在 P1-2 或后续单独处理 |
| 监控工件占比 | 修复后 FAILED 1526 条中，1470 条是 idea 评审任务的重复失败风暴（`archived:spinning`）、23 条是 `insight-auto` 监控工件。**排除后真实交付率 0.8281** |
| 冻结项联动 | 决策 §3 已冻结 creativity 调度与 observer 机会发现 —— 这 1470 条评审任务正是该冻结链路的产物，冻结后不应再生 |
