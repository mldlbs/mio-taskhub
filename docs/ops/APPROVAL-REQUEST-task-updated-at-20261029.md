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
| 端到端流程 | `scripts/migrate_task_updated_at_flow.py`（授权/预检/失败回滚） |
| 单元测试 | `tests/test_migrate_task_updated_at.py`（3 项） |
| 流程测试 | `tests/test_migrate_flow.py`（4 项） |
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

## 6 授权（自动化）

审批不再以人工签字为默认门禁，使用 `scripts/migration_approval.py` 服务端流程：

1. 创建审批任务并置于 `review` 阶段；
2. 服务端读取目标库、重新计算内容哈希与代码 commit；
3. 写入 `approve` 记录，artifacts 包含 `scope`、`db_path`、`db_content_hash`、`code_commit`、`expires_at` 和 HMAC `signature`；
4. 迁移流程校验签名、有效期、哈希与 commit；任一不符即中止。

客户端无法只靠声明 artifacts 通过校验；旧审批或换库复用都会失败。

合法转移校验已闭环：

- 创建接口拒绝 `stage=review`（`gate.review_requires_transition`）；
- 审批服务要求 `review_started_at` 非空，且存在 `stage_changed -> review` 事件；
- 同一任务重复审批被拒绝；
- 过期、伪造签名、错误哈希均拒绝。

审批通过前：生产库不变、`trial` 不变、新 exe 不部署。
