# 素材库（Material Library）实施计划

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-10-28 15:26 |
| 状态 | approved |
| 负责人 | opencode |
| 上游依据 | `requirement.md`、`spec.md`、`data-model.md` |
| 下游关联 | `api.md`（P5 前编写）、`test.md`（P1 起持续维护） |

---

## 1 实施原则

1. **先定死，再实现**：P1 只做数据模型、唯一约束、事务边界、硬闸与审计，不接全部来源。
2. **每阶段可独立验收**：任一阶段失败只回滚该阶段，不影响旧面板与旧 API。
3. **日志最后接**：日志风险最高，放在 P6，默认 trial，达标才激活。
4. **质量靠标注，不靠直觉**：P4 之前不宣称阈值可靠。
5. **不做非目标**：语义搜索、LLM 提炼、自动注入任务一律不做。

---

## 2 阶段总览

| 阶段 | 主题 | 主要交付 | 退出条件 |
|---|---|---|---|
| P0 | 基线核查 | 并发/备份/迁移现状确认；一次手动快照 | 事实记录完整，快照可读 |
| P1 | 证据模型 | 7 张表、版本化 raw、提取历史、独立性分组、索引、种子、脱敏、硬闸、幂等写入、审计脚本 | AC-1 / AC-2 / AC-4 / AC-11 / AC-12 |
| P2 | 确定性提炼 | `extract.py`、黄金样本、双版本回归 | 重放逐字节一致；AC-13 |
| P3 | 核心来源 | 8 个 active 适配器、30 天回填、来源健康、调度 | 回填完成，核心接口无回归 |
| P4 | 质量校准 | 200 条分层标注、混淆矩阵、阈值冻结 | AC-7 / AC-8 / AC-9 |
| P5 | API 与面板 | `/api/v1/materials`、新素材页、旧区域移除 | 面板可用，旧 API 不变 |
| P6 | 日志试运行 | trial 采集、签名归一、隔离观察 | AC-10 后人工激活 |

---

## 3 各阶段任务

### P0 基线核查

| 任务 | 产出 |
|---|---|
| 确认 WAL / busy_timeout / 连接池 / 备份策略 | 记录到 `data-model.md` §3.9 与本文档 |
| 确认 `create_all` + `migrations.py` 的建表顺序 | 记录模型导入点 |
| 手动触发一次 SQLite 快照 | 备份文件路径与可读性验证 |
| 确认 SQLite 版本支持部分索引与 `IF NOT EXISTS` | 版本号记录 |

退出条件：基线事实可复现；备份可恢复；无未决的迁移疑问。

### P1 证据模型

| 任务 | 产出 |
|---|---|
| `materials/models.py` 定义 7 张表 | 表、复合唯一约束、部分索引 |
| 版本化 raw：`raw_revision`、`source_snapshot_hash`、`sanitized_payload_hash`、`sanitizer_config_hash` | 旧 witness 可按旧 revision 回读 |
| `material_extract` 追加式历史 | 同版本重放不重复写；新版本不覆盖 |
| `material_source_audit` 分组审计 | 分组调整有 old/new/actor/reason |
| `db.init_db()` 导入模型并种子 10 个来源 | `independence_group=key`，trial 来源正确 |
| `materials/sanitize.py` 等长脱敏 + 复扫 | 单元测试：偏移不变、密钥 0 命中 |
| `materials/quality.py` 硬闸 + 四维评分 | 表驱动测试：8 条硬闸、阈值边界 |
| `materials/pipeline.py` 单批事务 + 游标绑定 | 集成测试：失败回滚、锁冲突重试、版本指针 |
| `scripts/materials_audit.py` 不变量审计 | I1~I18 查询可执行 |
| `test.md` 初版 | 用例清单与验收映射 |

退出条件：AC-1、AC-2、AC-4、AC-11、AC-12 通过；审计脚本在空库与样本库均通过。

### P2 确定性提炼

| 任务 | 产出 |
|---|---|
| `materials/extract.py` 切句、分类、去样板 | 纯函数，无 IO |
| 复用 `task_lessons.py` 的 `_SENT_SPLIT`、测试关键词、pitfalls 规则 | 抽取公共函数或显式复制并注明来源 |
| 黄金样本：HN 20、run 10、文档 5、讨论 5、history 5 | `tests/fixtures/materials/` |
| 双版本样本：同一 raw 在 v1 / v2 下输出不同 | 两条 extract、两条 material、旧 link 保留 |
| 每个适配器的 extract 函数 | 输入 raw → 期望 Draft 列表 |
| 分类基线 | 50 条人工标注的初始准确率 |

退出条件：同一 raw 连续提炼 3 次输出逐字节一致；黄金样本全部通过；双版本样本 AC-13 通过；分类基线记录在案。

### P3 核心来源

| 任务 | 产出 |
|---|---|
| `hn` / `github-trending` 适配器 | 读 observer 文件，按日期增量 |
| `doc.requirement` / `doc.review` | 扫已批准文档的 FR-n 与评审结论 |
| `discussion.closed` | conclusions 按条切分 |
| `run.result` / `run.failure` | exit_code 分流；复用测试证据规则 |
| `history.root_cause` | 读 historyevent.root_cause |
| 调度与隔离 | 每源间隔、日上限、独立 try/except |
| 独立性分组 | 每源默认独立组；镜像源同组；调整写审计 |
| 30 天回填 | 一次性 backfill 命令 |
| 来源健康接口 | 模式、最后运行、错误、计数 |

退出条件：30 天回填完成；8 个 active 来源均有产出；单源注入异常不影响其他源；核心 API P95 回归 ≤10%。

### P4 质量校准

| 任务 | 产出 |
|---|---|
| 200 条分层抽样（kind × origin） | 标注 CSV：kind、是否可用、是否重复、理由 |
| 每格样本目标 | 总样本 200；实际存在的组合每格 ≥10，活跃组合 ≥20；不足标“证据不足” |
| 混淆矩阵与误收/漏收曲线 | 分格 + 汇总，不只报总数 |
| 权重与阈值消融 | 2~3 组候选 |
| 冻结 `quality_model_version=2` | 配置与版本号 |
| 重复对标注 | 合并正确率 |
| 校准报告 | `calibration-YYYYMMDD.md`，记录样本分布与证据不足格 |

退出条件：AC-7 ≥90%、AC-8 误收 ≤5% / 漏收 ≤15%、AC-9 ≥95%；报告落盘且标注了证据不足的格子；未达标则回到 P2 调整规则，不进入 P5。

### P5 API 与面板

| 任务 | 产出 |
|---|---|
| 先写 `api.md` | 接口契约、错误码、示例 |
| `materials/api.py` | 10 个端点；分页；错误码 |
| `MaterialPanelView.jsx` 重写 | origin/kind 筛选、搜索、详情、来源健康 |
| 移除旧区域 | 洞察、选题、creativity、价值发现从素材页移除 |
| 兼容检查 | 旧 `/api/v1/mio/material-panel` 返回结构不变 |

退出条件：面板可用；旧 API 兼容；引用可增删查；状态转移合法。

### P6 日志试运行

| 任务 | 产出 |
|---|---|
| `hub.console` / `observer.serve` 适配器 | 尾部读取、错误块切分 |
| 签名归一 | 去时间戳/数字/UUID/路径/端口 |
| trial 模式 | 只写 quarantine |
| 安全扫描 | 入库后复扫密钥 |
| 7 天观察报告 | 误合并、噪声、唯一签名数 |
| 激活评审 | 达标才 `mode=active` |

退出条件：AC-10 全过；人工评审通过；否则保持 trial。

---

## 4 测试策略

| 类型 | 范围 | 工具/方法 |
|---|---|---|
| 单元 | sanitize / extract / quality 纯函数 | pytest 参数化，表驱动 |
| 集成 | 临时 SQLite：唯一约束、重放、事务回滚、锁冲突 | pytest + 临时 DB |
| 黄金样本 | 真实 raw → 期望 Draft | `tests/fixtures/materials/` |
| 故障注入 | 适配器异常、文件缺失、DB locked、编码异常、重建进程崩溃 | monkeypatch / 子进程 kill |
| 性能 | 1 万条合成素材列表查询 | 计时断言 P95 |
| 安全 | 密钥语料脱敏与复扫 | 正则语料集 |
| 校准 | 200 条分层标注 | 人工 + 脚本统计 |

### 4.1 四个必测反例

| 编号 | 反例 | 数据不变量 | 验收 |
|---|---|---|---|
| T-MIRROR | 两个镜像源重复传播同一内容 | `corroboration = COUNT(DISTINCT witness.independence_group) = 1`；`occurrences = 2`；`material_source_audit` 恰 1 行；改组后旧 witness 的 group 不变 | AC-11 |
| T-RAWVER | sanitizer 升级后检查旧 witness | `witness.raw_id` 仍指向旧 revision；`quote_exact` 在旧 `payload` 中逐字命中；`occurrences` 不因新 revision 增加；掩码长度变化后 fingerprint 仍相同、不新建 material；新旧 witness 的 version/hash 各自匹配 | AC-12 |
| T-EXTRACT | 同一 raw 经两个提取版本产生不同结果 | `COUNT(material_extract)=2`；`COUNT(material)=2`；M1 的 `material_link` 数不变；`(material_id, raw_id, extract_version)` 无冲突 | AC-13 |
| T-CRASH | 重建第 1 批提交后杀进程 | `rebuild_cursor` 停在批 1；重启后 `rebuild_resumed_count=1`；extract 无重复；link 数不变；最终计数与一次跑完一致 | AC-14 |

对应测试文件（P1/P2 落地）：

- `tests/test_materials_mirror.py::test_mirror_corroboration`
- `tests/test_materials_versioning.py::test_old_witness_reads_old_raw`
- `tests/test_materials_extract_history.py::test_two_versions_keep_history`
- `tests/test_materials_rebuild_crash.py::test_resume_after_crash`

每阶段结束必须更新 `test.md`，把用例编号映射到 AC。

---

## 5 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 阈值拍脑袋 | 好素材被隔离或噪声进主库 | P4 标注校准；trial 先隔离；阈值可配置 |
| 日志噪声淹没 | 主库不可用 | P6 才接；trial 只入隔离；签名去重 |
| 脱敏漏网 | 密钥落库 | 等长掩码 + 入库后复扫 + 隔离告警 |
| 分类错误 | 证据角色错位 | 黄金样本 + 标注准确率门槛 |
| DB 写锁竞争 | 核心接口变慢 | 短事务、批量上限、重试、只写素材表 |
| 重建改坏引用 | 证据链断裂 | 素材不可变；已引用不自动归档；重建前后对比 |
| 分组配错 | 镜像被当独立来源，或独立来源被合并 | 默认每源独立；调整写审计；P4 复核 |
| 提取历史膨胀 | DB 增长超预算 | 只存 quote/locator/kind；200MB 告警；不删被引用 raw |
| 崩溃后续跑丢批次 | 计数重复或引用缺失 | 批次事务 + rebuild_cursor + 锁过期接管 |
| 范围蔓延 | 又变成 LLM 管线 | 非目标写入需求；评审时逐条对照 |
| 旧面板调用方 | 接口破坏 | 旧 API 保留；新面板独立路径 |

---

## 6 回滚预案

| 阶段 | 回滚 |
|---|---|
| P1 | 停 job；按依赖顺序 `DROP TABLE material_link, material_witness, material_extract, material, material_raw, material_source_audit, material_source`；旧功能不受影响 |
| P2 | 回退 `extract.py`；raw 保留，可重放 |
| P3 | 停对应 source；已入库素材保留 |
| P4 | 恢复 `quality_model_version=1` 配置；不回溯评分 |
| P5 | 前端回退上一版；新 API 可保留但面板不调用 |
| P6 | `mode=trial` 或 `off`；隔离数据保留 |

灾难回滚：停 material job → 恢复最近小时级快照 → 重启 hub。旧业务表与旧 API 不在回滚范围内。

---

## 7 交付清单

| 文档 | 阶段 | 状态 |
|---|---|---|
| `requirement.md` | 设计 | draft |
| `spec.md` | 设计 | draft |
| `data-model.md` | P1 | draft |
| `api.md` | P5 前 | 未开始 |
| `test.md` | P1 起 | 未开始 |
| `changelog.md` | P5 | 未开始 |
| `review.md` | P5 | 未开始 |

---

## 8 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-10-28 | 初稿：P0~P6 分阶段，先冻结模型/事务/硬闸/指标 |
| v1.1 | 2026-10-28 | 按评审补：版本化 raw、提取历史、独立性分组审计、分层校准、崩溃恢复、四反例 |
