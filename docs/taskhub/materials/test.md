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
| 生产库实际回填 | 已执行 | 2026-10-28 18:58；证据 `evidence/20261028/` |
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
| T53 | `test_materials_sample.py::test_build_sample_includes_all_quarantine` | 隔离样本全量入样 |
| T54 | `test_materials_label.py::test_review_acceptance_is_decision_not_problem` | 评审通过 vs problem |
| T55 | `test_materials_label.py::test_review_rejection_is_problem` | 评审拒绝 |
| T56 | `test_materials_label.py::test_generic_failure_is_not_usable` | 泛化失败不可用 |
| T57 | `test_materials_label.py::test_short_release_title_is_usable` | 短发布标题 |
| T58 | `test_materials_label.py::test_three_numbered_items_is_not_atomic` | 多编号项非原子 |
| T59 | `test_materials_label.py::test_boilerplate_word_boundary` | 样板词边界 |
| T60 | `test_materials_extract_p5.py::test_review_acceptance_splits_and_stays_decision` | 评审通过 + 原子切分 |
| T61 | `test_materials_extract_p5.py::test_review_rejection_is_problem` | 评审拒绝 |
| T62 | `test_materials_extract_p5.py::test_long_single_sentence_review_is_rejected` | 超长单句拒绝 |
| T63 | `test_materials_extract_p5.py::test_discussion_long_items_split` | 讨论结论切分 |
| T64 | `test_materials_extract_p5.py::test_run_failure_generic_is_rejected_by_pipeline` | 泛化失败排除 |
| T65 | `test_materials_extract_p5.py::test_signal_question_is_rejected_by_pipeline` | 问句 signal 排除 |
| T66 | `test_materials_extract_p5.py::test_signal_short_release_is_accepted` | 短发布标题保留 |
| T67 | `test_materials_extract_p5.py::test_run_result_does_not_silently_truncate` | 不静默截断 |
| T68 | `test_materials_extract_p5.py::test_run_result_kind_mixing_is_classified` | result/fact/lesson/problem 分类 |
| T69 | `test_materials_extract_p5.py::test_decision_fragment_is_rejected` | decision 碎片拒绝 |
| T70 | `test_materials_extract_p5.py::test_decision_with_predicate_is_accepted` | decision 谓词通过 |
| T71 | `test_materials_extract_p5.py::test_decision_truncated_colon_is_rejected` | 冒号截断拒绝 |
| T72 | `test_materials_extract_p5.py::test_signal_positive_examples_are_not_killed` | signal 正例不误杀 |
| T73 | `test_materials_extract_p5.py::test_signal_negative_examples_are_excluded` | signal 反例排除 |
| T74 | `test_materials_review.py::test_review_analysis_agreement_and_disagreement` | 双人一致率/κ/分歧清单 |
| T75 | `test_materials_review.py::test_review_analysis_requires_reason` | 缺理由拒绝分析 |
| T76 | `test_materials_review.py::test_review_kit_builds_blind_pack_and_verifies` | 盲包重建/hash 校验/篡改检出 |
| T77 | `test_materials_review.py::test_review_analysis_flags_quote_tamper_and_identity_collision` | 原文篡改与共用代号检出 |
| T78 | `test_materials_review.py::test_review_kit_handout_copies_blank_sheets` | 双人空白副本发放与哈希 |
| T79 | `test_materials_known_disputes.py`（49 项） | 24 条已知争议 × 结果/确定性 + 类别覆盖 |
| T80 | `test_materials_validate.py`（3 项） | 结构缺陷、held-out 排除、类型/状态漂移 |
| T81 | `test_materials_gate.py`（25 项） | I1–I18 逐项阻塞、边界值、缺失库/表、CLI 退出码 |
| T82 | `test_migrate_task_updated_at.py`（3 项） | task.updated_at 备份/回填/触发器/回滚/缺表 |
| T83 | `test_gate_attestation.py`（5 项） | 报告指纹、commit、DB 哈希、失败门禁 |
| T84 | `test_required_checks.py`（3 项） | 分支保护 required checks 缺失/无 token |
| T85 | `test_release_entry.py`（4 项） | 发布必须校验报告；构建不发布；CI/分支保护审计 |
| T86 | `test_migrate_flow.py`（12 项） | 预检/授权/迁移/验证/失败回滚/幂等/TOCTOU/脏代码/离线审批/并发写入 |
| T87 | `test_migration_approval.py`（8 项） | 服务端签名、过期、伪造、换库、缺事件、缺时间、重复审批 |
| T88 | `test_task_create_stage_guard.py`（2 项） | 创建禁止直接 review；默认阶段可建 |
| T89 | `test_materials_downstream_audit.py`（2 项） | 重复分组归一化、功效公式 |
| T90 | `test_repair_benchmark.py`（1 项） | 构造式基准原型：唯一快照/基线失败/回归通过（已停止，不扩展） |

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

上表为**副本演练**。生产回填已于 2026-10-28 18:58 执行：退出码 0，审计两次 `ok: true`，业务表 7 张哈希前后一致；详见 `evidence/20261028/README.md`。

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

### 4.4 P4 校准结果（未冻结阈值）

| 指标 | 结果 |
|---|---|
| 样本 | 200 条：162 active + 38 quarantine（全部隔离样本） |
| 标注 | 规则 v1.1 双遍 + 11 条争议裁决；非人工金标准 |
| active 可用率 | 未加权 47.5% / 覆盖层加权 **60.8%** |
| active 误收率 | 未加权 52.5% / 覆盖层加权 **39.2%** |
| quarantine 漏收率 | **7.9%**（3/38，仅 `signal\|external`） |
| 分类准确率 | 未加权 86.0% / 加权 97.7% |
| 阈值 | **未调整、未冻结**；0.40 下限未被触发 |
| 结论 | 当前错误主要来自提取器，不是阈值；先修提取器再校准 |

报告：`docs/taskhub/materials/calibration/calibration-20261028.md`。

### 4.5 P5 提取器修复与重验证（未写生产）

| 指标 | 结果 |
|---|---|
| 旧样本回归 | 评审误分类 19→0；泛化失败 20→0；非原子 28→0；分类错误 28→1 |
| 新样本 | 197 active + 3 quarantine；active 可用率 76.3%（加权）；误收 23.7%；漏收 100%（3/3） |
| 独立复核 | 17 条盲标：类型一致 88.2%，可用性一致仅 41.2% |
| 副本审计 | 业务表哈希一致；audit ok；第二次回填零新增 |
| 生产 | **未变更**；新提取器尚未写入生产 |
| 阈值 | **未冻结、未调整** |

报告：`docs/taskhub/materials/calibration/calibration-p5-20261028.md`。

### 4.6 P5.1 决策硬闸与复核准备

| 指标 | 结果 |
|---|---|
| decision 误收 | 52.5% → **16.4%**（95% CI 9.2–27.6） |
| signal / result 误收 | 16.7% / 11.7% |
| active 可用率（加权） | 83.9% |
| active 误收（未加权） | 15.2%（95% CI 10.9–20.9） |
| quarantine 漏收 | 100%（3/3，CI 43.9–100） |
| 独立复核 | 已取消（编号 12）；Agent 预复核 80.0% / 56.7% **不作为金标准** |
| 副本审计 | 业务表哈希一致；audit ok；二次回填零新增 |
| 生产 | **未变更** |

报告：`docs/taskhub/materials/calibration/calibration-p51-20261028.md`。

### 4.7 自动化质量验证（编号 12）

| 检查 | 结果 |
|---|---|
| 已知争议 | 24 条 / 5 类，全部通过；两次提取结果一致 |
| held-out 结构缺陷 | 0 / 1,054 |
| 全量结构缺陷 / 重复 | 0 / 0（1,378 素材） |
| P5.1→当前漂移 | 8 removed / 8 added / 32 kind；3.46% |
| 同版本复跑漂移 | 0 |
| 二次回填 | 全部 `accepted=0 / new=0 / merged=0` |
| 业务表 / 审计 | 哈希一致；`ok: true` |
| 真实可用率 / 语义误收 | **未测量** |

报告：`docs/taskhub/materials/calibration/validation-20261028.md`。
证据冻结：`docs/taskhub/materials/calibration/EVIDENCE-FREEZE-20261028.md`。

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
  tests/test_materials_sample.py tests/test_materials_label.py \
  tests/test_materials_extract_p5.py -q

# 不变量审计（使用当前 MIO_TASKHUB_DB）
python scripts/materials_audit.py

# 30 天回填（默认全部已适配来源）
python scripts/materials_backfill.py --days 30
```

素材库实测：`155 passed`；全量 `1811 passed / 2 skipped / 5 deselected`（2026-10-30，排除本机已安装目录导致的 1 项 GUI 测试）。

---

## 6 已知限制

1. P1~P4 只提供库、流水线、适配器、来源健康端点与校准报告；完整素材 API/面板在 P5。
2. 30 天回填已执行生产库；新 exe 部署仍被 `task.updated_at` 缺列阻塞。
3. P4 标签是规则辅助 + 单 agent 裁决，不是人工金标准；19 个层证据不足，阈值未冻结。
4. P5 修复只在副本验证，生产未写入；17 条独立复核的可用性一致率仅 41.2%，标签仍需人工复核。
5. `material_source_audit` 的“只增不改不删”目前靠约定与写路径保证，未加数据库触发器。
6. 质量阈值尚未校准，`quality_model_version=1` 只是工程初值。
7. `MIO_MATERIALS_AUTOSTART=0` 可关闭采集后台线程（测试默认关闭）。
