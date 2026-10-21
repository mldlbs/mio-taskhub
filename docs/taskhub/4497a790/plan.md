# 实现计划：P1-1 区分 cancelled / failed 口径

## 改动清单

| # | 文件 | 改动 | 类型 |
|---|------|------|------|
| 1 | `scripts/repair_cancelled_failed.py` | **新建**：历史脏数据还原脚本（dry-run 默认，可 --apply，可 --undo，幂等） | 新增 |
| 2 | `scripts/archive_spinning_tasks.py` | 归档时改走 `apply_transition`，不再硬写 `t.state = CANCELLED` | 修复 |
| 3 | `mio_taskhub/api/board.py` | **新增** `GET /board/terminal-stats`：唯一权威口径 + data_integrity | 新增 |
| 4 | `mio_taskhub/observability/report.py` | `_collect_tasks()` 口径对齐（cancelled 不进 success_rate 分母）+ 暴露 data_integrity | 修复 |
| 5 | `tests/test_p1_1_terminal_semantics.py` | **新建**：11 个用例覆盖 AC-1..AC-9 | 新增 |

## 执行顺序

### Step 1 — 修复根因（先堵住再修数据）

```
scripts/archive_spinning_tasks.py
  原：t.state = TaskState.CANCELLED; t.stage = TaskStage.CANCELLED
  新：apply_transition(t, State.CANCELLED, stage, ActorType.SYSTEM, "ops:archive_spinning_tasks", reason=...)
      转移被拒 → 不改 state，只打标签 + block_reason
```

**为什么先做**：不堵住，继续跑归档脚本会再造同样的脏数据。

### Step 2 — 新建修复脚本

```
scripts/repair_cancelled_failed.py
  判定：state='CANCELLED' AND failed_at IS NOT NULL AND 'repaired:p1-1-cancelled-to-failed' NOT IN labels
  动作：state→FAILED, stage→implementing, cancelled_at→NULL, block_reason→修复原因,
        +label, +TaskEvent(failed/system)
  回滚：--undo → CANCELLED + 去掉 label
  幂等：已带 label 的跳过
```

### Step 3 — 暴露唯一权威口径

```
mio_taskhub/api/board.py
  @router.get("/terminal-stats")
    attempted = COMPLETED + FAILED
    success_rate = COMPLETED / attempted
    cancel_rate  = CANCELLED / terminal_total
    by_state 零填充全部 TaskState
    data_integrity: {cancelled_with_failure_evidence, cancelled_missing_timestamp, clean}
    definitions: 每个比率的分子分母说明
```

### Step 4 — 口径对齐 report.py

此前 `report.py` 用 `completed / total_terminal`（cancelled 进分母），
与 `metrics.py` 的 `completed / (completed + failed)` 冲突。
改为与 Step 3 一致，并暴露 `attempted` / `definitions` / `data_integrity`。

### Step 5 — 测试

```
tests/test_p1_1_terminal_semantics.py
  TC-1  data_integrity 检出「有失败证据却 CANCELLED」
  TC-2  data_integrity 检出「CANCELLED 缺 cancelled_at」
  TC-3  cancelled 不进 success_rate 分母
  TC-4  terminal-stats 暴露全量字段 + by_state 零填充
  TC-5  terminal-stats 与 observability/report 口径一致
  TC-6  report 暴露 data_integrity
  TC-7  修复脚本还原 CANCELLED→FAILED + 补 TaskEvent
  TC-8  修复脚本幂等
  TC-9  修复脚本 --undo 可回滚
  TC-10 修复脚本 dry-run 不写库
  TC-11 归档脚本走 apply_transition（cancelled_at + TaskEvent）
```

### Step 6 — 生产数据修复

```bash
# 1. 备份
python -c "import shutil,os; shutil.copy2(os.path.expanduser('~/.mio_taskhub/taskhub.db'), os.path.expanduser('~/.mio_taskhub/backups/pre-p11-repair-<ts>.db'))"

# 2. 预览
.venv/Scripts/python.exe scripts/repair_cancelled_failed.py --dry-run

# 3. 落库
.venv/Scripts/python.exe scripts/repair_cancelled_failed.py --apply

# 4. 验收
curl -s http://127.0.0.1:48620/api/v1/board/terminal-stats
```

## 风险与回退

| 风险 | 处置 |
|------|------|
| 误把合法取消当成失败还原 | 判定条件是 `failed_at IS NOT NULL` —— 只有**确实失败过**的任务才被还原；合法取消没有 failed_at，不会被误伤 |
| 还原后成功率「暴跌」 | **这是真实情况的暴露，不是新故障**。修复前 0.8364 是虚高的（分母排除了 1470 条被洗成取消的失败）。诚实口径下 0.1050，其中 1470 条是 idea 评审任务的重复失败风暴（监控工件），排除后真实交付率 0.8281 |
| 归档脚本改走状态机后行为变化 | 转移被拒时**只打标签不改 state**，比原实现更保守（原来会强行改 state 造成 state/event 不一致） |
| 生产库改坏 | 有备份 + `--undo` 可回滚 |

## 验证命令

```bash
# 专项测试
MIO_TASKHUB_DB=<tmp>.db .venv/Scripts/python.exe -m pytest tests/test_p1_1_terminal_semantics.py -q --basetemp=<独立目录>

# 回归（相关模块）
.venv/Scripts/python.exe -m pytest tests/test_metrics.py tests/test_board_summary.py \
  tests/test_archive_spinning.py tests/test_first_start_stats.py tests/test_status.py \
  tests/test_projection.py -q --basetemp=<独立目录>

# 全量
.venv/Scripts/python.exe -m pytest tests/ -q --basetemp=<独立目录>
```

> 跑测纪律：全量跑必须加 `--basetemp=<独立目录>`，否则 pytest 清理触发
> safe-delete 批量删除保护，进程被杀拿不到汇总。
