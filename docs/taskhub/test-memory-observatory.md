# 测试与验收（Test & Acceptance）— memory-observatory

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | planned |
| 任务 | 8a966293 |
| 上游需求 | docs/taskhub/requirement-memory-observatory.md（FR-1~FR-10） |
| 上游规格 | docs/taskhub/spec-memory-observatory.md「测试计划」 |
| 更新日期 | 2026-10-05 |

---

## ✅ 验收标准

| FR 编号 | 验收标准 | 验证方式 |
|---------|---------|---------|
| FR-1 | 端点返回 `{entities, relations, meta}`，只读两源文件，错误体 `{error}` | `test_api_endpoint`、`test_only_reads_mio_home`、`test_relations_and_ids` |
| FR-2 | 默认隐藏 `note+task-outcome` 日志；`logs=1` 展开；`MIO_OBSERVATORY_HIDE_LOGS` 强制隐藏 | `test_default_view_hides_logs`、`test_logs_param_expands`、`test_env_force_hide_logs` |
| FR-3 | 复用热度公式与 mneme 逐项一致（含下限 0） | `test_reuse_score_formula` |
| FR-4 | kind→类型映射、`[agent]` 前缀剥离、标题 >60 截断、顺序编号与去重 | `test_kind_map_clean_title`、`test_relations_and_ids` |
| FR-5 | 证据统计覆盖全量记忆；坏行进 `meta.skipped` 不中断 | `test_evidence_stats`、`test_bad_lines_skipped` |
| FR-6 | mtime 缓存命中不重读；`showLogs`/文件变更触发重建 | `test_mtime_cache` |
| FR-7 | Rail「记忆观测」视图全区块可达（卡片/筛选/日志开关/本地过滤/出入边/片段/聚合），仅调新端点 | `npm run build` 绿 + 手工验收清单（FR-7 手工） |
| FR-8 | 全量回归只增不减；前端构建绿 | 全量 `pytest -q` + `npm run build` |
| FR-9 | 文档链门控：doc_quality 无 errors、棘轮不回退、pre-push 通过 | `taskhub_doc_quality` / ratchet / pre-push 记录 |
| FR-10 | mneme 源仓库零改动 | `git -C mneme status --short` 为空 |

---

## 🔍 测试范围

- 单元/接口测试：
  - `tests/test_memory_observatory.py`（11 用例，对标 mneme 投影语义：只读源、日志可见性、公式、清洗/标题、关系编号、证据统计、坏行容错、mtime 缓存、端点契约）
- 回归测试：
  - 全量 `pytest tests -q`（观测层用例只增不减；`test_spinning_guard` 4 失败为存量问题，基线同样失败，与本任务无关）
  - `cd web && npm run build`（Vite 生产构建）
- 手工验收：
  - Rail 打开「记忆观测」→ 默认视图 / 日志一键展开 / 类型 chips / 本地过滤（非权威检索标注）/ 详情出入边跳转 / 提示词片段复制与导出 / 项目与来源聚合
  - 红线扫描：新前端无 `高价值` / `规则提取` 措辞；`权威检索` 仅以「非权威检索」否定式出现
  - 残留扫描：`web/src` 与 `mio_taskhub/` 无 `3456` / `hermes-server` 运行时引用
- 明确不测的：
  - 全图力导向渲染（非目标）、分页/limit（沿用 mneme 决定）、投影性能硬门槛（仅记录实测值）
  - `buildAdjacency` 纯函数单测：web 侧无测试基建（无 vitest/jest），记为验收观察项，由构建 + 手工 ego 交互覆盖

---

## 📋 用例清单

| 用例编号 | 覆盖需求 | 前置 | 步骤 | 预期 |
|---------|---------|------|------|------|
| TC-1 | FR-1 | 假 `MIO_HOME` 两源文件就绪 | `GET /api/v1/memory/observatory/data` | 200，键集合 `{entities, relations, meta}`，仅 `mio-*` 实体，源文件字节不变 |
| TC-2 | FR-2 | 数据含 `note+task-outcome` 行 | 默认请求 / `?logs=1` / 设 `MIO_OBSERVATORY_HIDE_LOGS` 后 `?logs=1` | 日志实体依次 隐藏 / 出现 / 仍隐藏 |
| TC-3 | FR-3 | 构造 observer 源、各类 kind、决策词、task-outcome 标签、证据数 | 直调 `compute_reuse_score` | 各边界得分与公式一致，最小为 0 |
| TC-4 | FR-4 | 数据含 decision/未知 kind/前缀行/超长行/空内容 | 解析后查实体 | `decision→rule`、未知→`note`、前缀剥离、60 截断 `…`、空内容标题兜底 |
| TC-5 | FR-5 | 复用文件 4 行（含无 id 行） | 查 `meta.evidence` | `memTotal=4`、`memWithEvidence=1`、`reuseRecords=4` |
| TC-6 | FR-5 | 记忆/复用文件各含坏行 | `GET` 端点 | 200，`meta.skipped={mio:1, reuse:1}`，好行照常投影 |
| TC-7 | FR-6 | 首次构建后连续两次同参调用 | monkeypatch `read_jsonl` 计数 + 改文件 / 改 `showLogs` | 命中不重读；两种变更各自触发重建 |
| TC-8 | FR-1 | 同上 | 注入 `build_data_cached` 抛异常 | 500，响应体恰为 `{"error": "boom"}` |
| TC-9 | FR-7 | hub 运行且前端构建完成 | Rail 打开「记忆观测」，逐项操作手工清单 | 全区块可达、文案合规、无 3456/hermes 引用 |
| TC-10 | FR-8 | 全部实现落地 | 全量 pytest + `npm run build` | 观测层 11 用例全绿；构建成功 |
| TC-11 | FR-9/FR-10 | 文档回填完成 | doc_quality / ratchet / `git -C mneme status` | errors=0、分数不低于基线、mneme 干净 |

---

## 4. 验收流程

- 谁验收：实现 agent 自测 + taskhub 文档质量门 / pre-push 门控
- 何时验收：Step 5（回归）与 Step 7（提交门控）
- 通过条件：FR-1~FR-10 全覆盖、`test_api_endpoint` 等 11 用例全绿、构建绿、门控通过
- 不通过的回退路径：`git checkout main && git branch -D task-8a966293`（已批准文档不回退）
