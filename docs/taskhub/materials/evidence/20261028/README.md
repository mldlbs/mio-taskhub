# 生产素材回填执行记录（2026-10-28）

## 1 执行摘要

| 项 | 结果 |
|---|---|
| 操作 | 生产库 30 天素材回填（只写 `material_*`） |
| 执行人 | opencode |
| 开始 | 2026-10-28 18:58（本地） |
| 退出码 | **0** |
| 回填后审计 | `ok: true`（连续两次，结果一致） |
| 业务表 | 7 张行数与 sha256 **前后完全一致** |
| 重复 / 孤儿 | raw 0 / material 0 / witness 0 / extract 0；孤儿 material 0 / witness 0 |
| 生产写入范围 | 仅 7 张素材表 |
| 新 exe | **未部署**（仍被 `task.updated_at` 缺列阻塞） |
| 阈值 | **未冻结** |

---

## 2 门禁逐项结果

### 2.1 备份

| 项 | 值 |
|---|---|
| 备份文件 | `C:\Users\admin\.mio_taskhub\preflight\prod-backfill-20261028\taskhub-before-backfill.db` |
| 大小 | 19,435,520 B |
| sha256 | `f5cedee8047a68ef8b4bf418eb69cb15f7f39d4bc58e9c1751da79f6792487b6` |
| `quick_check` | `ok` |
| 表数 | 33 |
| 证据 | `backup.json` |

### 2.2 空闲确认

- 回填前 `run` 表无 `CLAIMED/RUNNING`，`task` 表无 `CLAIMED/RUNNING`。
- 最近事件停留在 2026-10-28 06:36 UTC（约 3 小时前）。
- 为确保业务表哈希可对比，短暂停止旧 exe：
  - 停止前：PID 4308 / 6856
  - 回填完成后重启：PID 23756 / 25148
- 重启后 `/api/v1/board/summary` 返回 `ready=12 done=92`。

### 2.3 回填

| 项 | 值 |
|---|---|
| 命令 | `python scripts/materials_backfill.py --days 30` |
| 退出码 | `0` |
| stdout | `backfill.stdout.log` |
| stderr | `backfill.stderr.log`（0 字节） |
| 入口 | `init_materials_db()`，只建 7 张素材表，不跑既有迁移 |

### 2.4 审计

| 项 | 值 |
|---|---|
| 第一次 | `audit-1.json` → `ok: true` |
| 第二次 | `audit-2.json` → `ok: true` |
| 一致性 | 两个文件 sha256 相同 |

### 2.5 差异

| 表 | 行数前/后 | 哈希 |
|---|---|---|
| task | 1,941 / 1,941 | 一致 |
| run | 5,207 / 5,207 | 一致 |
| event | 21,090 / 21,090 | 一致 |
| historyevent | 99 / 99 | 一致 |
| discussion | 67 / 67 | 一致 |
| tasktemplate | 8 / 8 | 一致 |
| scheduledjob | 3 / 3 | 一致 |

素材表：

| 表 | 行数 |
|---|---|
| material_source | 10 |
| material_raw | 8,658 |
| material | 2,389 |
| material_witness | 8,744 |
| material_extract | 8,744 |
| material_link | 0 |
| material_source_audit | 0 |

状态 / 类型 / 产地：

| 维度 | 分布 |
|---|---|
| status | active 2,351 / quarantine 38 |
| kind | signal 2,058 / result 150 / decision 136 / problem 45 |
| origin | external 2,058 / task 176 / internal 155 |

重复与孤儿检查全部为 0；审计覆盖 I1/I2/I3/I5/I6/I12/I13/I14/I16。

---

## 3 来源分布

| 来源 | raw | materials | 状态 |
|---|---|---|---|
| hn | 3,376 | 1,960 | active 1,929 / quarantine 31 |
| github-trending | 112 | 98 | active 91 / quarantine 7 |
| doc.requirement | 8 | 81 | active 81 |
| doc.review | 48 | 48 | active 48 |
| discussion.closed | 14 | 26 | active 26 |
| run.result | 139 | 150 | active 150 |
| run.failure | 4,960 | 25 | active 25 |
| history.root_cause | 1 | 1 | active 1 |
| hub.console / observer.serve | 0 | 0 | trial 未接 |

与副本演练的差异：HN raw 3,374 → 3,376（observer 当天文件在演练后又追加 2 条），因此 materials 1,958 → 1,960；其余来源一致。

---

## 4 证据文件

| 文件 | 内容 |
|---|---|
| `backup.json` | 备份路径、大小、sha256、quick_check |
| `hub-processes-before.txt` | 回填前旧 exe 进程 |
| `backfill.stdout.log` | 回填完整 stdout |
| `backfill.stderr.log` | 回填 stderr（空） |
| `backfill.exit_code.txt` | 退出码 0 |
| `audit-1.json` / `audit-2.json` | 两次审计结果 |
| `business-before.json` / `business-after.json` | 业务表行数与哈希 |
| `diff.json` | 业务表差异 + 素材计数/分布/重复检查 |

---

## 5 未执行项

| 项 | 原因 |
|---|---|
| 新 exe 部署 | 生产库缺 `task.updated_at`，需先单独补迁移 |
| 阈值冻结 | 200 条样本仍需人工标注；`problem \| internal` 等层证据不足 |
| 日志来源激活 | 仍为 `trial`，P6 处理 |

回填只证明“素材表可安全写入”，不证明所有生产故障场景安全；后续仍以审计与日志为准。
