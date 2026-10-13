# 📋 记忆视图合并 + Memory Gateway 旧路径修复 需求规格

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | e4c3bc51 |
| 上游依据 | 用户裁决（2026-10-05）：「合并一个功能」「记忆也有问题」；真机实测旧路径 404 |
| 更新日期 | 2026-10-05 |

---

## 🔙 背景与问题

三个真实问题（均已证实）：

1. **双入口冗余**：任务 8a966293 把 mneme 观测层迁入后，Rail 出现「记忆」「记忆观测」两个相邻入口。用户裁决合并：一个「记忆」入口，内部 tab 化。
2. **Memory Gateway 前端 404（存量 bug）**：后端路由已迁移为 `/api/v1/memory/*`，前端仍调旧路径。实测（2026-10-05，hub 127.0.0.1:48620）：`GET /api/memory/health` → 404（`/api/v1/memory/health` → 200）、`GET /api/memory/query` → 404、`POST /api/memory/observer/ingest` → 失败。后果：「记忆」视图健康状态永远报错、关键词查询永远失败、ErrorBoundary 前端报错无法回传。
3. **一键打包必失败（存量 bug）**：`mio-taskhub.spec` 的 COLLECT `name="mio-taskhub-v3"`，而 `packaging/build.ps1` 的校验/复制/启动全写死 `dist\mio-taskhub` → 第 4 步校验 5 项全 MISSING 抛错，第 5（复制分发文件）/6（重启 hub）步永不执行。2026-10-05 重建 exe 时实测复现，绕过法为手动补步骤。

为什么现在做：合并是用户当面裁决；404 与打包失配均已在真机复现，修复路径明确、无后端行为变更，风险低。

---

## 📌 概述

「记忆」视图容器化为双 tab（记忆观测默认 + 记忆网关），Rail 收掉独立观测入口；前端 3 处旧路径改指 `/api/v1/memory/*`；`build.ps1` 产物路径与 spec 对齐；`api/memory.py` docstring v1 化。**纯前端重构 + 路径纠错 + 打包脚本修正 + 注释修正：无新端点、无后端行为变更。**

---

## 🎯 目标与非目标

| 类型 | 内容 |
|---|---|
| ✅ 目标 | 单一「记忆」入口 + 双 tab，默认「记忆观测」（用户裁决） |
| ✅ 目标 | 3 处旧路径修复，健康/查询/报错回传真机可用 |
| ✅ 目标 | `build.ps1` 一键打包全流程（校验/复制/启动）恢复可用 |
| 🚫 非目标 | 新端点或后端行为变更（后端 diff 仅限 docstring 注释） |
| 🚫 非目标 | Memory Gateway 功能增强（查询语法、健康指标扩展） |
| 🚫 非目标 | 观测视图内部功能改动（整体迁移，行为保持） |
| 🚫 非目标 | version bump / 发版（重建 exe 仅本机验证，不发 Release） |

---

## 📦 范围

| # | 交付项 | 说明 |
|---|---|---|
| 1 | 前端合并 | `MemoryView` 双 tab 容器；`Rail.jsx`/`App.jsx` 移除 observatory 入口 |
| 2 | 路径修复 | `api.js` / `MemoryView.jsx` / `App.jsx` 三处 → `/api/v1/memory/*` |
| 3 | 脚本修复 | `build.ps1` 产物路径 `dist\mio-taskhub-v3` |
| 4 | 注释修正 | `api/memory.py` docstring v1 化 |
| 5 | 测试与门控 | 新增回归 pytest（4 用例）、build/真机验证、文档链门控 |

---

## 📋 功能需求（FR）

| FR | 需求描述 | 优先级 | 验收要点 |
|---|---|---|---|
| FR-1 | Rail 仅保留「记忆」一个入口；「记忆」视图为双 tab 容器：tab「记忆观测」（**默认**，迁入 MemoryObservatoryView 全部内容）与 tab「记忆网关」（现有 存储状态/查询/调用统计/实时事件）；tab 样式沿用现有 `sjv__tab is-on` 模式 | P0 | 打开「记忆」默认见观测视图；切换网关 tab 各区块正常；Rail/CommandPalette 无「记忆观测」残留入口 |
| FR-2 | 修复 3 处旧路径：`api.js` memoryHealth、`MemoryView` 查询面板、`App.jsx` ErrorBoundary 上报，一律 `/api/v1/memory/*`；修复后 `web/src` 内不再出现字面 `/api/memory/` | P0 | 真机 health 200、查询出结果、上报成功；静态扫描 0 命中 |
| FR-3 | `api/memory.py` 顶部 docstring 端点清单与实际路由一致（`/api/v1/memory/*`），不再出现裸 `/api/memory/` 路径 | P1 | docstring 含 `/api/v1/memory/health`，不含裸 `/api/memory/` |
| FR-4 | `packaging/build.ps1` 产物路径与 `mio-taskhub.spec` COLLECT name（`mio-taskhub-v3`）一致：校验、复制分发文件、zip、启动均指向实际产物目录 | P0 | 一键打包第 4 步 5 项全 OK、第 5/6 步执行、hub 自动重启 |
| FR-5 | 回归：`npm run build` 绿；全量 pytest 只增不减（存量 `test_spinning_guard` 4 失败不计入）；观测功能迁移后与迁移前一致 | P0 | 构建成功；新增回归用例绿；手工核对观测区块清单（头部/类型筛选/本地过滤/详情出入边/片段/聚合） |
| FR-6 | 文档链：requirement/spec/api/plan 全 approved 且质量 ≥80；测试文档闭环 FR↔用例追溯；分支 `task-e4c3bc51`；pre-push 门控通过 | P0 | doc_quality 无 errors、追溯全覆盖、push 成功 |

---

## ⚙️ 非功能需求（NFR）

| NFR | 描述 |
|---|---|
| NFR-1 | 零后端行为变更：本任务后端 diff 仅限 `api/memory.py` 注释 |
| NFR-2 | 观测数据源与语义不变：仍仅调 `GET /api/v1/memory/observatory/data` |
| NFR-3 | 视觉一致性：tab 样式复用现有组件风格，不引入新前端依赖 |
| NFR-4 | 打包脚本与 spec 单一来源一致（唯一打包 spec 是根目录 `mio-taskhub.spec`） |

---

## 🔗 依赖与约束

| 项 | 内容 |
|---|---|
| 上游 | 任务 8a966293 交付（MemoryObservatoryView / Rail 入口 / 观测端点）；Memory Gateway 既有端点 |
| 下游 | 后续 rebuild exe / 发版流程复用修复后的 `build.ps1` |
| 约束 | 分支 `task-e4c3bc51`，push 前 spec/api/plan 必须 approved |
| 约束 | 验证期间 hub 实例（127.0.0.1:48620）会被 build.ps1 重启 |

---

## ✅ 验收标准

1. Rail 单入口 + 双 tab 默认观测，观测功能不回退
2. 三处 v1 路径真机可用（health 200 / 查询出结果 / 上报成功）
3. `npm run build` 绿；全量 pytest 只增不减；新增 4 个回归用例绿
4. `build.ps1` 一键打包校验通过并自动重启 hub
5. 文档链 approved、质量 ≥80、追溯闭环、pre-push 通过

---

## 📎 追溯

| FR | 来源（裁决/缺陷） | 测试用例 |
|---|---|---|
| FR-1 | 用户裁决「合并一个功能」 | `test_rail_single_memory_entry` + 手工 TC-6（双 tab 默认观测） |
| FR-2 | 真机实测旧路径 404 | `test_web_no_legacy_memory_paths` + 真机 TC-2（health/query/ingest） |
| FR-3 | docstring 与路由失配 | `test_api_memory_docstring_v1` |
| FR-4 | 2026-10-05 重建实测校验必失败 | `test_build_script_dist_matches_spec` + 一键打包实跑 TC-3 |
| FR-5 | 质量守恒 | 全量 pytest + `npm run build` + 观测区块手工核对 TC-4 |
| FR-6 | 仓库文档链门控 | doc_quality/追溯报告 + pre-push 记录 TC-5 |
