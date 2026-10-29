# `task.updated_at` 生产迁移预演报告（2026-10-29）

> 编号 15 P2。以下全部在隔离副本上执行，**生产库未写入**。

## 1 环境与备份

| 项 | 值 |
|---|---|
| 源 | `~/.mio_taskhub/taskhub.db`（只读备份） |
| 副本 | `~/.mio_taskhub/preflight/p2-migration-rehearsal-20261029/taskhub-copy.db` |
| 大小 | 40,058,880 字节 |
| 备份 | `taskhub-pre.db` |
| 备份 sha256 | `bf1100a58da33c822fb5650bb1ac5445a419b8d7d9a1e15a6d1a6788086c45b1` |
| 备份完整性 | `ok` |

## 2 迁移

| 项 | 结果 |
|---|---|
| 迁移前 | 1,941 行；无 `updated_at` |
| 迁移后 | 列存在；NULL=0；触发器 `task_updated_at_after_insert` / `_after_update` |
| 备份 | 自动生成、`integrity_check=ok` |

## 3 旧代码读写（worktree `278a447`）

| 检查 | 结果 |
|---|---|
| 旧模型 `select(Task)` | 1,941 行 |
| 旧代码插入（不写 `updated_at`） | 触发器回填 `2026-10-29 02:03:07.575410` |
| 旧代码只更新 `last_transition_at` | 触发器同步为相同时间 |
| 清理测试行 | 行数恢复 1,941 |

旧 exe 在迁移后可直接读写，不需要停机等新版本。

## 4 新代码读写（`48aacb1`）

| 检查 | 结果 |
|---|---|
| `select(Task)` | 1,941 行；NULL=0 |
| 新建任务 | `updated_at` 自动写入 |
| `apply_transition` | `updated_at == last_transition_at` |
| 清理测试行 | 行数恢复 1,941 |

## 5 重启

| 项 | 结果 |
|---|---|
| 启动 | `uvicorn mio_taskhub.main:app`，端口 48731，`MIO_TASKHUB_DB=<副本>` |
| 健康请求 | `GET /api/v1/tasks` → HTTP 200 |
| 返回 | 1,743 条任务 |
| 停止 | 进程正常退出 |

说明：应用启动会写 `scheduledjob` / `slosnapshot` 等后台表，这是应用行为，不是迁移造成。迁移本身（未启动应用）在上一轮副本中验证过：38 张非 task 表内容哈希完全一致。

## 6 完整性与回滚

| 检查 | 结果 |
|---|---|
| 迁移后 `integrity_check` | `ok` |
| 任务行数 | 1,941 |
| `updated_at` NULL | 0 |
| 回滚方式 | 恢复 `taskhub-pre.db` |
| 回滚后 | 列消失、1,941 行、`integrity_check=ok` |
| 回滚后旧代码读取 | 1,941 行 |

## 7 结论

| 项 | 结论 |
|---|---|
| 迁移脚本 | 可执行、可回滚、可验证 |
| 旧 exe 兼容 | 插入/更新均由触发器兜底 |
| 新代码兼容 | ORM 读取与迁移写入正常 |
| 重启 | 通过 |
| 生产 | **未执行**，等待单独审批（P3） |
| 新 exe | 仍不部署，属于另一项独立门禁 |

## 8 复现命令

```bash
# 备份副本
python scripts/migrate_task_updated_at.py --db <copy.db> --apply --backup <pre.db>
# 校验
python scripts/migrate_task_updated_at.py --db <copy.db> --verify
# 回滚
python scripts/migrate_task_updated_at.py --db <copy.db> --rollback <pre.db> --yes
```

旧代码路径：`git worktree add <dir> 278a447`；新代码：当前 `main`。
