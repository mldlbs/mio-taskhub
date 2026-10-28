# 素材库（Material Library）测试验收

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.3 |
| 最后更新 | 2026-10-28 18:00 |
| 状态 | approved |
| 负责人 | opencode |
| 上游依据 | `spec.md` v1.1 §5、`plan.md` v1.1 §4 |
| 下游关联 | P4 正式标注、P5 完整 API / 面板 |

---

## 1 验收标准

| 编号 | 指标 | 目标 | 状态 |
|---|---|---|---|
| AC-1 | 重放幂等 | 同一 raw 重放 3 次，素材行数、occurrences、corroboration 不变 | 已覆盖 |
| AC-2 | 逐字核验 | active 素材 locator 回读命中率 100% | 已覆盖（硬闸 + 审计） |
| AC-4 | 计数语义 | 同源 100 次重复：occurrences=100、corroboration=1 | 已覆盖（按来源记录去重） |
| AC-5 | 故障隔离 | 单源异常不影响其他源 | 已覆盖（P3 job） |
| AC-11 | 镜像佐证 | 同组两源同文 corroboration=1；改组不回溯 | 已覆盖 |
| AC-12 | 旧 witness 可回读 | sanitizer 升级后旧 witness 仍命中旧 revision | 已覆盖 |
| AC-13 | 提取历史 | 两版本输出不同：两条 extract、两条 material、旧 link 保留 | 已覆盖 |
| AC-14 | 重建崩溃恢复 | 杀进程后从最后成功批次续跑 | 已覆盖 |
| AC-7/8/9 | 质量校准 | 200 条分层标注 | P4 待做 |
| AC-10 | 日志安全 | 密钥 0、误合并 ≤2% | P6 待做 |

---

## 2 测试范围

### 已覆盖（P1 + P2 + P3）

- 7 张表、复合唯一约束、主 witness 部分唯一索引、来源种子
- 等长脱敏、密钥复扫、掩码折叠指纹、稳定 hash
- 8 条硬闸、四维评分、TTL、trial 隔离
- 单批事务、raw revision 分配、重放幂等、计数语义
- 引用前置条件、配置漂移拒绝
- 四个反例：T-MIRROR / T-RAWVER / T-EXTRACT / T-CRASH
- 10 个来源的确定性提炼器、16 条精确黄金样本、50 条分类基线
- 7 类真实来源适配器、30 天回填、采集 job 日上限/单源隔离、来源健康端点
- 生产只读预检、副本双跑演练、业务表零变化证明
- P4 200 条分层样本与证据不足标记

### 未覆盖（后续阶段）

| 范围 | 阶段 | 说明 |
|---|---|---|
| 生产库实际回填 | 待批准 | 预检通过，但未执行 |
| 阈值校准 | P4 | 样本已准备，标签未填写 |
| 完整 API / 面板 | P5 | 当前只有 `GET /materials/sources` |
| 日志试运行 | P6 | 当前只验证 trial 隔离 |

---

## 3 用例清单

| 编号 | 测试文件与用例 | 覆盖 |
|---|---|---|
| T01 | `test_materials_models.py::test_seed_sources` | 10 个来源、trial 默认 |
| T02 | `test_materials_models.py::test_raw_revision_unique` | raw revision 唯一 |
| T03 | `test_materials_models.py::test_raw_snapshot_unique` | 同内容+算法+配置不重复 |
| T04 | `test_materials_models.py::test_primary_witness_partial_index_exists` | 主 witness 部分唯一索引 |
| T05 | `test_materials_models.py::test_source_audit_append_only_shape` | 分组/模式审计 |
| T06 | `test_materials_sanitize.py::test_mask_preserves_length_and_newlines` | 等长掩码 |
| T07 | `test_materials_sanitize.py::test_scan_finds_original_but_not_masked` | 密钥复扫 |
| T08 | `test_materials_sanitize.py::test_sanitize_payload_hashes_stable` | 源/脱敏 hash |
| T09 | `test_materials_sanitize.py::test_fingerprint_folds_mask_runs` | 掩码折叠指纹 |
| T10 | `test_materials_quality.py::test_gate_accepts_verbatim` | 硬闸通过 + locator |
| T11 | `test_materials_quality.py::test_gate_rejects_wrong_quote` | HG-2/HG-3 |
| T12 | `test_materials_quality.py::test_gate_rejects_short_and_invalid_kind` | HG-4/HG-5 |
| T13 | `test_materials_quality.py::test_gate_rejects_signal_with_project_scope` | HG-7 |
| T14 | `test_materials_quality.py::test_gate_rejects_llm_product_source` | HG-6 |
| T15 | `test_materials_quality.py::test_density_and_status` | 评分 + active/trial |
| T16 | `test_materials_quality.py::test_expires_by_kind` | TTL |
| T17 | `test_materials_pipeline.py::test_replay_is_idempotent` | AC-1 |
| T18 | `test_materials_pipeline.py::test_raw_revision_does_not_inflate_occurrences` | AC-4 / 版本化 |
| T19 | `test_materials_pipeline.py::test_trial_source_never_active` | trial 隔离 |
| T20 | `test_materials_pipeline.py::test_link_requires_active_material` | 引用前置 |
| T21 | `test_materials_pipeline.py::test_link_rejects_quarantine_material` | 引用前置 |
| T22 | `test_materials_pipeline.py::test_rebuild_rejects_config_drift_without_version_bump` | 配置漂移拒绝 |
| T23 | `test_materials_mirror.py::test_mirror_counts_one_and_group_change_does_not_rewrite_history` | AC-11 |
| T24 | `test_materials_versioning.py::test_old_witness_survives_sanitizer_upgrade` | AC-12 |
| T25 | `test_materials_extract_history.py::test_two_versions_keep_history_and_links` | AC-13 |
| T26 | `test_materials_rebuild_crash.py::test_rebuild_crash_resumes_from_last_committed_batch` | AC-14 |
| T27 | `test_materials_extract.py::test_registry_covers_all_seeded_sources` | 10 个来源都有提炼器 |
| T28 | `test_materials_extract.py::test_unknown_source_raises` | 未登记来源拒绝 |
| T29 | `test_materials_extract.py::test_golden_exact` | 16 条黄金样本精确输出 |
| T30 | `test_materials_extract.py::test_golden_is_deterministic` | 同一输入 3 次逐字节一致 |
| T31 | `test_materials_extract.py::test_classification_baseline_50` | 50 条分类基线 ≥90% |
| T32 | `test_materials_extract.py::test_public_lesson_helpers` | 复用切句/证据规则 |
| T33 | `test_materials_adapters.py::test_hn_adapter_and_cursor` | HN 适配 + 日期游标 |
| T34 | `test_materials_adapters.py::test_hn_limit_does_not_skip_mid_file` | 限额不跳过未处理条目 |
| T35 | `test_materials_adapters.py::test_github_adapter_builds_clean_content` | GitHub 清洗 `repo — desc` |
| T36 | `test_materials_adapters.py::test_doc_requirement_adapter` | 已批准需求文档 |
| T37 | `test_materials_adapters.py::test_doc_review_adapter` | 评审结论 |
| T38 | `test_materials_adapters.py::test_discussion_adapter` | 讨论结论 + 项目 |
| T39 | `test_materials_adapters.py::test_run_adapters_split_by_exit_code` | run result/failure 分流 |
| T40 | `test_materials_adapters.py::test_history_root_cause_adapter` | 根因事件 |
| T41 | `test_materials_adapters.py::test_adapter_to_pipeline_end_to_end` | 适配器 → 入库 |
| T42 | `test_materials_jobs.py::test_job_daily_cap` | 日上限 |
| T43 | `test_materials_jobs.py::test_job_isolates_source_failure` | 单源失败隔离 |
| T44 | `test_materials_api.py::test_sources_health_endpoint` | 来源健康端点 |
| T45 | `test_materials_bootstrap.py::test_bootstrap_idempotent_on_global_engine` | 只建素材表 + 幂等 |
| T46 | `test_materials_bootstrap.py::test_bootstrap_does_not_touch_existing_task_table` | 不碰业务表结构/数据 |
| T47 | `test_materials_bootstrap.py::test_ingest_retries_locked` | 锁冲突重试 |
| T48 | `test_materials_preflight.py::test_preflight_read_only_flags` | 只读预检缺列/备份 |
| T49 | `test_materials_preflight.py::test_preflight_flags_existing_material_tables` | 已存在素材表告警 |
| T50 | `test_materials_sample.py::test_allocate_proportional_and_capped` | 分层配额 |
| T51 | `test_materials_sample.py::test_build_sample_shape_and_determinism` | 200 条样本可复现 |
| T52 | `test_materials_pipeline.py::test_rejected_raw_is_not_reprocessed_same_version` | 拒收 raw 不重复处理 |

---

## 4 四个反例的断言

| 反例 | 关键断言 |
|---|---|
| T-MIRROR | 同组两源同文：`corroboration=1`；改组后旧 witness 的 `independence_group` 不变；新 witness 才可能增加佐证 |
| T-RAWVER | 旧 witness 的 `raw_id`、`sanitized_payload_hash`、`sanitizer_version` 不变；`quote_exact` 在旧 payload 中命中；`occurrences` 不虚增 |
| T-EXTRACT | 两条 `material_extract`、两条 material；旧 `material_link` 保留；同版本重放不新增 |
| T-CRASH | `rebuild_cursor` 停在已提交批次；重启后 `rebuild_resumed_count=1`；extract 无重复、link 数不变 |

### 4.1 P2 黄金样本与分类基线

| 文件 | 内容 | 结果 |
|---|---|---|
| `tests/fixtures/materials/golden.json` | 16 条精确期望，覆盖 10 个来源 | 全部通过，逐字节一致 |
| `tests/fixtures/materials/classification.json` | 50 条分类基线（kind 期望） | 准确率 100%（自建基线，不等同真实分布） |

说明：50 条基线是工程回归集，不是 P4 的现场标注集；P4 必须重新抽样并报告误收/漏收。

### 4.2 P3 真实回填验证（生产库副本）

在 `taskhub.db` 副本上执行，不写生产库：

| 来源 | raw | materials | 状态 |
|---|---|---|---|
| hn（30 天） | 3,373 | 1,957 | active 1,927 / quarantine 30 |
| github-trending（30 天） | 112 | 98 | active 91 / quarantine 7 |
| doc.requirement（180 天） | 14 | 138 | active 138 |
| doc.review | 89 | 87 | active 87 |
| discussion.closed | 60 | 76 | active 76 |
| run.result | 193 | 216 | active 216 |
| run.failure | 5,011 | 37 | active 37 |
| history.root_cause | 1 | 1 | active 1 |

回填后 `scripts/materials_audit.py` 返回 `ok: true`。生产库未写入，等待确认后再执行。

### 4.3 生产预检与 P4 样本准备

| 证据 | 结果 |
|---|---|
| 只读预检 | `quick_check=ok`；33 张表；无 `material_*`；最近备份可用 |
| 旧 exe 影响 | 新表/索引不引用业务表；回填脚本使用 `init_materials_db()`，不跑既有迁移 |
| 副本双跑 | 业务表 7 张哈希前后一致；第二遍 raw/material/witness 零新增 |
| 审计 | `ok: true` |
| P4 样本 | 200 条；5 个层有数据，20 个层标记证据不足 |
| 新 exe | **阻塞**：生产库缺 `task.updated_at`，需先补迁移 |

详细报告：`docs/taskhub/materials/preflight-20261028.md`。

---

## 5 运行方式

```bash
# 素材库 P1 + P2 + P3 + 预检/P4 准备
python -m pytest tests/test_materials_models.py tests/test_materials_sanitize.py \
  tests/test_materials_quality.py tests/test_materials_pipeline.py \
  tests/test_materials_mirror.py tests/test_materials_versioning.py \
  tests/test_materials_extract_history.py tests/test_materials_rebuild_crash.py \
  tests/test_materials_extract.py tests/test_materials_adapters.py \
  tests/test_materials_jobs.py tests/test_materials_api.py \
  tests/test_materials_bootstrap.py tests/test_materials_preflight.py \
  tests/test_materials_sample.py -q

# 不变量审计（使用当前 MIO_TASKHUB_DB）
python scripts/materials_audit.py

# 30 天回填（默认全部已适配来源）
python scripts/materials_backfill.py --days 30
```

素材库实测：`52 passed`；全量 `1662 passed / 2 skipped / 4 deselected`（2026-10-28）。

---

## 6 已知限制

1. P1~P3 只提供库、流水线、适配器与一个来源健康端点；完整素材 API/面板在 P5。
2. 30 天回填在生产库副本上验证，生产库尚未写入。
3. 新 exe 部署仍被 `task.updated_at` 缺列阻塞；需先由该改动补迁移。
4. P4 的 200 条样本已准备，但标签未填写、阈值未冻结。
5. `material_source_audit` 的“只增不改不删”目前靠约定与写路径保证，未加数据库触发器。
6. 质量阈值尚未校准，`quality_model_version=1` 只是工程初值。
7. `MIO_MATERIALS_AUTOSTART=0` 可关闭采集后台线程（测试默认关闭）。
