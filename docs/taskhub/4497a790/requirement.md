# 需求：P1-1 区分 cancelled / failed 口径

## 文档信息

| 项 | 值 |
|----|-----|
| 任务 ID | `4497a790` |
| 需求编号 | P1-1 |
| 决策依据 | `docs/taskhub/product-decision-20261020.md` §4 前置止血第 1 条 |
| 实施计划 | `docs/taskhub/4497a790/plan.md` |
| 接口契约 | `docs/taskhub/4497a790/api.md` |
| 测试验收 | `docs/taskhub/4497a790/test.md` |

## 背景与问题

决策文档 `docs/taskhub/product-decision-20261020.md` §4「前置止血」第 1 条：

> P1-1 | 区分 `cancelled` / `failed`，停止把失败清洗为取消，API 暴露全量统计
> - 为什么先做：数据不可信，则窄门禁的证据也没人信
> - 验收：DB 语义清晰可查、API 有含失败的全量口径

窄门禁（task d468e633）的价值前提是「完成 = 有硬证据」。若失败被清洗为取消，
则成功率口径失真，门禁产出的证据无人可信，门禁形同虚设。

### 根因：`scripts/archive_spinning_tasks.py` 绕过状态机硬写 state

该脚本直接执行 `t.state = TaskState.CANCELLED`，**不经过**
`workflow/transitions.apply_transition`，导致三处后果：

1. 不写 `TaskEvent` → 审计链断裂，「谁在何时取消的」查不到
2. 不设 `cancelled_at` → 取消时间戳缺失
3. **把已有失败证据（`failed_at` 非空）的任务也洗成 CANCELLED**

### 实测数据（修复前，生产库）

| 指标 | 修复前 |
|------|--------|
| `state=CANCELLED` 且 `failed_at` 非空 | **1490** 条 |
| 其中 `task_kind=REVIEW`（idea 评审） | 1470 条 |
| 其中 `NORMAL` | 20 条 |
| `state=CANCELLED` 且 `cancelled_at` 为空 | **1550** 条 |
| 带 `archived:spinning` 标签且 CANCELLED | 1588 条 |
| 有 `to_state=CANCELLED` 的 TaskEvent | **0** 条（证实绕过状态机） |

### 口径冲突（同一「成功率」三个数）

| 位置 | 口径 | 实测值 |
|------|------|--------|
| `observability/metrics.py` | `completed / (completed + failed)` | 0.8364 |
| `observability/report.py` | `completed / (completed + failed + cancelled)` | 0.0947 |
| `api/board.py` `/overview` | 只给 `by_state` 原始计数 | 无成功率 |

→ 无法审计。窄门禁的证据链因此不可信。

## 目标与非目标

### 目标

1. **DB 语义清晰可查**：`failed` 仅表示执行失败，`cancelled` 仅表示人为/自动作废
2. **停止把失败清洗为取消**：消除所有绕过状态机写终态的路径
3. **API 暴露全量统计**：给出**唯一权威口径**，且暴露数据完整性自检

### 非目标（防过度建设）

- 不新增面板、不改前端 UI
- 不删除 `cancelled` 状态（它是独立终态，合法存在）
- 不做任务重跑、不动 `max_retries` 语义
- 不动窄门禁（d468e633）的任何代码

## 功能需求

| 编号 | 优先级 | 需求描述 | 验收标准 |
|------|--------|----------|----------|
| FR-1 | P0 | 对 `state=CANCELLED AND failed_at IS NOT NULL` 的任务，还原为 `FAILED`：`state→FAILED`、`stage→implementing`、`cancelled_at→NULL`、保留 `failed_at`、写 `block_reason`、追加 label `repaired:p1-1-cancelled-to-failed`、**补 TaskEvent**（`event_type='failed'`, `actor_type='system'`） | `cancelled_with_failure_evidence` 从 1490 → 0；1490 条各有 ≥1 条 P1-1 TaskEvent |
| FR-2 | P0 | `scripts/archive_spinning_tasks.py` 改走 `apply_transition`：合法转移写 TaskEvent + `cancelled_at` + `last_transition_at`；转移被拒时**不强改 state**，只打标签 + 留 `block_reason` | 归档脚本产出的 CANCELLED 任务有 `cancelled_at` 且有 TaskEvent |
| FR-3 | P0 | 新增 `GET /api/v1/board/terminal-stats`：`attempted=COMPLETED+FAILED`、`success_rate=COMPLETED/attempted`、`failure_rate=FAILED/attempted`、`cancel_rate=CANCELLED/terminal_total`、`by_state` 零填充全部状态、`definitions` 写明每个口径的分子分母 | 响应含全部 12 个必需字段；`by_state` 含全部已知状态 |
| FR-4 | P0 | `observability/report.py` 的 `_collect_tasks()` 口径与 FR-3 **完全一致**（此前把 cancelled 计入分母） | 两端点 `success_rate` 误差 < 1e-3 |
| FR-5 | P1 | 所有统计出口暴露 `data_integrity`：`cancelled_with_failure_evidence`、`cancelled_missing_timestamp`、`clean` | 脏数据能被自检检出；`clean` 语义明确 |

## 非功能需求

| 项 | 要求 |
|----|------|
| 性能 | 端点为单条聚合 SQL，响应 < 100ms（1933 行任务实测 < 20ms）；不引入 N+1 查询 |
| 幂等 | 修复脚本重复执行 `changed == 0`，不产生重复 TaskEvent |
| 可回滚 | 修复脚本 `--undo` 可完全还原原状态；生产改库前必须留备份 |
| 安全 | 端点在 `/api/*` 前缀下，与既有端点一致受 Bearer token 保护；不暴露任务内容，仅聚合数字 |
| 可审计 | 所有状态变更（含修复）必须留 TaskEvent，`actor_type='system'` + `actor_id` 标明来源 |
| 兼容性 | 存量 `doc_gate_exempt` 风格豁免不受影响；修复不改变任何运行时门禁语义 |

## 追溯

- TC-1..TC-11 见 `test.md`
- 实现计划见 `plan.md`
- 接口契约见 `api.md`
