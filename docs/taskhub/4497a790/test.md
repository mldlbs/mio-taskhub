# 测试验收：P1-1 区分 cancelled / failed 口径

## 文档信息

| 项 | 值 |
|----|-----|
| 任务 ID | `4497a790` |
| 测试文件 | `tests/test_p1_1_terminal_semantics.py` |
| 用例数 | 11 |
| 最后更新 | 2026-10-21 08:52 |
| 负责人 | hermes (agent) |

## 测试范围

### 覆盖范围

| 模块 | 覆盖内容 |
|------|----------|
| `mio_taskhub/api/board.py` | `/board/terminal-stats` 端点的口径正确性、字段完整性、零填充、数据完整性自检 |
| `mio_taskhub/observability/report.py` | `_collect_tasks()` 与端点口径一致；`data_integrity` 暴露 |
| `scripts/repair_cancelled_failed.py` | 还原正确性、幂等性、可回滚、dry-run 不写库 |
| `scripts/archive_spinning_tasks.py` | 归档改走 `apply_transition`（写 TaskEvent + `cancelled_at`） |
| 端点间一致性 | `/board/terminal-stats` ↔ `/observability/report` ↔ `/metrics` 三处成功率 |

### 不在范围

- 窄门禁（d468e633）的 diff 采集与 422 拦截 —— 属另一任务
- 前端 UI 回归 —— 本次无前端改动
- 性能压测 —— 端点为单条聚合 SQL，1933 行实测 < 20ms
- 多用户/并发场景 —— 单用户本地工具，无此场景

### 隔离约定

测试通过 `MIO_TASKHUB_DB` 指向临时库隔离，不触碰生产库。
所有新建任务用固定 id 前缀（`p11-*`）并在 `finally` 中清理。

## 用例清单

| 编号 | 用例 | 覆盖 | 预期结果 |
|------|------|------|----------|
| TC-1 | `test_terminal_stats_detects_cancelled_with_failure_evidence` | AC-1 | 建 `CANCELLED + failed_at` 任务 → `data_integrity.cancelled_with_failure_evidence >= 1`，`clean == false` |
| TC-2 | `test_terminal_stats_detects_cancelled_missing_timestamp` | AC-1 | 建 `CANCELLED + cancelled_at=NULL` 任务 → `cancelled_missing_timestamp >= 1`，`clean == false` |
| TC-3 | `test_success_rate_excludes_cancelled_from_denominator` | AC-5 | 9 完成 + 1 取消 → `attempted == completed + failed`；`success_rate == completed/attempted`；`cancel_rate == cancelled/terminal_total` |
| TC-4 | `test_terminal_stats_exposes_full_coverage` | AC-6 | 响应含全部 12 个必需字段；`by_state` 含 `COMPLETED`/`FAILED`/`CANCELLED`（零填充） |
| TC-5 | `test_three_endpoints_report_same_success_rate` | AC-5 | `/board/terminal-stats` 与 `/observability/report` 的 `success_rate` 误差 <1e-3；`/metrics` 有 `taskhub_task_success_rate` |
| TC-6 | `test_report_exposes_data_integrity` | AC-5 | `report.tasks.data_integrity` 含 `cancelled_with_failure_evidence` + `clean`；`attempted == by_state.COMPLETED + by_state.FAILED` |
| TC-7 | `test_repair_script_restores_cancelled_to_failed` | AC-2/AC-3 | 修复后 `state==FAILED`、`failed_at` 保留、`cancelled_at IS NULL`、label 已加、**TaskEvent 已补**且 reason 含 `P1-1` |
| TC-8 | `test_repair_script_is_idempotent` | AC-7 | 首次 `changed>=1`，第二次 `changed==0` |
| TC-9 | `test_repair_script_undo` | AC-8 | `--undo` 后 `state==CANCELLED`、label 移除 |
| TC-10 | `test_repair_script_dry_run_does_not_write` | AC-7 | dry-run 后 `state` 不变 |
| TC-11 | `test_archive_script_uses_apply_transition` | AC-4 | 归档后 `state==CANCELLED`、**`cancelled_at` 非空**、**TaskEvent 已写** |

### 追溯矩阵

| 需求 | 用例 |
|------|------|
| FR-1 历史脏数据还原 | TC-7, TC-8, TC-9, TC-10 |
| FR-2 消除绕过路径 | TC-11 |
| FR-3 唯一权威口径端点 | TC-3, TC-4 |
| FR-4 口径对齐 | TC-5, TC-6 |
| FR-5 数据完整性自检 | TC-1, TC-2, TC-6 |

## 验收标准

| 编号 | 验收项 | 判定方式 | 结果 |
|------|--------|----------|------|
| AC-1 | 历史脏数据还原 | `cancelled_with_failure_evidence` 从 1490 → 0 | ✅ 0（生产库实测） |
| AC-2 | DB 语义清晰 | 还原后 `FAILED` 全部在 `implementing` stage | ✅ 1526/1526（生产库实测） |
| AC-3 | 审计链完整 | 1490 条各有 ≥1 条 P1-1 TaskEvent | ✅ 1490（生产库实测） |
| AC-4 | 无绕过路径 | 归档脚本产出有 `cancelled_at` + TaskEvent | ✅ TC-11 通过 |
| AC-5 | 唯一权威口径 | 两端点 `success_rate` 一致 | ✅ TC-5 通过 |
| AC-6 | 口径定义可审计 | 响应含 `definitions` | ✅ TC-4 通过 |
| AC-7 | 幂等 | 重复执行 `changed == 0` | ✅ TC-8 通过 |
| AC-8 | 可回滚 | `--undo` 还原 | ✅ TC-9 通过 |
| AC-9 | 无回归 | 全量测试通过 | ✅ 见执行记录 |

## 跑测纪律

全量跑必须加 `--basetemp=<独立目录>`，否则 pytest 清理触发 safe-delete
批量删除保护，进程被杀拿不到汇总。

```bash
# 专项
MIO_TASKHUB_DB=<tmp>.db .venv/Scripts/python.exe -m pytest \
  tests/test_p1_1_terminal_semantics.py -q --basetemp=<独立目录>

# 回归（相关模块）
.venv/Scripts/python.exe -m pytest tests/test_metrics.py tests/test_board_summary.py \
  tests/test_archive_spinning.py tests/test_first_start_stats.py tests/test_status.py \
  tests/test_projection.py -q --basetemp=<独立目录>
```
