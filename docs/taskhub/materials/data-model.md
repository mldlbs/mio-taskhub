# 素材库（Material Library）数据模型

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 最后更新 | 2026-10-28 14:58 |
| 状态 | draft |
| 负责人 | opencode |
| 上游依据 | `spec.md` §3.2 ~ §3.9 |
| 下游关联 | `plan.md` P1 |

---

## 1 实体与字段

### 1.1 `material_source`（来源登记）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `key` | TEXT | PK | `hn` / `github-trending` / `doc.requirement` / ... |
| `origin` | TEXT | NOT NULL | `external` / `internal` / `task` / `log` |
| `name` | TEXT | NOT NULL | 人读名称 |
| `adapter` | TEXT | NOT NULL | 适配器标识 |
| `mode` | TEXT | NOT NULL, DEFAULT `off` | `off` / `trial` / `active` |
| `config` | TEXT | NOT NULL, DEFAULT `{}` | JSON：路径、正则、上限等 |
| `cursor` | TEXT | NULL | 增量位点 JSON；只在事务提交时前移 |
| `rebuild_cursor` | TEXT | NULL | 重建位点，独立于采集游标 |
| `extract_version` | INT | NOT NULL, DEFAULT 1 | 当前提炼规则版本 |
| `sanitizer_version` | INT | NOT NULL, DEFAULT 1 | 当前脱敏规则版本 |
| `min_interval_s` | INT | NOT NULL, DEFAULT 600 | 最小采集间隔 |
| `daily_cap` | INT | NOT NULL, DEFAULT 300 | 每日最多处理 raw 数 |
| `lock_owner` | TEXT | NULL | 重建锁持有者 |
| `lock_until` | DATETIME | NULL | 锁过期时间；过期自动可抢占 |
| `last_run_at` | DATETIME | NULL | 最近一次运行 |
| `last_status` | TEXT | NOT NULL, DEFAULT `never` | `never` / `ok` / `partial` / `error` |
| `last_error` | TEXT | NULL | 最近错误摘要 |
| `rebuild_state` | TEXT | NOT NULL, DEFAULT `idle` | `idle` / `running` / `done` / `failed` |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

### 1.2 `material_raw`（来源快照）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `source_key` | TEXT | NOT NULL, FK → source.key | |
| `external_id` | TEXT | NOT NULL | 源内唯一键 |
| `observed_at` | DATETIME | NOT NULL | 事件发生时间 |
| `observed_at_estimated` | INT | NOT NULL, DEFAULT 0 | 时间是否由采集时间推断 |
| `fetched_at` | DATETIME | NOT NULL | 采集时间 |
| `payload` | TEXT | NOT NULL | 脱敏后的 JSON 快照，≤256KB；locator 的 `field` 必须指向其中的字符串字段 |
| `source_hash` | TEXT | NOT NULL | 原始字节 hash，不落原文 |
| `sanitized_hash` | TEXT | NOT NULL | 脱敏后 hash |
| `sanitizer_version` | INT | NOT NULL | 产生该 payload 的脱敏版本 |
| `extract_version` | INT | NOT NULL, DEFAULT 0 | 已执行的提炼版本；0 表示未提炼 |
| `extract_status` | TEXT | NOT NULL, DEFAULT `pending` | `pending` / `extracted` / `rejected` / `skipped` |
| `reject_reason` | TEXT | NULL | 拒收原因 |
| `payload_truncated` | INT | NOT NULL, DEFAULT 0 | 是否因超限截断 |
| `encoding_used` | TEXT | NULL | `utf-8` / `gbk` / `replace` |
| `needs_resanitize` | INT | NOT NULL, DEFAULT 0 | 脱敏规则升级后待处理 |
| `created_at` | DATETIME | NOT NULL | |

唯一约束：`UNIQUE(source_key, external_id)`。

### 1.3 `material`（证据单元）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | PK | `mat_<8hex>` |
| `kind` | TEXT | NOT NULL | 6 类枚举 |
| `origin` | TEXT | NOT NULL | 主 witness 的产地 |
| `scope_key` | TEXT | NOT NULL, DEFAULT `''` | `''` 为全局；否则为 project |
| `project` | TEXT | NULL | 展示用 |
| `quote` | TEXT | NOT NULL | 主 witness 的逐字原文，不可改写 |
| `content_fingerprint` | TEXT | NOT NULL | 规范化后的内容指纹 |
| `title` | TEXT | NULL | 人读短标题 |
| `url` | TEXT | NULL | 外部链接 |
| `observed_at` | DATETIME | NOT NULL | 首次观察时间 |
| `first_seen_at` | DATETIME | NOT NULL | 首次入库时间 |
| `last_seen_at` | DATETIME | NOT NULL | 最近一次 witness 时间；freshness 锚点 |
| `expires_at` | DATETIME | NULL | `last_seen_at + kind TTL`；新 witness 到达时顺延；decision/lesson 为 NULL |
| `status` | TEXT | NOT NULL | `quarantine` / `active` / `stale` / `refuted` / `archived` |
| `status_reason` | TEXT | NULL | 状态原因 |
| `quality` | REAL | NOT NULL | 0..1 |
| `quality_detail` | TEXT | NOT NULL | JSON：四维分数 + 原因 |
| `quality_model_version` | INT | NOT NULL | 评分模型版本 |
| `occurrences` | INT | NOT NULL, DEFAULT 1 | 去重后观察次数；首次写入取 witness 的 occurrence_count，后续为总和 |
| `corroboration` | INT | NOT NULL, DEFAULT 1 | 不同来源数 |
| `needs_review` | INT | NOT NULL, DEFAULT 0 | 重建后需人工确认 |
| `extract_version` | INT | NOT NULL | 产生它的提炼版本 |
| `sanitizer_version` | INT | NOT NULL | 产生它的脱敏版本 |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

唯一约束：`UNIQUE(kind, scope_key, content_fingerprint)`。

### 1.4 `material_witness`（出处边）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `material_id` | TEXT | NOT NULL, FK → material.id | |
| `raw_id` | INTEGER | NOT NULL, FK → raw.id | |
| `source_key` | TEXT | NOT NULL | 冗余，便于统计 |
| `quote_exact` | TEXT | NOT NULL | 该 raw 内的逐字片段 |
| `locator` | TEXT | NOT NULL | JSON，相对 sanitized_payload |
| `source_ref` | TEXT | NULL | JSON，人类可跳转位置 |
| `is_primary` | INT | NOT NULL, DEFAULT 0 | 主出处标记 |
| `occurrence_count` | INT | NOT NULL, DEFAULT 1 | 该 raw 内出现次数 |
| `observed_at` | DATETIME | NOT NULL | |
| `created_at` | DATETIME | NOT NULL | |

唯一约束：`UNIQUE(material_id, raw_id)`。
部分唯一索引：`CREATE UNIQUE INDEX ... ON material_witness(material_id) WHERE is_primary = 1`。

### 1.5 `material_link`（引用边）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | |
| `material_id` | TEXT | NOT NULL, FK → material.id | |
| `ref_type` | TEXT | NOT NULL | `task` / `idea` / `doc` / `run` / `discussion` |
| `ref_id` | TEXT | NOT NULL | 被引用对象主键 |
| `relation` | TEXT | NOT NULL | `supports` / `refutes` / `context` / `produced_by` |
| `note` | TEXT | NULL | 引用说明 |
| `created_by` | TEXT | NULL | agent / operator |
| `created_at` | DATETIME | NOT NULL | |

唯一约束：`UNIQUE(material_id, ref_type, ref_id, relation)`。

---

## 2 状态机

### 2.1 `material.status`

| 当前 | 事件 | 下一状态 | 触发者 | 副作用 |
|---|---|---|---|---|
| （新建） | 硬闸全过且 quality ≥ 0.65 且 source.mode=active | `active` | 系统 | 写 expires_at |
| （新建） | quality 0.40~0.65 或 source.mode=trial | `quarantine` | 系统 | 记录 status_reason |
| （新建） | quality < 0.40 | `quarantine` | 系统 | 原因 `low_quality`，保留审计行 |
| （新建） | 硬闸失败 | 拒收 | 系统 | 不建 material，写隔离区 |
| `quarantine` | 人工提升或校准任务 | `active` | operator | 留痕 |
| `active` | TTL 到期 | `stale` | 系统 | 不删除，默认列表隐藏 |
| `active` | 引用方标记 refutes 且人工确认 | `refuted` | operator | 保留正文 |
| `stale` | 新 witness 到达 | `active` | 系统 | 刷新 last_seen_at |
| `active` / `stale` / `refuted` | 人工归档 | `archived` | operator | 保留全部数据 |
| 其他 | 非法转移 | 拒绝 | 系统 | API 409 |

### 2.2 `material_raw.extract_status`

| 当前 | 事件 | 下一状态 | 说明 |
|---|---|---|---|
| `pending` | 提炼成功 | `extracted` | 写 extract_version |
| `pending` | 提炼异常 | `rejected` | 写 reject_reason |
| `pending` | 适配器判定无需提炼 | `skipped` | 记原因 |
| `rejected` | 规则修正后重建 | `pending` | 允许重试 |
| `extracted` | extract_version 升级 | `pending` | 进入重建队列 |

### 2.3 `material_source.mode` / `rebuild_state`

| 字段 | 转移 | 条件 |
|---|---|---|
| `mode` | `off` → `trial` | 人工开启 |
| `mode` | `trial` → `active` | 试运行闸门全过（P6） |
| `mode` | `active` → `trial` / `off` | 人工降级 |
| `rebuild_state` | `idle` → `running` | 获取来源锁成功 |
| `rebuild_state` | `running` → `done` | 全部批次提交 |
| `rebuild_state` | `running` → `failed` | 任一批次失败；锁释放 |

---

## 3 完整性约束

### 3.1 数据库约束

| 约束 | 位置 | 作用 |
|---|---|---|
| `UNIQUE(source_key, external_id)` | `material_raw` | 同一来源记录只落一次 |
| `UNIQUE(kind, scope_key, content_fingerprint)` | `material` | 同一证据只建一条 |
| `UNIQUE(material_id, raw_id)` | `material_witness` | 重放不重复加边 |
| `UNIQUE(material_id, ref_type, ref_id, relation)` | `material_link` | 引用幂等 |
| 部分唯一索引 `(material_id) WHERE is_primary=1` | `material_witness` | 一条素材只有一个主出处 |

说明：当前 engine 未启用 `PRAGMA foreign_keys=ON`，FK 声明是**文档性约束**；实际完整性由应用层写入顺序 + 定期审计保证。v1 不全局开启，避免影响既有表。

### 3.2 业务不变量

| 编号 | 不变量 | 审计方式 |
|---|---|---|
| I1 | 每条 material 至少一个 witness | `LEFT JOIN witness ... HAVING COUNT=0` 必须为空 |
| I2 | 每条 material 恰好一个 primary witness | 部分唯一索引 + 计数审计 |
| I3 | `material.quote = primary.quote_exact` | 逐行比对 |
| I4 | `content_fingerprint = sha256(normalize(quote))` | 抽样重算 |
| I5 | `occurrences = sum(witness.occurrence_count)` | 聚合比对 |
| I6 | `corroboration = count(distinct witness.source_key)` | 聚合比对 |
| I7 | active 素材硬闸全过 | 全量回读 locator |
| I8 | 有 link 的 material 不被自动归档 | 状态变更审计 |
| I9 | `signal.scope_key=''`；其他 kind 为 project 或 `''`（人工共享） | 规则查询 |
| I10 | 不收 LLM 产品来源 | source_key 白名单 |
| I11 | 引用只指向 `active` / `stale` 素材 | 引用 API 校验 + 审计 |

审计脚本作为 P1 交付物，可在 CI 与运维时执行。

---

## 4 迁移策略

### 4.1 建表

1. `mio_taskhub/materials/models.py` 定义 5 个 SQLModel。
2. `db.init_db()` 在 `SQLModel.metadata.create_all(engine)` 之前导入该模块，确保新表被注册。
3. 复合唯一约束与部分索引：模型内 `__table_args__` + `migrations.py` 中 `CREATE ... IF NOT EXISTS` 兜底。
4. 种子：`seed_material_sources(session)` 在表为空时插入 10 个来源，`hub.console` / `observer.serve` 默认 `mode=trial`，其余 `active`；幂等。

### 4.2 存量数据

| 数据 | 处理 |
|---|---|
| observer observations | P3 回填 28 天，作为 `hn` / `github-trending` 的 raw |
| 已批准需求文档 | P3 扫描 `FR-n`，按文件路径+行号建 witness |
| review_result / discussion / run / history | P3 按游标回填最近 180 天 |
| 旧洞察 / 选题 / creativity | 不导入；保留原文件与旧 API |
| 旧素材面板接口 | 保留，不改返回结构 |

### 4.3 回滚

| 场景 | 操作 |
|---|---|
| 单批失败 | 事务回滚，游标保留，`rebuild_state=failed` |
| 部署失败 | 停 material job → 恢复小时级快照（31 份） |
| 彻底回退 | 按依赖顺序 `DROP TABLE material_link, material_witness, material, material_raw, material_source`；旧业务表不受影响 |
| 模型版本回退 | 保留已入库素材，改回 `quality_model_version`，不回溯评分 |

### 4.4 部署前检查

| 检查 | 通过标准 |
|---|---|
| 备份可用 | 最近一次快照 < 1 小时，可读 |
| 表不存在 | 5 张表均不存在或结构一致 |
| 索引创建 | 迁移后索引数量与定义一致 |
| 种子 | 10 个 source 存在，mode 正确 |
| 只读探测 | `SELECT COUNT(*)` 5 张表均成功 |

---

## 5 索引与容量

### 5.1 索引

| 索引 | 列 | 用途 |
|---|---|---|
| `idx_material_kind_seen` | `(kind, last_seen_at DESC)` | 按类型浏览 |
| `idx_material_origin_seen` | `(origin, last_seen_at DESC)` | 按产地浏览 |
| `idx_material_status_seen` | `(status, last_seen_at DESC)` | 默认 active 列表 |
| `idx_material_scope_seen` | `(scope_key, last_seen_at DESC)` | 项目过滤 |
| `idx_material_fingerprint` | `(content_fingerprint)` | 去重与佐证 |
| `idx_material_quality` | `(quality)` | 校准抽样 |
| `idx_raw_source_status` | `(source_key, extract_status, id)` | 增量提炼/重建 |
| `idx_witness_material` | `(material_id)` | 详情 |
| `idx_witness_raw` | `(raw_id)` | 反查 |
| `idx_link_ref` | `(ref_type, ref_id)` | 任务反查素材 |
| `idx_source_mode` | `(mode)` | 调度 |

### 5.2 容量估算

| 项 | 估算 |
|---|---|
| 素材量 | 约 4,000/月，48,000/年 |
| raw 平均体积 | 约 1KB（HN 300B，run/文档 1~3KB） |
| 年增 raw | 约 50MB |
| 年增 material + witness + 索引 | 约 40MB |
| 年增合计 | 约 90MB，预算上限 100MB |
| 列表查询 | 5 万行 LIKE + 索引，P95 ≤100ms |
| 详情查询 | 主键 + 两个索引，P95 ≤20ms |

超过 150MB 时告警，优先归档 `stale` 素材的 raw payload（保留 hash 与出处），不删除 material。

---

## 6 事务边界

| 操作 | 事务范围 | 上限 |
|---|---|---|
| 采集批次 | raw + material + witness + cursor + source 状态 | 200 行 / 2 秒 |
| 重建批次 | 同上 + raw.extract_version + rebuild_cursor | 200 行 / 2 秒 |
| 新增引用 | material_link 单行 | 1 行 |
| 状态变更 | material.status + status_reason | 1 行 |
| 统计查询 | 只读，无事务 | — |

写入顺序：`material` → `material_witness` → 更新 `material` 计数 → 更新 raw → 更新 source 游标 → commit。
