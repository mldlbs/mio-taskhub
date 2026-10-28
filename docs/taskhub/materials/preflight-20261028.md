# 生产回填前只读预检与演练报告

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 生成时间 | 2026-10-28 17:45 |
| 范围 | 素材库 7 张表的 30 天回填；不含新 exe 部署 |
| 生产写入 | **已于 2026-10-28 18:58 执行**（证据见 `evidence/20261028/`） |
| 结论 | 回填通过；新 exe 仍阻塞；阈值未冻结 |

> 执行更新（2026-10-28 19:05）：第 1~8 节是回填前预检快照。生产回填已按门禁执行，退出码 0、审计两次 `ok: true`、业务表 7 张哈希前后一致。执行日志与差异见 `evidence/20261028/README.md`。

---

## 1 只读预检结果

| 检查 | 结果 |
|---|---|
| 数据库 | `C:\Users\admin\.mio_taskhub\taskhub.db` |
| 大小 / WAL / SHM | 19,382,272 B / 4,136,512 B / 32,768 B |
| journal_mode | `wal` |
| `PRAGMA quick_check` | `ok` |
| 磁盘剩余 | 约 56.3 GB |
| 现有表 | 33 张，**无 `material_*`** |
| `task` 列数 | 57；**没有 `updated_at`** |
| 运行中的旧 exe | PID 4308 / 6856，`dist\mio-taskhub-v3\TaskHub.exe` |
| 最近备份 | `taskhub_20261028_092343.db`，19,402,752 B，`quick_check=ok` |
| 备份策略 | 小时级，保留 31 份 |

业务表基线（预检时）：

| 表 | 行数 | sha256 前 12 位 |
|---|---|---|
| task | 1,941 | `61d1ded5d1fa` |
| run | 5,207 | `bca295e22835` |
| event | 21,090 | `651cc43d72a6` |
| historyevent | 99 | `014dd9896d41` |
| discussion | 67 | `77226fc8202f` |
| tasktemplate | 8 | `7db3e8ecbaf4` |
| scheduledjob | 3 | `59f4a4ca29ac` |

---

## 2 旧 exe 与新表 / 新代码的相互影响

| 问题 | 结论 |
|---|---|
| 新表是否影响旧 exe 的查询 | **不会**：旧 exe 的模型与 SQL 不引用 `material_*`；新表没有触发器、没有外键指向业务表 |
| 新表是否影响旧 exe 的写入 | **不会**：回填只写 `material_*`；业务表哈希在演练前后一致 |
| 新索引是否落在业务表 | **不会**：索引只在 7 张新表上 |
| `db.init_db()` 是否会跑既有迁移 | **会**；因此回填脚本已改为 `init_materials_db()`，只 `create_all` 7 张新表，不执行 `migrations.py` |
| 新后台线程是否进入旧进程 | **不会**：旧 exe 不含该代码；`MIO_MATERIALS_AUTOSTART` 只影响新 exe |
| 新 exe 能否直接启动 | **不能**：当前工作区 `models.py` 已新增 `task.updated_at`，生产库没有该列；需先由该改动补迁移。这是既有未提交改动，不是素材库引入 |

---

## 3 写入边界与演练证据

演练方式：用 SQLite backup API 复制生产库到副本，在副本上执行 **两遍** 30 天回填。

| 证据 | 结果 |
|---|---|
| 副本 | `C:\Users\admin\.mio_taskhub\preflight\rehearsal_20261028_094107.db` |
| 副本 sha256 | `d17c1e6f83dfe11f...` |
| 业务表哈希前后 | **全部一致**（task/run/event/historyevent/discussion/tasktemplate/scheduledjob） |
| 第一遍 | raw_new=8,656；accepted=8,742；rejected=4；materials_new=2,387；merged=6,355 |
| 第二遍 | raw_new=0；accepted=0；rejected=0；materials_new=0；merged=0 |
| 素材表行数（两遍后） | source 10 / raw 8,656 / material 2,387 / witness 8,742 / extract 8,742 / link 0 |
| 审计 | `scripts/materials_audit.py` → `ok: true` |

结论：在副本上，**只写新表、业务表零变化、重复执行零新增**。

---

## 4 来源回填明细（副本）

| 来源 | records | new raw | accepted | new material | merged |
|---|---|---|---|---|---|
| hn | 3,374 | 3,374 | 3,360 | 1,958 | 1,402 |
| github-trending | 112 | 112 | 112 | 98 | 14 |
| doc.requirement（30 天内） | 8 | 8 | 81 | 81 | 0 |
| doc.review | 48 | 48 | 48 | 48 | 0 |
| discussion.closed | 14 | 14 | 26 | 26 | 0 |
| run.result | 139 | 139 | 154 | 150 | 4 |
| run.failure | 4,960 | 4,960 | 4,960 | 25 | 4,935 |
| history.root_cause | 1 | 1 | 1 | 1 | 0 |

状态：HN active 1,927 / quarantine 31；GitHub active 91 / quarantine 7；其余 active。

---

## 5 质量与隔离

| 检查 | 结果 |
|---|---|
| trial 来源 | 日志两个来源默认 `trial`，P3 没有日志适配器，不会产生 active |
| 硬闸失败 | 写 `~/.mio_taskhub/materials/quarantine/*.jsonl`，raw 标 `rejected` |
| 低分素材 | 留在 `quarantine`，可审计 |
| 阈值 | `quality_model_version=1`，未校准；P4 之前不宣称可靠 |
| 演练拒绝量 | 4 条（短文本/无有效 Draft） |

---

## 6 幂等、事务与恢复

| 场景 | 结果 |
|---|---|
| 同一回填第二遍 | raw / material / witness / extract 零新增 |
| 单批失败 | 事务回滚；游标不前移；重跑去重 |
| 锁冲突 | `ingest_records` 退避 0/0.2/0.5/1.0 秒重试；仍失败则本批放弃，可重跑 |
| 重建崩溃 | P1 的 T-CRASH 用例已覆盖；批次原子，不是全局原子 |
| 旧 hub 并发写 | SQLite WAL 单写者；回填批次 200 行、短事务；建议在 hub 空闲时执行 |

---

## 7 回滚与备份

| 项 | 内容 |
|---|---|
| 写入前备份 | 已有小时级快照；执行前再手动触发一次并校验 `quick_check` |
| 清理边界 | 只删 7 张新表，按依赖顺序：link → witness → extract → material → raw → source_audit → source |
| 业务表 | 不在回滚范围内，演练证明未被修改 |
| 恢复 | 若出现异常，停回填 → 恢复快照 → 重启旧 exe |

---

## 8 P4 样本准备

已从演练副本生成 200 条分层样本，**未填标签**：

| 文件 | 内容 |
|---|---|
| `docs/taskhub/materials/calibration/sample-20261028.jsonl` | 200 条，含 material_id / kind / origin / status / quality / quote / source_key |
| `docs/taskhub/materials/calibration/manifest-20261028.json` | 分层数量、抽样种子、副本 sha256、证据不足层 |

分层结果：

| 层 | eligible | sampled | 证据 |
|---|---|---|---|
| signal \| external | 2,056 | 52 | 充足 |
| decision \| internal | 136 | 52 | 充足 |
| result \| task | 150 | 51 | 充足 |
| problem \| task | 26 | 26 | 充足 |
| problem \| internal | 19 | 19 | **不足（<20）** |
| 其余 19 个组合 | 0 | 0 | **证据不足** |

标注字段为空：`label_kind`、`label_usable`、`label_duplicate_of`、`label_notes`。P4 不得用本文件直接冻结阈值。

---

## 9 结论

| 项 | 裁定 |
|---|---|
| 素材表回填 | **有条件通过**：必须使用 `init_materials_db()`、执行前备份、hub 空闲时运行 |
| 业务表写入 | 不需要，演练证明零变化 |
| 新 exe 部署 | **阻塞**：`task.updated_at` 缺列，需先补迁移 |
| 生产写入 | **已执行 2026-10-28 18:58**；详见 §11 |
| P4 | 样本已准备，阈值未校准 |

---

## 10 证据文件

| 文件 | 用途 |
|---|---|
| `scripts/materials_preflight.py` | 只读预检 |
| `scripts/materials_rehearsal.py` | 副本双跑演练 |
| `scripts/materials_sample.py` | P4 分层抽样 |
| `scripts/materials_backfill.py` | 生产回填（已于 18:58 执行） |
| `scripts/materials_audit.py` | 不变量审计 |
| `docs/taskhub/materials/calibration/` | 200 条样本与 manifest |
| `docs/taskhub/materials/evidence/20261028/` | 生产回填日志、审计、差异 |

---

## 11 生产执行结果（2026-10-28）

| 项 | 结果 |
|---|---|
| 退出码 | 0 |
| 审计 | 两次 `ok: true`，文件一致 |
| 业务表 | 7 张行数与 sha256 前后一致 |
| 素材表 | source 10 / raw 8,658 / material 2,389 / witness 8,744 / extract 8,744 |
| 重复 / 孤儿 | 全部 0 |
| 状态 | active 2,351 / quarantine 38 |
| 旧 exe | 回填后重启，board `ready=12 done=92` |
| 新 exe | 未部署 |
| 阈值 | 未冻结 |

完整证据：`docs/taskhub/materials/evidence/20261028/README.md`。
