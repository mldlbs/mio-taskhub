# 生产迁移审批申请：`task.updated_at`（2026-10-29）

> 编号 15 P3。**本文件是申请，不是执行记录。** 审批通过前不操作生产。

## 1 申请范围

| 项 | 内容 |
|---|---|
| 变更 | 生产库 `task` 表增加 `updated_at`、回填、建两个触发器 |
| 不包含 | 新 exe 部署、素材生产重跑、阈值调整、日志来源切换 |
| 目标库 | `C:\Users\admin\.mio_taskhub\taskhub.db` |
| 影响表 | 仅 `task`；其余表不改 |
| 旧 exe | 迁移后继续可读写（触发器兜底） |

## 2 证据

| 证据 | 位置 |
|---|---|
| 迁移方案 | `docs/ops/MIGRATION-task-updated-at-20261029.md` |
| 预演报告 | `docs/ops/REHEARSAL-task-updated-at-20261029.md` |
| 迁移脚本 | `scripts/migrate_task_updated_at.py` |
| 单元测试 | `tests/test_migrate_task_updated_at.py`（3 项） |
| 旧/新代码测试 | `tests/test_task_updated_at.py`（3 项） |
| 预演副本 | `~/.mio_taskhub/preflight/p2-migration-rehearsal-20261029/` |
| 备份 sha256 | `bf1100a58da33c822fb5650bb1ac5445a419b8d7d9a1e15a6d1a6788086c45b1` |

## 3 执行前检查

- [ ] 维护窗口已公告，hub 与 worker 已停止；
- [ ] 生产库备份完成，`integrity_check=ok`，sha256 已记录；
- [ ] 备份文件在隔离位置可读、可恢复演练已通过；
- [ ] 迁移脚本版本与预演一致（commit `48aacb1` 或更新）；
- [ ] 回滚负责人、回滚命令、旧 exe 启动方式已确认；
- [ ] 迁移后验证清单已打印（列存在、NULL=0、行数 1,941、触发器、应用冒烟）。

## 4 回滚

1. 停止新进程；
2. `python scripts/migrate_task_updated_at.py --db <prod> --rollback <backup> --yes`；
3. `--verify` 应显示 `has_updated_at: false`、`task_rows: 1941`；
4. 启动旧 exe，确认看板与任务列表正常。

## 5 风险

| 风险 | 缓解 |
|---|---|
| 迁移期间旧 exe 写入 | 触发器兜底；维护窗口进一步降低竞争 |
| 备份不可用 | 预演已验证恢复；备份独立存放 |
| 迁移后列大量 NULL | 回填 + 触发器；验证 NULL=0 |
| SQLite 锁 | 维护窗口、单进程执行、重试逻辑 |
| 新 exe 部署混入本次变更 | 明确排除；新 exe 另行审批 |

## 6 审批

| 角色 | 结论 | 日期 |
|---|---|---|
| 变更申请人 | 待签 | |
| 数据/运维负责人 | 待签 | |
| 发布负责人 | 待签 | |

审批通过前：生产库不变、`trial` 不变、新 exe 不部署。
