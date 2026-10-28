# 素材库（Material Library）测试验收

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 最后更新 | 2026-10-28 15:45 |
| 状态 | approved |
| 负责人 | opencode |
| 上游依据 | `spec.md` v1.1 §5、`plan.md` v1.1 §4 |
| 下游关联 | P2 适配器测试、P5 API 测试 |

---

## 1 验收标准

| 编号 | 指标 | 目标 | P1 状态 |
|---|---|---|---|
| AC-1 | 重放幂等 | 同一 raw 重放 3 次，素材行数、occurrences、corroboration 不变 | 已覆盖 |
| AC-2 | 逐字核验 | active 素材 locator 回读命中率 100% | 已覆盖（硬闸 + 审计） |
| AC-4 | 计数语义 | 同源 100 次重复：occurrences=100、corroboration=1 | 已覆盖（按来源记录去重） |
| AC-11 | 镜像佐证 | 同组两源同文 corroboration=1；改组不回溯 | 已覆盖 |
| AC-12 | 旧 witness 可回读 | sanitizer 升级后旧 witness 仍命中旧 revision | 已覆盖 |
| AC-13 | 提取历史 | 两版本输出不同：两条 extract、两条 material、旧 link 保留 | 已覆盖 |
| AC-14 | 重建崩溃恢复 | 杀进程后从最后成功批次续跑 | 已覆盖 |
| AC-7/8/9 | 质量校准 | 200 条分层标注 | P4 待做 |
| AC-10 | 日志安全 | 密钥 0、误合并 ≤2% | P6 待做 |

---

## 2 测试范围

### 已覆盖（P1）

- 7 张表、复合唯一约束、主 witness 部分唯一索引、来源种子
- 等长脱敏、密钥复扫、掩码折叠指纹、稳定 hash
- 8 条硬闸、四维评分、TTL、trial 隔离
- 单批事务、raw revision 分配、重放幂等、计数语义
- 引用前置条件、配置漂移拒绝
- 四个反例：T-MIRROR / T-RAWVER / T-EXTRACT / T-CRASH

### 未覆盖（后续阶段）

| 范围 | 阶段 | 说明 |
|---|---|---|
| HN / GitHub / 文档 / run / history 适配器 | P3 | P1 用测试 extractor 替身 |
| 质量阈值校准 | P4 | 需要人工标注集 |
| API / 面板 | P5 | 当前无 HTTP 入口 |
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

---

## 4 四个反例的断言

| 反例 | 关键断言 |
|---|---|
| T-MIRROR | 同组两源同文：`corroboration=1`；改组后旧 witness 的 `independence_group` 不变；新 witness 才可能增加佐证 |
| T-RAWVER | 旧 witness 的 `raw_id`、`sanitized_payload_hash`、`sanitizer_version` 不变；`quote_exact` 在旧 payload 中命中；`occurrences` 不虚增 |
| T-EXTRACT | 两条 `material_extract`、两条 material；旧 `material_link` 保留；同版本重放不新增 |
| T-CRASH | `rebuild_cursor` 停在已提交批次；重启后 `rebuild_resumed_count=1`；extract 无重复、link 数不变 |

---

## 5 运行方式

```bash
# 素材库 P1
python -m pytest tests/test_materials_models.py tests/test_materials_sanitize.py \
  tests/test_materials_quality.py tests/test_materials_pipeline.py \
  tests/test_materials_mirror.py tests/test_materials_versioning.py \
  tests/test_materials_extract_history.py tests/test_materials_rebuild_crash.py -q

# 不变量审计（使用当前 MIO_TASKHUB_DB）
python scripts/materials_audit.py
```

P1 实测：`26 passed`；全量 `1636 passed / 2 skipped / 4 deselected`（2026-10-28）。

---

## 6 已知限制

1. P1 只提供库与流水线，没有 HTTP 入口，不能从 UI 触发。
2. 适配器是测试替身；真实来源接入在 P3。
3. `material_source_audit` 的“只增不改不删”目前靠约定与写路径保证，未加数据库触发器。
4. 质量阈值尚未校准，`quality_model_version=1` 只是工程初值。
