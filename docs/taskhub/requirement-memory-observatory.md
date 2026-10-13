# 📋 mneme 观测层迁移至 mio-taskhub 记忆模块 需求规格

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | 8a966293 |
| 上游依据 | mneme/docs/observatory-design.md（边界/能力定稿，commit 3ce2fbe）、mneme/README.md（端点/配置约定） |
| 更新日期 | 2026-10-05 |

---

## 🔙 背景与问题

mneme 是独立 Node 观测层（`hermes-server.js` 222 行投影服务 + `hermes-graph.html` 995 行观测前端 + `tests/server.test.js` 136 行），只读投影 `MIO_HOME`（`memory.jsonl` + `experience_reuse.jsonl`），默认端口 3456，定位为 **Memory Observatory**——Mio 是唯一 Memory Source of Truth，mneme 只呈现不治理。

痛点（真实场景）：

1. **能力游离在 hub 外**：要看记忆图谱/复用热度必须另起 Node 进程与 3456 端口，与 mio-taskhub 的 Memory Gateway（`/api/v1/memory/*`）是两套服务、两套数据视图；
2. **UI 割裂**：观测前端是孤页，与 MIO·HUB 的 Rail（MioRuntimeView 等）风格与导航不打通，观测完还得切窗口；
3. **Node 依赖**：mio-taskhub 已定「不依赖 Node」（Mio 集成决策），观测层留在 Node 栈与架构方向相悖；
4. **记忆模块缺观测面**：Memory Gateway 只有 query/record 等读写端点，没有「一屏看清记忆结构、复用热度、证据覆盖率」的观测能力。

为什么现在做：Memory Gateway 已上线（任务 c5fc195e/b521838a）；mneme 边界与能力规格已两轮评审定稿（observatory-design.md），直接照搬边界迁移风险低；一次迁移补齐记忆模块「写入 + 检索 + 观测」三面中的观测面。

---

## 📌 概述

把 mneme 的只读投影能力整体迁入 mio-taskhub：后端以纯 Python 实现投影模块并挂到现有 `/api/v1/memory` router；前端观测 UI **React 化进 Rail**（用户已裁决），成为与 MioRuntimeView 并列的「记忆观测」视图；测试对标 `tests/server.test.js` 转 pytest。mneme 原仓库**保持不动**（用户已裁决）。

> **守则**：迁移不改变 mneme 已定稿边界——只读、不写入、不治理、不做权威检索；后端零 Node 依赖；投影语义与 mneme 逐案一致。

---

## 🎯 目标与非目标

| 类型 | 内容 |
|---|---|
| ✅ 目标 | 后端投影纯 Python 实现，挂 `GET /api/v1/memory/observatory/data`，零 Node 依赖 |
| ✅ 目标 | 前端观测能力 React 化进 Rail「记忆观测」：卡片网格、观察视图、本地过滤、复用热度、提示词片段、项目/来源导航 |
| ✅ 目标 | 投影语义与 mneme 完全一致（reuse_score 公式、清洗、kind 映射、缓存、证据统计），测试对标迁移 |
| ✅ 目标 | 证据覆盖率显式呈现，避免「无复用证据 = 无价值」误读 |
| 🚫 非目标 | 记忆写入 / CRUD / 生命周期（归 Mio memory_* 工具与 Memory Gateway 现有写路径） |
| 🚫 非目标 | 权威语义检索（归 `GET /api/v1/memory/query` 与 Mio `memory_query`） |
| 🚫 非目标 | 全图力导向布局、分页/limit（待浏览器 benchmark，本期不引入，沿用 mneme 决定） |
| 🚫 非目标 | mneme 原仓库的任何改动（原样保留） |

---

## 📦 范围

| # | 交付项 | 说明 |
|---|---|---|
| 1 | 后端投影模块 | `mio_taskhub/memory_observatory.py`：数据读取、清洗、公式、实体/关系聚合、mtime 缓存 |
| 2 | API 端点 | `GET /api/v1/memory/observatory/data?logs=` 挂现有 memory router |
| 3 | 前端 React 视图 | `MemoryObservatoryView` 进 Rail「记忆观测」，仅调新端点 |
| 4 | 测试迁移 | server.test.js 覆盖面 → pytest；全量回归 + 前端构建绿 |
| 5 | 文档链 | requirement/spec/api/plan 走 taskhub 门控并 approved |

---

## 📋 功能需求（FR）

| FR | 需求描述 | 优先级 | 验收要点 |
|---|---|---|---|
| FR-1 | `GET /api/v1/memory/observatory/data` 返回 `{entities, relations, meta}`；仅读 MIO_HOME 的 `memory.jsonl` 与 `experience_reuse.jsonl`；`meta={generatedAt, showLogs, skipped, counts, evidence}` | P0 | 结构与 mneme `/api/data` 一致；读取失败返回 `{error}` 不 500 整页；无任何写路径 |
| FR-2 | 观察视图：默认隐藏 log（`kind=note` 且 tags 含 `task-outcome`）；`?logs=1` 展开；env `MIO_OBSERVATORY_HIDE_LOGS` 置位时 `logs=1` 无效（强制隐藏） | P0 | 三种模式（默认/`logs=1`/env 强制）各自行为正确 |
| FR-3 | `reuse_score` 公式与 mneme `computeReuseScore` 一致：`5×复用 + 3×改善 + 非*-observer 源 +3 + 类型权重(decision3/context2/problem2/experience2/note0/未知0) + 决策词启发(/决定\|采用\|必须\|禁止\|架构\|原因\|约定/+1) + task-outcome 标签 −2`，下限 0 | P0 | 与 mneme 逐案一致：空证据、未知 kind、observer 源、决策词命中各至少 1 例断言 |
| FR-4 | 投影语义与 mneme 一致：kind 映射 `decision→rule`（context/problem/note/experience 同名，未知→`note`）；按行清洗 `[xxx] ` 前缀；标题取首句（≥4 字符优先）超 60 截断加 `…`；实体 `mio-<id>` / `mio-src-<source>` / `mio-proj-<project>`；关系 `来自`/`属于`；实体按出现顺序编号，重复 name 不重复入列，关系端点缺 id 丢弃 | P0 | 对照 mneme `parseMio` 用例全绿；重复/缺字段/脏行不崩溃 |
| FR-5 | 证据聚合：`experience_reuse.jsonl` 按 `experienceId` 聚合 `reuse`/`outcomeImproved`；`meta.evidence={memTotal, memWithEvidence, reuseRecords}` 对**全量**记忆统计（含被观察视图隐藏的 log） | P0 | memTotal 含 log；memWithEvidence 仅 `reuse>0`；无效行计入 skipped 不中断 |
| FR-6 | mtime 缓存：key = 两源文件 `(mtime, size)` + `showLogs`；命中不重算；任一文件变更自动失效 | P0 | 未变更二次请求不重读全文件；变更后数据更新；`showLogs` 不同 key 不串 |
| FR-7 | 前端 Rail「记忆观测」视图：卡片网格 + 类型筛选 + 观察视图默认/日志一键展开 + 本地过滤（明确标注非权威检索）+ 复用热度徽标 + 证据覆盖率头部 + 详情面板出入边（ego，不渲染全图）+ 提示词片段（复制/导出、带来源跳转、标注启发式）+ 项目/来源聚合面板 | P0 | 仅调 FR-1 端点，代码无 3456/Node 引用；5k 实体可交互；文案无「高价值/规则/权威」暗示 |
| FR-8 | 回归与质量：全量 pytest 只增不减；`npm run build` 绿；既有 memory 端点行为不变 | P0 | 本地全量测试与构建通过 |
| FR-9 | 文档链：requirement/spec/api/plan 全部 approved 且质量分 ≥80；代码/测试注释引用本表真实 FR-n；分支 `task-8a966293` | P0 | pre-push 钩子与 Read Evidence 门控通过 |
| FR-10 | mneme 隔离：迁移全程不修改 `E:\work\code\agent-dev\mneme`（含 git status 干净） | P0 | 完成时 mneme `git status` 干净 |

---

## ⚙️ 非功能需求（NFR）

| NFR | 描述 |
|---|---|
| NFR-1 | 零 Node 依赖：后端仅标准库 + 现有依赖；前端进现有 Vite/React 构建链 |
| NFR-2 | 性能：mtime 缓存命中不做全量重算；参照 mneme 基线（733 条冷构建 ~14ms / 5k ~51ms），Python 侧实测值记录进 spec/测试注释 |
| NFR-3 | 安全：仅读 MIO_HOME；输出 JSON 由 React 文本渲染天然转义，保留 XSS 回归意识；端点默认仅本机可达（hub 既有绑定策略） |
| NFR-4 | 诚实语义：UI 不出现「高价值/规则/权威检索」暗示；显式展示证据覆盖率与「无复用证据 ≠ 无价值」提示 |
| NFR-5 | 测试：新增 pytest 对标 server.test.js 覆盖面（投影/观察视图/公式/缓存/统计/端点）；全量基线不回退 |

---

## 🔗 依赖与约束

| 项 | 内容 |
|---|---|
| 上游 | mneme observatory-design.md（边界、5 能力规格、观察视图表）、mneme README（端点/配置/环境变量约定） |
| 下游 | Memory Gateway 现有 `/api/v1/memory/*` 端点共存不冲突；MioRuntimeView 同级并列视图 |
| 约束 | 只读边界：不写入、不裁决、不治理、不做权威检索 |
| 约束 | `MIO_HOME` 经 `mio_runtime.home()` 解析，与 hub 其他模块保持一致 |
| 约束 | 不新增外部服务依赖；分支 `task-8a966293`，push 前 spec/api/plan 必须 approved |

---

## ✅ 验收标准

1. 端点返回投影 JSON：默认观察视图排除 log、`?logs=1` 全量、env 强制隐藏生效，`meta.evidence` 证据覆盖率可用
2. `reuse_score` 公式与 mneme 一致（FR-3 各边界用例断言），kind 映射/清洗/标题语义一致
3. mtime 缓存命中不重算、变更自动失效、`showLogs` 不串
4. Rail 新增「记忆观测」视图，功能覆盖 FR-7 清单，仅调新端点
5. 新增 pytest 对标 server.test.js；全量 pytest 只增不减；`npm run build` 绿
6. 文档链 requirement/spec/api/plan approved 且质量 ≥80，分支 push 过 pre-push 门控
7. mneme 目录 git status 干净

---

## 📎 追溯

| FR | 来源（mneme） | 测试用例 |
|---|---|---|
| FR-1 | `buildData` + `GET /api/data` 端点 | `test_api_endpoint`、`test_only_reads_mio_home`、`test_relations_and_ids` |
| FR-2 | `parseMio(showLogs)` + `?logs=1` + `MNEME_HIDE_LOGS` | `test_default_view_hides_logs`、`test_logs_param_expands`、`test_env_force_hide_logs` |
| FR-3 | `computeReuseScore` | `test_reuse_score_formula` |
| FR-4 | `MIO_KIND_MAP` / `cleanMioContent` / `deriveMioTitle` / `parseMio` | `test_kind_map_clean_title`、`test_relations_and_ids` |
| FR-5 | `loadMioEvidence` + `stats` | `test_evidence_stats`、`test_bad_lines_skipped` |
| FR-6 | `fileSig` / `buildDataCached` | `test_mtime_cache` |
| FR-7 | hermes-graph.html 能力 + design §2.1–2.5 | `npm run build` + Rail 手工验收清单（`MemoryObservatoryView` 全区块；`buildAdjacency` 纯函数无 web 测试基建，记为验收观察项） |
| FR-8 | design §7-7 测试收口 | 全量 pytest（观测层 11 用例全绿；`test_spinning_guard` 4 失败为存量问题，基线同挂）+ `npm run build` |
| FR-9 | AGENTS.md 文档链门控 | `taskhub_doc_quality` / ratchet + pre-push 门控记录 |
| FR-10 | 用户裁决「mneme 保持不动」 | `git -C mneme status --short` 为空（Step 5 核对） |
