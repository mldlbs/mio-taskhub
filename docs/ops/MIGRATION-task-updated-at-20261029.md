# 生产部署阻塞：`task.updated_at` 独立迁移方案（2026-10-29）

> 编号 14 第 3 项。与素材质量门禁完全独立；本轮只在副本上演练，**未触碰生产库**。

## 1 问题

- 新代码 `mio_taskhub/models.py` 的 `Task` 增加 `updated_at`，`workflow/transitions.py` 在状态迁移与领取时写入。
- 生产库 `task` 表 1,941 行，**没有 `updated_at` 列**；新 exe 启动后 ORM 查询会报 `no such column`。
- 旧 exe 仍在运行，迁移期间可能继续插入/更新 task。

## 2 方案

采用“加列 + 回填 + 双触发器”，保持旧 exe 兼容：

```sql
alter table task add column updated_at datetime;

update task
   set updated_at = coalesce(last_transition_at, created_at, current_timestamp)
 where updated_at is null;

-- 旧 exe 插入不写 updated_at
create trigger task_updated_at_after_insert
after insert on task
for each row
when new.updated_at is null
begin
  update task set updated_at = coalesce(new.last_transition_at,
                                        new.created_at, current_timestamp)
   where id = new.id;
end;

-- 旧 exe 只更新 last_transition_at
create trigger task_updated_at_after_update
after update of last_transition_at on task
for each row
when new.last_transition_at is not null
begin
  update task set updated_at = new.last_transition_at
   where id = new.id
     and (updated_at is null or updated_at != new.last_transition_at);
end;
```

选择理由：

| 方案 | 结论 |
|---|---|
| 加列 + 回填 + 触发器 | **采用**：旧 exe 不停机也不产生 NULL；回滚简单 |
| 加列 NOT NULL DEFAULT | SQLite 不允许非常量默认值；全表同一时间戳不可接受 |
| 12 步重建表 | 风险高、耗时长，1,941 行没有必要 |

新增列可空；新代码始终写入，触发器兜底旧代码。`updated_at` 不参与索引与业务查询（当前只有 transitions 写入，无 API 读取）。

## 3 工具

`scripts/migrate_task_updated_at.py`：

```bash
# 只读检查
python scripts/migrate_task_updated_at.py --db <db> --verify

# 备份 + 迁移（备份默认与 db 同目录，也可 --backup 指定）
python scripts/migrate_task_updated_at.py --db <db> --apply --backup <backup.db>

# 回滚（覆盖 db，必须显式 --yes）
python scripts/migrate_task_updated_at.py --db <db> --rollback <backup.db> --yes
```

`--apply` 自带：sqlite backup → `integrity_check` → 加列 → 回填 → 建触发器 → 复检（列存在、NULL=0）。

## 4 发布步骤（生产，需单独审批）

1. 公告维护窗口；停止 hub 与 worker（避免迁移时写入竞争）。
2. 备份：`sqlite3 .backup` 或脚本自动备份；记录 sha256 与 `integrity_check=ok`。
3. 迁移：`--apply`；确认输出 `ok: true`、`null_updated_at: 0`。
4. 验证：
   - `pragma table_info(task)` 含 `updated_at`；
   - `select count(*) from task where updated_at is null` = 0；
   - 两个触发器存在；
   - 新 exe 的 ORM 查询任务列表成功（副本已验证 1,941 行）；
   - 非 `task` 表哈希不变（副本 38 张表一致）。
5. 启动新 exe；冒烟：任务列表、领取、一次状态迁移、`updated_at` 随迁移更新。
6. 观察 24 小时；保留备份与迁移报告。

## 5 回滚

触发条件：新 exe 启动失败、任务列表异常、迁移后 `updated_at` 大面积为空、其他表出现非预期变化。

1. 停止新 exe。
2. `--rollback <backup> --yes` 恢复整库（比 `DROP COLUMN` 更彻底）。
3. `--verify` 应返回 `has_updated_at: false`、`task_rows: 1941`。
4. 启动旧 exe，确认任务列表与看板正常。
5. 记录事故原因；备份保留。

## 6 副本演练结果（2026-10-29）

> 完整预演（备份恢复、旧/新代码读写、重启、回滚）见 `REHEARSAL-task-updated-at-20261029.md`。

| 项 | 结果 |
|---|---|
| 副本 | `~/.mio_taskhub/preflight/migration-task-updated-at-20261029/taskhub-copy.db`（40,050,688 字节） |
| 迁移前 | 1,941 行；无 `updated_at` |
| 备份 | `taskhub-pre-updated-at.db`；`integrity_check=ok`；sha256 `3da5069b02db266345376896001980ac6dc7443e02695aeace4175418cd3c326` |
| 迁移后 | 列存在；NULL=0；两个触发器存在 |
| ORM 验证 | 新模型 `select(Task)` 返回 1,941 行，`updated_at` 可读 |
| 非 task 表 | 38 张表内容哈希完全一致 |
| 回滚演练 | 恢复备份后列消失、1,941 行保留 |
| 生产库 | **未变更** |

## 7 验收标准

- [ ] 迁移脚本单测通过（备份、回填、触发器、回滚、缺表报错）；
- [ ] 副本演练 `ok: true`、NULL=0、ORM 可读、非 task 表哈希一致；
- [ ] 回滚演练成功；
- [ ] 发布窗口、备份路径、回滚负责人明确；
- [ ] 生产执行需单独审批，不与素材门禁捆绑。
