# 素材库（Material Library）设计规格（Spec）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-10-28 15:10 |
| 状态 | draft |
| 负责人 | opencode |
| 任务 | 素材面板重构 |
| 上游依据 | `requirement.md`（FR-1 ~ FR-14、NFR-1 ~ NFR-6） |
| 下游关联 | `data-model.md`（表结构）、`plan.md`（分阶段） |

---

## 1 模块职责与边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| `materials/adapters/*` | 从外部文件、taskhub 表、日志读取原始记录；产出 `RawRecord` | 不写数据库、不判断质量、不做去重 |
| `materials/extract.py` | 纯函数：`RawRecord → Draft[]`；切句、分类、去样板；复用 `task_lessons.py` 的规则 | 不读文件、不连数据库、不调 LLM |
| `materials/quality.py` | 硬闸校验 + 四维评分 + 评分拆解 | 不改变素材正文，不决定生命周期以外的状态 |
| `materials/sanitize.py` | 等长脱敏、敏感信息扫描、`sanitizer_version` | 不保留原始快照 |
| `materials/pipeline.py` | 采集 → 落 raw → 提炼 → 归并 → 打分 → 提交；游标与事务 | 不提供 HTTP，不直接改任务状态 |
| `materials/api.py` | `/api/v1/materials` 读写接口、来源健康、重建、导出 | 不内联长任务；重建只入队 |
| `materials/jobs.py` | 后台线程、按来源调度、故障隔离、锁与重试 | 不阻塞核心任务接口，不持有长事务 |
| `MaterialPanelView.jsx` | 素材列表、筛选、详情、出处跳转、质量拆解 | 不提供洞察/选题/creativity/价值发现入口 |

边界总原则：

1. **证据层与产品层分离**：素材库只收可核验原文；洞察、选题、创意、价值发现是产品，不进入素材表。
2. **素材不可改写**：正文一旦入库就是只读；规则升级只新增素材，不修改已存在的 quote。
3. **引用优先于整洁**：已引用的素材不自动归档、不自动换正文；宁可多一条，不可断一条证据链。
4. **fail-open**：采集与重建的任何失败都只影响该来源，不改变任务状态、不阻塞 hub。

---

## 2 输入 / 输出

### 2.1 适配器输出：`RawRecord`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `source_key` | str | 是 | 必须已在 `material_source` 登记 |
| `external_id` | str | 是 | 源内稳定标识：HN item id / run id / doc path / discussion id / 日志签名；不包含内容 hash，内容变化用 `source_snapshot_hash` 区分 |
| `observed_at` | datetime | 是 | 事件真实发生时间；未知时用采集时间并标记 `observed_at_estimated` |
| `payload` | dict | 是 | 原始记录的最小完整快照，序列化后 ≤256KB；进入 pipeline 后脱敏并落 raw |
| `source_snapshot_hash` | str | 是 | 原始内容 hash（文件字节 / DB 字段值 / 规范化 JSON），用于 raw revision 判定 |
| `source_ref` | dict | 是 | 人类可跳转的出处：`{url}` / `{path,line,offset}` / `{table,pk,field}` |
| `project` | str | 否 | 任务/文档/run 所属项目 |
| `occurrence_hint` | int | 否 | 该 raw 内同一内容的出现次数（日志用） |

### 2.2 提炼输出：`Draft`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | enum | 是 | signal / fact / result / problem / decision / lesson |
| `quote_exact` | str | 是 | 从 `sanitized_payload` 中截取的**逐字**片段 |
| `locator` | dict | 是 | 相对 `sanitized_payload` 的定位；见 3.3 |
| `title` | str | 否 | 人读短标题，可为源标题 |
| `url` | str | 否 | 外部链接 |
| `scope_key` | str | 否 | 项目键；缺省为 `project or ''` |
| `occurrence_count` | int | 否 | 该 quote 在 raw 内的重复次数，默认 1 |

### 2.3 对外读模型：`MaterialView`

| 字段 | 说明 |
|---|---|
| `id` / `kind` / `origin` | 主键、分类两轴 |
| `quote` | 主 witness 的逐字原文 |
| `title` / `url` / `project` | 展示与过滤 |
| `observed_at` / `last_seen_at` / `expires_at` | 时间语义 |
| `status` / `quality` / `quality_detail` | 生命周期与评分拆解 |
| `occurrences` / `corroboration` | 计数语义 |
| `primary_witness` | 主出处（locator + source_ref + raw revision + sanitizer version） |
| `witnesses` | 全部出处，详情接口返回 |
| `extract_history` | 该素材的提取历史，详情接口返回 |
| `links` | 引用关系，详情接口返回 |

---

## 3 详细设计

### 3.1 流水线与事务边界

```
1 登记   source 必须在 material_source 且 mode != off
2 采集   adapter 读源，产出 RawRecord[]；此阶段不持有数据库会话
3 落 raw 脱敏 → source_snapshot_hash / sanitized_payload_hash / config hash → 计算 raw_revision → INSERT OR IGNORE (source_key, external_id, raw_revision)
4 提炼   extract(raw) → Draft[]；纯函数；失败只影响该 raw
5 硬闸   provenance / verbatim / kind / length / 非 LLM / scope / 版本一致
6 归并   content_fingerprint + scope_key + kind → 命中则加 witness，否则新建 material
7 打分   evidence / freshness / authority / density → quality + 初始 status
8 提交   同一事务内写 material_extract、更新 material_source.cursor/last_run_at/last_status
```

事务规则：

| 规则 | 内容 |
|---|---|
| 读写在事务外 | 文件、HTTP、DB 查询原始记录全部在会话外完成；进事务只做写入与必要的唯一键查询 |
| 批大小 | 每批 ≤200 条 raw，或累计耗时 ≤2 秒，先到者提交 |
| 游标绑定 | `cursor` 与 raw、material、witness、extract 在**同一事务**提交；失败则整体回滚，游标不前移 |
| 重试 | `database is locked` 退避 0.2 / 0.5 / 1.0 秒重试 3 次；仍失败则放弃本批，保留游标 |
| 幂等 | raw 版本键、material 唯一键、witness 唯一键、extract 唯一键四层保证至少一次投递不产生重复 |
| 隔离 | 每个 source 独立 try/except；失败写 `last_error`，其他 source 继续 |

### 3.2 证据模型：raw ↔ material 多对多

`material_raw` 是来源快照，`material` 是证据单元，二者不是一对多，而是**多对多**：

- 一个 run 的 result 可以提炼出多条素材：测试结果、失败原因、文件布局、已知坑。
- 同一条原文可能来自多个 raw：HN 重复转载、同一结论出现在评审与讨论里。

因此：

| 表 | 角色 | 关键点 |
|---|---|---|
| `material_raw` | 版本化来源快照 | `(source_key, external_id, raw_revision)` 唯一；payload 不可变 |
| `material` | 证据单元 | `(kind, scope_key, content_fingerprint)` 唯一；quote 不可变 |
| `material_witness` | 出处边 | `(material_id, raw_id)` 唯一；每条边有自己的 `locator`、`quote_exact`、`occurrence_count`、`independence_group` 快照与 `current_extract_id` |
| `material_extract` | 提取历史（追加式） | 每个通过硬闸的 Draft 一行；`(material_id, raw_id, extract_version)` 唯一；不更新、不删除 |
| `material_link` | 引用边 | 指向任务/想法/文档/run/讨论 |

规则：

1. **主出处**：第一个成功入库的 witness 为 `is_primary=1`，用部分唯一索引保证一条素材只有一个主出处。
2. **主出处不可覆盖**：后续 witness 只增加计数与佐证，不改变 `material.quote`、不改变主 locator。确需更换时走人工运维接口并留痕。
3. **逐字校验按 witness 做**：每个 witness 存自己的 `quote_exact`；合并只要求规范化后指纹相同，不要求字节相同。`material.quote` 等于主 witness 的 `quote_exact`。
4. **一条素材至少一个 witness**：没有 witness 的 material 不允许存在（应用层不变量 + 定期审计）。
5. **witness 绑定不可变 raw revision**：witness 冗余 `sanitizer_version` 与 `sanitized_payload_hash`，校验时必须与所指向 raw 的同名字段一致；旧 witness 永远按旧 revision 回读，不随新版本漂移。
6. **提取历史不覆盖**：同一 raw 在新版本下重新提炼时，写入新的 `material_extract` 行，并把 witness 的 `current_extract_id` 指向新行；旧提取行保留，供审计与回滚。
7. **佐证按独立组计数**：witness 固化当时的 `independence_group`；`corroboration` 统计不同组，不统计不同 `source_key`，也不统计重复采集。

### 3.3 脱敏与三级校验对象

| 对象 | 是否落库 | 用途 | 校验基准 |
|---|---|---|---|
| `source_snapshot` | 否 | 采集瞬间的原始字节，仅内存中存在 | 只计算 `source_snapshot_hash`，不持久化 |
| `sanitized_payload` | 是 | 脱敏后的稳定快照 | `locator`、`quote_exact` 的逐字校验基准 |
| `quote` / `quote_exact` | 是 | 展示与引用 | 必须是 `sanitized_payload` 的精确子串 |

#### 版本化快照

raw 是不可变的**版本化快照**，同一来源记录可以有多个 revision：

| 字段 | 含义 |
|---|---|
| `source_snapshot_hash` | 原始源内容的稳定标识（文件字节 / DB 字段值 / 规范化 JSON） |
| `sanitized_payload_hash` | 脱敏输出的内容标识 |
| `sanitizer_version` | 脱敏算法版本 |
| `sanitizer_config_hash` | 脱敏规则代码 + 配置的 hash；同一算法版本下规则或配置不同也算不同输出 |
| `raw_revision` | 同一 `(source_key, external_id)` 的快照序号，从 1 递增 |

约束：

- `material_raw` 永不原地修改 payload；源内容变化、脱敏算法升级或脱敏配置变化 → 新增 raw revision。
- 唯一键：`(source_key, external_id, raw_revision)`，以及 `(source_key, external_id, source_snapshot_hash, sanitizer_version, sanitizer_config_hash)`。
- witness 固定指向一个 `raw_id`，并冗余 `sanitizer_version`、`sanitized_payload_hash`；校验时必须与 raw 同名字段一致。
- 旧 witness 永远能按旧 revision 的 payload 回读，即使新版本已经产生；不存 `source_snapshot`，只存它的 hash。
- `raw_revision` 分配：先按 `(source_key, external_id, source_snapshot_hash, sanitizer_version, sanitizer_config_hash)` 查重，命中直接复用；未命中才取当前最大值 + 1，唯一冲突则回退查询。重复采集不产生新 revision。
- `source_snapshot_hash` 的粒度由适配器定义：外部条目=条目 JSON；文档=文件内容；run=result 字段；日志=错误块文本（不是整个日志文件）。

脱敏规则：

- 采用**等长掩码**：命中的密钥值逐字符替换为 `*`，保留换行；字符数不变，因此字符偏移与行号在原始内容与脱敏内容之间一致。
- 覆盖：`sk-*`、`ghp_*`、`github_pat_*`、`Bearer <token>`、`api_key/token/password/secret = value`、AWS `AKIA*`、私钥块、`scheme://user:pass@host`。
- 脱敏发生在写 raw 之前；`quote_exact` 从脱敏后的 payload 截取，再执行一次幂等脱敏。
- `material_raw` 记录 `sanitizer_version` 与 `sanitizer_config_hash`；升级脱敏规则时重新采集源并写入**新的 raw revision**，不修改旧 revision；源不可回读时旧 revision 保持原样，并在来源健康中告警。
- 入库后异步复扫：对 `sanitized_payload` 与 `quote` 跑同一组正则，命中即把素材置 `quarantine` 并告警。

定位语义（统一为 JSON 字段定位）：

```json
{
  "v": 1,
  "type": "json",
  "raw_revision": 3,
  "field": "content",
  "char_start": 0,
  "char_end": 68,
  "line": null,
  "sanitizer_version": 1
}
```

- 所有 raw 的 `payload` 都是 JSON；`field` 指向 payload 内的字符串字段（`content` / `result` / `text` / `block` / `conclusions`）。
- `char_start/char_end` 是**该字段值内**的 **Unicode 码点索引**（Python `str` 索引），不是 UTF-8 字节偏移，也不是 UTF-16 code unit 索引。
- `line` 可选，仅当 payload 内保留原始行号时有值；换行未被掩码改变。
- `raw_revision` 必须与 witness 指向的 `raw_id` 对应同一行；跨 revision 的偏移不可复用。
- `source_ref` 另存人类可跳转位置：URL、文件路径+行号、`table+pk+field`。文件轮转后 `source_ref` 可能失效，但 canonical `locator` 始终可回读 raw。

### 3.4 指纹与跨项目合并

| 概念 | 定义 | 用途 |
|---|---|---|
| `content_fingerprint` | `sha256(normalize(quote_exact))`；normalize = NFKC + 压缩空白 + 连续 `*` 掩码折叠为 `<mask>` + 去首尾空白，保留大小写 | 判断“是否同一段文字”；脱敏升级不拆分同一证据 |
| `scope_key` | 默认 `project or ''`；`signal` 固定为 `''`（全局） | 限定合并范围 |
| 素材唯一键 | `(kind, scope_key, content_fingerprint)` | 素材级去重 |
| 来源唯一键 | `(source_key, external_id, raw_revision)` | raw 快照版本去重 |
| witness 唯一键 | `(material_id, raw_id)` | 重放幂等 |

跨项目策略：

- `signal` 是全局知识，跨项目合并为一条，witness 记录各自出处。
- `fact/result/problem/decision/lesson` **默认不跨项目合并**：同一句话在两个项目里可能是不同约束，合并会丢失上下文。需要共享时由人工把 `scope_key` 改为 `''` 并留痕。
- 同一段文字即使 kind 不同也不合并：它作为“结果”和作为“教训”是两种证据角色。

重复出现时：

- 只新增 witness、累加 `occurrences`、更新 `last_seen_at`。
- **不覆盖** `material.quote`、主 witness、`observed_at`（首次观察时间）。
- 近重复（同 URL 不同标题、改写转载）v1 不做语义合并，留到校准阶段评估。

### 3.5 occurrences 与 corroboration

| 字段 | 定义 | 维护方式 |
|---|---|---|
| `material_witness.occurrence_count` | 该 quote 在**这一条 raw 内**出现的次数 | 日志按签名聚合；其他来源默认 1 |
| `material.occurrences` | 按 `(source_key, external_id)` 去重后的观察次数 | 每个来源记录取 `MAX(occurrence_count)`，再求和；脱敏升级产生的新 raw revision 不算新观察 |
| `material.corroboration` | 不同 `independence_group` 的数量 | `COUNT(DISTINCT witness.independence_group)`，重复采集、同源镜像、重放都不增加 |

独立性分组：

- `material_source.independence_group` 默认等于 `key`；HN、GitHub、文档、run、日志各自一组。
- 如果两个来源是同一内容的镜像（例如某个聚合站转载 HN），配置为同一组，只算 1 个独立佐证。
- witness 在写入时固化 `independence_group` 与 `independence_version`；之后修改来源分组**不回溯**已存在 witness，也不重算历史评分。
- 分组调整必须写入 `material_source_audit`（old / new / actor / reason / at）；需要重算历史分数时，走显式重算任务并升级 `quality_model_version`。

硬约束：

1. 同一个 raw 重放 100 次：raw 被 `INSERT OR IGNORE` 忽略 → witness 不新增 → 两个计数都不变。
2. 同一来源的 100 条不同 raw（例如同一错误在 100 个时间点被记录）：occurrences=100，corroboration=1。
3. 两个不同独立组各 1 条：occurrences=2，corroboration=2；两个镜像源同组各 1 条：occurrences=2，corroboration=1。
4. `authority` 只受 corroboration 影响，**occurrences 不增加权威性**。
5. 首次写入时 `occurrences` 等于首个来源记录的 `occurrence_count`（不是默认 1）；后续按 `(source_key, external_id)` 去重后取最大值再求和。同一 witness 重放时 `occurrence_count` 取本次提炼值，保证确定性。
6. 同一来源记录因脱敏升级产生新的 raw revision 时，只新增 witness 指针，不增加 occurrences；旧 witness 仍指向旧 revision。

### 3.6 质量模型与校准

`active` 只表示该素材**可被检索与引用**，不表示事实已被证实。证伪由引用方（任务、评审、人工）通过 `refutes` 关系完成；素材库本身不宣称真假。

#### 硬闸（任一失败即拒收，写入隔离区并记录原因）

| 编号 | 检查 | 说明 |
|---|---|---|
| HG-1 | 出处可定位 | `locator` 能解析到 raw 的具体字段/偏移 |
| HG-2 | 原文可回读 | 能从 `sanitized_payload` 取出定位片段 |
| HG-3 | 逐字命中 | `quote_exact` 是该片段的精确子串；校验使用与 witness 相同 `raw_revision`、`sanitizer_version`、`sanitized_payload_hash` 的 payload |
| HG-4 | kind 合法 | 6 类枚举之一 |
| HG-5 | 长度合法 | 8 ≤ 字符数 ≤ 400 |
| HG-6 | 非 LLM 产品 | `source_key` 不在 insights / topics / creativity / value-discovery 列表 |
| HG-7 | scope 合法 | `scope_key` 与 kind 的全局/项目规则一致 |
| HG-8 | 版本一致 | witness 的 `sanitizer_version` / `sanitized_payload_hash` 与所指向 raw 一致；`locator.raw_revision` 与 raw 一致 |

#### 评分（0..1）

| 维度 | 权重 | 计算 |
|---|---|---|
| evidence | 0.35 | A：URL + locator = 1.0；B：文件路径 + 行号 + locator = 0.95；C：表 + 主键 + 字段 + locator = 0.90。没有可回读 locator 的来源不进入评分（硬闸拒收） |
| freshness | 0.20 | `exp(-age_days / half_life)`；half_life：signal 14d、problem 30d、result 90d、fact 365d、decision/lesson 3650d |
| authority | 0.25 | 基础：internal 1.0 / task 0.9 / log 0.8 / external 0.5；每多一个独立组 +0.15，上限 1.0 |
| density | 0.20 | 20~160 字 = 1.0；161~240 = 0.8；241~400 = 0.6；8~19 = 0.4；命中样板 = 0；超过 2 个句末标点再 -0.2（下限 0.2） |

说明：

- `evidence` 的硬闸保证“能核验”，评分只区分出处类型，不区分真假。
- `density` 是**规则指标**，只识别长度、样板、句数，不宣称理解语义；“单主题”由切句规则近似。
- `freshness` 用 `last_seen_at` 作为时间锚：重复被观察到的素材仍然新鲜；首次观察时间另存 `observed_at`。
- `quality_model_version` 随每条素材保存；改权重不回溯已入库素材，需显式重算任务。
- 新 witness 或新提取版本可以触发该素材的 quality 重算；**单纯修改 `independence_group` 不重算历史素材**，除非显式运行重算任务并升级 `quality_model_version`。

#### 阈值（初始值，P4 校准）

| 条件 | 处理 |
|---|---|
| 硬闸失败 | 拒收：raw 标 `rejected`，写隔离区 + 原因，不建 material |
| quality < 0.40 | `quarantine`（原因 `low_quality`），保留可审计行 |
| 0.40 ≤ quality < 0.65 | `quarantine` |
| quality ≥ 0.65 且 source.mode=active | 候选 `active`；硬闸必须全过 |

`trial` 来源**永不自动 active**，即使 quality 达到 0.65；只有人工把 `mode` 切到 `active` 且通过 P6 闸门后才可能产生 active 素材。

阈值与权重通过 `MIO_MATERIALS_QUALITY` 指向的 JSON 覆盖；默认值写在 `quality.py`。P4 校准规则：

- 总样本 200 条，按实际存在的 `kind × origin` 组合分层；活跃组合（素材数 ≥50）目标 ≥20 条，其余目标 ≥10 条；达不到的格子标“证据不足”。
- 分别报告：分类准确率、误收率、漏收率、重复合并正确率。
- 样本不足的格子明确标“证据不足”，不得外推为整体结论。
- 校准报告写入 `docs/taskhub/materials/calibration-YYYYMMDD.md`，与 `quality_model_version` 一起冻结。
- 未达到 AC-7 / AC-8 / AC-9 不进入 P5。

### 3.7 重建与回滚

版本化：

- 每个 source 有 `extract_version`（规则版本）与 `extract_config_hash`（规则代码 + 配置的 hash）；任一变化但版本没变 → 重建拒绝启动（`config_version_mismatch`）。
- 每条 raw 记录 `extract_version`、`extract_config_hash`、`extract_status`（pending / extracted / rejected）。
- 每次提炼中**通过硬闸**的每个 Draft 写入 append-only 的 `material_extract`；唯一键 `(material_id, raw_id, extract_version)` 保证重放不重复写。被硬闸拒绝的 Draft 写隔离区 JSONL。
- 同一唯一键重复写入时，若 `draft_fingerprint` 不同，视为提炼非确定性：拒绝写入、标 raw `rejected` 并告警，不静默覆盖。
- witness 只持 `current_extract_id` 指向当前版本；material 记录 `first_extract_version` / `last_extract_version`；历史提取行永不删除。

重建流程：

```
POST /materials/sources/{key}/rebuild {to_version, to_config_hash}
1 获取来源锁：lock_owner=rebuild:<uuid>, lock_heartbeat_at=now, lock_until=now+10min
2 从 rebuild_cursor 开始，按 200 条 raw 分批
3 每批一个事务：
   a 必要时重新脱敏 → 新 raw revision（旧 revision 不动）
   b 计算指纹并 upsert material（首次建行）
   c 写 material_extract（material_id 已存在）+ witness（更新 current_extract_id）
   d 更新 raw 提炼状态、rebuild_cursor、lock_heartbeat_at
   e commit
4 收尾：处理本来源在 to_version 之前产生、但本次未再产出的 material
5 释放锁，记录 rebuild_state=done / failed
```

素材不可变原则在重建中的体现：

- 即使规则改变导致同一 raw 产生不同 quote，也**不修改**已有 material，而是新增一条；旧 material 进入孤儿处理。
- 如果新提炼结果与已有 material 指纹相同，新增一条 `material_extract` 记录并把 `current_extract_id` 指向它；不修改正文与主出处，旧提取记录保留。
- 孤儿 material：无 `material_link` → `archived`（原因 `stale_extract`）；有 `material_link` → 保持原状态并置 `needs_review=1`，绝不自动归档已引用证据。
- 归档 material 的 `material_extract` 历史与 `material_link` 永久可查；状态归档不触发删除。
- 因此“引用不丢失”是结构性保证，不依赖重建逻辑正确。

回滚与恢复：

- 单批失败：事务回滚，`rebuild_cursor` 停在上一个成功批次，`last_error` 记录，`rebuild_state=failed`。
- 重启恢复：启动或调度时扫描 `rebuild_state=running` 且 `lock_until < now` 的来源，从 `rebuild_cursor` 续跑；锁未过期则等待或人工释放。锁由每批的 `lock_heartbeat_at` 续期。
- 全局失败：释放锁；已提交批次保留（它们本身是完整、幂等的）；重跑从游标继续，不重复计数。
- **原子性边界**：只保证**批次原子性**，不保证整个重建任务的全局原子性；已提交批次不会因后续失败而回滚。
- 灾难回滚：停止 job → 恢复小时级 DB 快照 → 或 `DROP TABLE material_*`；旧业务表不受影响。

### 3.8 生命周期状态机

| 当前 | 事件 | 下一状态 | 副作用 |
|---|---|---|---|
| （新建） | 硬闸通过且 quality ≥0.65 且 source.mode=active | `active` | 写 expires_at |
| （新建） | quality 0.40~0.65，或 source.mode=trial | `quarantine` | 记录原因 |
| （新建） | quality <0.40 | `quarantine` | 原因 `low_quality`，保留审计行 |
| （新建） | 硬闸失败 | 拒收 | 只写隔离区，不建 material |
| `quarantine` | 人工提升 / 校准任务 | `active` | 留痕 operator |
| `active` | TTL 到期 | `stale` | 不删除，不参与默认列表 |
| `active` | 引用方标记 refutes 且人工确认 | `refuted` | 保留正文与出处 |
| `stale` | 新 witness 到达 | `active` | 刷新 last_seen_at |
| `active` / `stale` / `refuted` | 人工归档 | `archived` | 保留全部数据 |
| 任意 | 非法转移 | 拒绝 | API 返回 409 |

TTL：signal 30 天、problem 180 天、result 180 天、fact 365 天、decision / lesson 不设过期。

`expires_at` 以 `last_seen_at` 为锚计算，而不是首次 `observed_at`：新 witness 到达时顺延过期时间，并允许 `stale` 回到 `active`。`observed_at` 始终保留首次观察时间。

`trial` 来源没有自动进入 `active` 的路径：即使硬闸全过且 quality ≥0.65，也只能停在 `quarantine`，直到人工把 `mode` 切到 `active`。

### 3.9 SQLite 并发与迁移

现状核查（2026-10-28）：

| 项 | 现状 |
|---|---|
| 日志模式 | WAL |
| busy_timeout | 5000ms |
| synchronous | NORMAL |
| 连接池 | QueuePool，pool_size=5，max_overflow=10 |
| 建表 | `SQLModel.metadata.create_all` + `migrations.py` 幂等迁移 |
| 备份 | 小时级快照，保留 31 份 |
| 进程 | 单进程多线程后台任务 |

设计约束：

1. **短事务**：每批 ≤200 行 / ≤2 秒；进事务前完成所有文件与网络读取。
2. **只写素材表**：采集与重建只写 `material_*`，不触碰 `task`、`run`、`event` 等热表。
3. **锁冲突处理**：3 次退避重试；仍失败则本批放弃、游标保留、下一轮继续，不抛到 hub。
4. **线程模型**：沿用 `BackgroundWorker` + `register_thread`；每 30 秒检查一次哪些 source 到期。
5. **迁移**：新表由 `create_all` 创建；复合唯一索引与部分索引在 `migrations.py` 用 `CREATE ... IF NOT EXISTS` 兜底。v1 不做删列、改类型等破坏性变更。
6. **模型导入**：`materials/models.py` 必须在 `init_db()` 调用 `create_all` 之前被导入，否则新表不会被创建。
7. **部署前备份**：首次启用前手动触发一次快照；失败则回滚为“停 job + 恢复快照”。

### 3.10 采集调度与来源隔离

| source | mode 默认 | 间隔 | 日上限 | 备注 |
|---|---|---|---|---|
| `hn` | active | 3600s | 300 | 读 observer observations |
| `github-trending` | active | 3600s | 300 | 同上 |
| `doc.requirement` | active | 600s | 500 | 扫已批准需求文档的 FR-n |
| `doc.review` | active | 600s | 500 | 读 review_result |
| `discussion.closed` | active | 600s | 300 | 读 conclusions |
| `run.result` | active | 120s | 500 | exit_code=0 且含测试/构建证据 |
| `run.failure` | active | 120s | 500 | exit_code≠0 的错误块 |
| `history.root_cause` | active | 300s | 300 | historyevent.root_cause |
| `hub.console` | trial | 300s | 200 | 只入隔离区 |
| `observer.serve` | trial | 300s | 200 | 只入隔离区 |

每个 source 有 `independence_group`，默认等于 `key`；镜像源配成同一组。分组调整写入 `material_source_audit`，并只在后续 witness 生效，不回溯历史。

`external_id` 规则（决定 raw 级幂等，以及“文档改动是否算新记录”）：

| source | `external_id` |
|---|---|
| `hn` | HN item id |
| `github-trending` | `repo_full_name@YYYY-MM-DD` |
| `doc.requirement` | `path` |
| `doc.review` | `task_id` |
| `discussion.closed` | `discussion_id` |
| `run.result` / `run.failure` | `run_id`（同一 run 只落一条 raw） |
| `history.root_cause` | `event_id` |
| `hub.console` / `observer.serve` | `<file>#<signature>`（签名稳定，`occurrence_count` 累加） |

内容变化由 `source_snapshot_hash` 识别，并产生新的 raw revision；旧 revision 与旧素材保持不可变。run 与 event 本身是不可变记录。

隔离规则：

- 每个 source 单独 try/except；异常写 `last_error`，不抛出到调度线程。
- 日上限按来源独立计数，达到上限后跳过本轮。
- `mode=off` 完全不读源；`mode=trial` 只写 quarantine；`mode=active` 才可能产生 active。
- 来源连续失败 3 次在面板显示红色状态，但不自动停用（由人决定）。

### 3.11 日志试运行（P6）

目的：在不让日志污染主库的前提下，验证签名归一、脱敏与噪声水平。

规则：

- 只读日志尾部 5MB 或最近 200 个错误块，取小者。
- 只收 `ERROR` / `Traceback` / `Exception` / `CRITICAL`；排除 HTTP 200 访问日志与心跳行。
- 签名归一：去掉时间戳、数字、UUID、十六进制、绝对路径、端口，再 hash；同一签名聚为一条 witness，`occurrence_count` 记出现次数。
- trial 期间所有素材强制 `quarantine`，不进入 active，不参与默认列表。

激活闸门（全部满足才允许 `mode=active`）：

| 指标 | 阈值 | 方法 |
|---|---|---|
| 密钥扫描 | 0 命中 | 用脱敏同一组正则复扫 |
| 误合并率 | ≤2% | 人工抽查 100 个签名 |
| 噪声比 | ≤30% | 人工判定无信息量的签名占比 |
| 日唯一签名数 | ≤50 | 连续 7 天统计 |

未达标则保持 trial，只保留隔离样本，不激活、不删除。

### 3.12 API 与面板

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/materials` | 列表：kind / origin / project / status / q / cursor |
| GET | `/api/v1/materials/{id}` | 详情：quote、全部 witness、extract 历史、links、质量拆解 |
| POST | `/api/v1/materials/{id}/link` | 新增引用 |
| DELETE | `/api/v1/materials/{id}/link/{link_id}` | 删除引用 |
| POST | `/api/v1/materials/{id}/status` | 人工状态转移（提升/归档/证伪） |
| GET | `/api/v1/materials/sources` | 来源健康：模式、最后运行、错误、产出/拒收/隔离计数 |
| PATCH | `/api/v1/materials/sources/{key}` | 调整 `mode` / `independence_group` / 上限；必须带 reason，写 `material_source_audit` |
| POST | `/api/v1/materials/sources/{key}/run` | 手动采集一次 |
| POST | `/api/v1/materials/sources/{key}/rebuild` | 版本化重建 |
| GET | `/api/v1/materials/stats` | 总量、按 kind/origin/status、引用率、过期率 |

引用约束：只允许引用 `active` / `stale` 素材；`quarantine` / `refuted` / `archived` 返回 409。

兼容：旧 `/api/v1/mio/material-panel` 保留，不删除、不改返回结构；新面板不调用它。

面板：

- 顶部：origin 切换（全部/外部/内部/任务/日志）+ 来源健康入口。
- 筛选：kind chips、项目、状态、搜索框（LIKE，v1 不上 FTS5）。
- 列表：原文片段（最多两行）、kind、出处、时间、质量、引用数、状态。
- 详情抽屉：完整 quote、原文上下文、主出处与全部 witness、引用列表、质量四维拆解、状态操作。
- 移除：洞察、选题、creativity、价值发现的全部区块与按钮；旧数据仍可通过旧 API 读取。

---

## 4 异常与边界情况

| 场景 | 处理 |
|---|---|
| 数据库锁冲突 | 退避重试 3 次；仍失败放弃本批，游标保留，记 `last_error` |
| 源文件缺失 / 被删除 | 本批失败；不断言文件永久消失；连续 3 次在面板告警 |
| 日志轮转 / 截断 | 检测 size < cursor.offset → 重置 offset，按签名去重避免重复 |
| 单条 raw 提炼异常 | 该 raw 标 `extract_status=rejected`，记原因；继续处理同批其他 raw |
| 脱敏失败 | 拒收该 raw，不落库；记 `sanitizer_error` |
| locator 回读失败 | 硬闸失败，拒收；不猜测、不修正 quote |
| quote 超长 | 拒绝，不截断；截断会破坏逐字语义 |
| quote 重复 | 合并为 witness；不覆盖主出处 |
| 重建中断 | 已提交批次有效；`rebuild_cursor` 续跑；锁在 10 分钟后自动过期 |
| 时区 | 统一存 UTC ISO8601；面板按本地时区展示 |
| 编码 | 文件按 UTF-8 读，失败回退 GBK，再失败用 errors=replace；记录 `encoding_used` |
| 超大 payload | 截断到 256KB 并记 `payload_truncated=1`；quote 超出截断区则拒收 |
| quote 含密钥 | 等长掩码后入库；复扫命中则隔离并告警 |
| 同一 raw 提炼出 0 条 | 正常，raw 标 `extracted`，不建 material |

---

## 5 验收指标

| 编号 | 指标 | 目标 | 验证方式 |
|---|---|---|---|
| AC-1 | 重放幂等 | 同一 raw 重放 3 次，material / occurrences / corroboration 不变 | 自动化用例 |
| AC-2 | 逐字核验 | active 素材 locator 回读命中率 100% | 全量审计脚本 |
| AC-3 | 引用保持 | 重建后 linked material 数量不减少、quote 不变 | 重建前后快照对比 |
| AC-4 | 计数语义 | 同源 100 次 → occurrences=100、corroboration=1 | 构造样本 |
| AC-5 | 故障隔离 | 注入单源异常，其他源正常；核心 API P95 回归 ≤10% | 故障注入 + 压测 |
| AC-6 | 规则回滚 | 失败批次回滚，游标不前移，数据与重建前一致 | 事务用例 |
| AC-7 | 分类准确率 | 200 条标注样本 kind 准确率 ≥90% | P4 人工标注 |
| AC-8 | 误收 / 漏收 | 误收 ≤5%，漏收 ≤15% | P4 人工标注 |
| AC-9 | 重复合并 | 重复对合并正确率 ≥95% | P4 人工标注 |
| AC-10 | 日志安全 | 密钥命中 0、误合并 ≤2%、噪声 ≤30% | P6 试运行 |
| AC-11 | 镜像佐证 | 同组两源同文：corroboration=1；改分组不回溯历史评分；审计有记录 | 构造样本 + 审计 |
| AC-12 | 旧 witness 可回读 | sanitizer 升级后，旧 witness 仍按旧 raw revision 逐字命中 | 构造 v1/v2 快照 |
| AC-13 | 提取历史 | 同一 raw 两版本输出不同：两条 extract、两条 material、旧 link 保留、无重复 | 双版本黄金样本 |
| AC-14 | 重建崩溃恢复 | 杀进程后重启，从最后成功批次续跑；计数与引用不变 | 故障注入 |

---

## 6 分阶段交付

| 阶段 | 内容 | 出口条件 |
|---|---|---|
| P1 证据模型 | 7 张表、版本化 raw、提取历史、独立性分组、locator、硬闸、幂等写入 | AC-1 / AC-2 / AC-4 / AC-11 / AC-12 通过 |
| P2 确定性提炼 | 适配器提炼函数、黄金样本、双版本回归 | 重放输出逐字节一致；AC-13 通过 |
| P3 核心来源 | HN、GitHub、文档、评审、讨论、run、history 接入 | 30 天回填完成，来源健康可见 |
| P4 质量校准 | 200 条标注、阈值与权重校准、冻结模型版本 | AC-7 / AC-8 / AC-9 达标 |
| P5 API 与面板 | 新接口 + 新面板，旧 API 保留 | 面板可用，旧区域移除 |
| P6 日志试运行 | trial 采集、脱敏、签名去重、隔离观察 | AC-10 达标后人工激活 |

详细任务拆分见 `plan.md`。

---

## 7 待校准项

以下数值是工程初值，不是实验结论，P4 必须用标注样本校准后再冻结：

| 项 | 初值 | 校准方法 |
|---|---|---|
| active 阈值 | 0.65 | 误收率 / 漏收率曲线 |
| 拒收阈值 | 0.40 | 同上 |
| evidence 权重 | 0.35 | 消融对比 |
| authority 权重 | 0.25 | 消融对比 |
| density 分档 | 20/160/240/400 | 人工密度标注 |
| 日志激活阈值 | 误合并 2%、噪声 30% | 100 签名抽查 |
| 跨项目合并 | 默认不合并 | 观察是否出现真实共享需求 |
| 独立性分组映射 | 每个来源默认独立组 | 发现镜像时合并，写审计 |
| P4 标注样本 | 200 条，按 kind × origin 分层 | 活跃格 ≥20，其余 ≥10；不足标“证据不足” |

---

## 8 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-10-28 | 初稿：吸收 6 项评审修正（指纹、一对多、脱敏、重建、计数、并发） |
| v1.1 | 2026-10-28 | 按终审意见补：版本化 raw、镜像独立性、提取历史、active 语义、分层校准、崩溃恢复与批次原子性 |
