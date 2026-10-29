# 生产迁移执行结果：`task.updated_at`（2026-10-29）

> **验收状态：通过（编号 22，2026-10-29）。**
> 编号 21 执行记录。**迁移已执行**，以下为真实生产证据。
> 审计证据副本：`docs/ops/evidence/prod-migration-20261029/`（含 manifest 与三份 DB 备份的 sha256）。

## 1 执行窗口

| 项 | 值 |
|---|---|
| 目标库 | `C:\Users\admin\.mio_taskhub\taskhub.db` |
| 执行代码 | `86d00bd`（干净工作区） |
| 审批任务 | `37e540b9`（`review`，含 `review_started_at` 与 `stage_changed` 事件） |
| 审批绑定 | 内容哈希 `9c7fcb66…` + commit `86d00bd` + scope `task.updated_at` |
| 停机 | TaskHub 进程全部停止；`48620` 无监听；迁移后重启 |
| 独立备份 | `taskhub-pre-updated-at-20261029T064703Z.db`（sha256 `4d5b9063…`） |
| 迁移前备份 | `taskhub-pre-retry-20261029T064903Z.db`（sha256 `881c4baa…`） |
| 迁移后快照 | `taskhub-migrated-20261029T065341Z.db`（sha256 `65e6bf5b…`） |

## 2 执行过程（含一次失败与回滚）

1. 第一次执行：审批写入成功，但流程在 `preflight/db_identity` 中止。
   原因：内容哈希包含 `taskreview` 表，而审批记录本身就写在该表，导致审批前后哈希不一致。**未写库**。
2. 修复：内容哈希排除 `taskreview`；增加 `code_clean` 预检，脏工作区禁止执行。提交 `86d00bd`。
3. 第二次执行前：反向迁移（删触发器/列）恢复，行数保持 1,943，`integrity_check=ok`；删除旧审批后重新授权。
4. 干净代码重新执行：`verified`，无失败项；应用 ORM 读取通过；服务重启后 API 200。

这次失败是“哈希口径错误”，不是迁移逻辑错误；已回滚并重新执行，最终执行绑定到 `86d00bd`。

## 3 迁移后验收

| 检查 | 结果 |
|---|---|
| `updated_at` 列 | 存在 |
| `task` 行数 | 1,943（迁移前 1,943，含审批任务；未丢行） |
| `updated_at` NULL | **0** |
| 触发器 | `task_updated_at_after_insert` / `task_updated_at_after_update` 存在 |
| `integrity_check` | `ok` |
| 新代码 ORM | 1,943 行，NULL=0 |
| API | `GET /api/v1/tasks` → HTTP 200 |
| 素材库 | 2,351 active / 38 quarantine，未变 |
| 其他表 | 迁移前后非 task 表哈希一致（流程校验） |
| 回滚路径 | 反向迁移已验证；灾备备份可用 |

## 4 未捆绑的变更

以下均未执行：

- 素材生产回填；
- 阈值调整或冻结；
- 日志来源切换（仍 `trial`）；
- 新 exe 部署；
- 第二层效用实验。

## 5 结论

`task.updated_at` 迁移完成，旧 exe 继续通过触发器兼容；新代码 ORM 可读。生产服务已恢复。

剩余独立事项：分支保护 required checks 的实际配置仍需有权限的 token 核实；它不阻塞本次数据库迁移，但必须阻止受保护代码的正式发布。

## 6 关闭记录

| 项 | 结果 |
|---|---|
| 验收 | 编号 22 通过：完整性、兼容性、授权追溯、变更隔离 |
| 最终记录 | 本文件 |
| 审计链 | 首次失败（未写库）→ 修复 → 反向迁移 → 重试成功；日志见 `docs/ops/evidence/prod-migration-20261029/` |
| 备份保留 | 迁移前、重试前、迁移后三份 DB 备份，路径与 sha256 见 manifest |
| 未捆绑 | 素材回填、阈值、日志、新 exe、第二层实验 |
| 分支保护 | 独立治理事项，不追溯阻塞本迁移 |
