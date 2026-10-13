# 🧪 测试与验收报告 · memory-merge（记忆视图合并 + 404 + 打包修复）

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 任务 | e4c3bc51 |
| 分支 | task-e4c3bc51 |
| 执行日期 | 2026-10-05 |
| 执行环境 | Windows 10 / PowerShell 5.1 / hub 127.0.0.1:48620（dist\mio-taskhub-v3\mio-taskhub.exe） |
| 总体结果 | **PASS**（4 新增用例绿、全量回归只增不减、构建绿、一键打包全流程绿、真机与浏览器点验全绿） |

---

## ✅ 验收标准（对照需求）

| # | 验收标准（requirement） | 结果 |
|---|---|---|
| 1 | Rail 单入口 + 双 tab 默认观测，观测功能不回退 | PASS（TC-9 十二项全过） |
| 2 | 三处 v1 路径真机可用（health/query/ingest） | PASS（TC-8 全 200） |
| 3 | `npm run build` 绿；全量 pytest 只增不减；新增 4 用例绿 | PASS（TC-4/TC-5/TC-6） |
| 4 | `build.ps1` 一键打包校验通过并自动重启 hub | PASS（TC-7 五项 OK） |
| 5 | 文档链 approved、质量 ≥80、追溯闭环、pre-push 通过 | PASS（TC-10/TC-11） |

---

## 🧭 测试范围

**范围内**：FR-1 双 tab 单入口结构、FR-2 前端旧路径清零 + 三端点真机、FR-3 docstring 一致性、FR-4 打包脚本与 spec 对齐（静态 + 实跑）、FR-5 全量回归与前端构建、FR-6 文档链门控与推送。

**范围外（豁免/非目标）**：存量 `tests/test_spinning_guard.py` 4 个失败（`_should_skip_webhook_generation` 已从源码移除，stash 基线同样失败，与本任务无关）；观测视图内部功能增强（组件零改动）；`/api/memory/*` 兼容 alias（契约明确不提供）；version bump / 发版。

---

## 📋 用例清单

| 用例 | 标题 | FR | 类型 | 步骤 / 断言 | 预期 | 结果 |
|---|---|---|---|---|---|---|
| TC-1 | 前端无旧路径 | FR-2 | pytest 静态 | `tests/test_memory_merge.py::test_web_no_legacy_memory_paths` 扫描 `web/src/**/*.{js,jsx}` | 0 处字面 `/api/memory/` | PASS |
| TC-2 | docstring 与路由一致 | FR-3 | pytest 静态 | `test_api_memory_docstring_v1`：含 `/api/v1/memory/health`、docstring 无裸旧路径行 | 断言全过 | PASS |
| TC-3 | 脚本与 spec 对齐 | FR-4 | pytest 静态 | `test_build_script_dist_matches_spec`：解析 spec `COLLECT name` vs build.ps1 `$distDir` | 均为 `mio-taskhub-v3` | PASS |
| TC-4 | 单入口默认观测（静态） | FR-1 | pytest 静态 | `test_rail_single_memory_entry`：Rail 无 `id:'observatory'`、App 无分支/引用、MemoryView 默认 observatory + 双 tab 文案 | 断言全过 | PASS（4/4，0.49s） |
| TC-5 | 全量回归 | FR-5 | pytest 全量 | `.venv\Scripts\python.exe -m pytest tests -q` | 只增不减 | PASS：**1129 passed / 1 skipped / 4 deselected**，仅存量 spinning_guard 4 failed（豁免） |
| TC-6 | 前端构建 | FR-5 | 构建 | `web` 下 `npm run build`（vite 5.4.21，272 modules） | 退出码 0 | PASS（2.18s，chunk>500kB 为既有告警） |
| TC-7 | 一键打包全流程 | FR-4 | 实跑 | `packaging\build.ps1 -Quick` | 第 4 步 5 项 OK、第 5 步复制、hub 自动重启 | PASS：PIL/_imaging/index.html/assets/exe 五项 OK，分发文件复制，hub 重启 **PID 27096**，产物 `dist\mio-taskhub-v3` |
| TC-8 | 三端点真机 + 旧路径 | FR-2 | HTTP | GET health、GET query、POST ingest、GET 旧路径、GET observatory | 前三 200、旧路径 404、观测 200 | PASS：health 200 / query 200（出结果）/ ingest 200 `{"ok":true}` / `/api/memory/health` **404（无 alias 符合契约）** / observatory 200 |
| TC-9 | 浏览器点验双 tab | FR-1 | Playwright | 打开 hub → 点 Rail「记忆」→ 断言默认 tab/观测渲染/网关四区块/无 health 报错/查询面板非 404/切回观测；另断言 Rail 无「记忆观测」入口 | 12 项断言全过 | PASS：**ALL PASS**（截图 `tc4-memory-merge.png`；查询面板返回真实结果 `mem-note-…`） |
| TC-10 | 文档链质量与追溯 | FR-6 | taskhub | `taskhub_doc_quality`：requirement/spec/api/plan 分数、errors、FR↔测试追溯 | 四文档 ≥80、0 errors、FR 覆盖 6/6 | PASS：四文档均分 100、0 errors、追溯 6/6 |
| TC-11 | pre-push 门控 | FR-6 | git | `git push origin task-e4c3bc51`（不加 `--no-verify`） | spec/api/plan approved + diff FR-n 真实存在于已批准 requirement | PASS（push 成功，钩子无拦截） |

**观测功能不回退核对（FR-5 手工清单，随 TC-9）**：头部证据覆盖 / 日志开关 / 类型 chips / 本地过滤 / 详情出入边 / 提示词片段 / 聚合面板 / 错误重试——`MemoryObservatoryView` 组件零改动（diff 仅容器接入），浏览器断言观测视图与标题正常渲染。

---

## 🔎 FR ↔ 用例追溯

| FR | 用例 |
|---|---|
| FR-1 | TC-4、TC-9 |
| FR-2 | TC-1、TC-8 |
| FR-3 | TC-2 |
| FR-4 | TC-3、TC-7 |
| FR-5 | TC-5、TC-6、TC-9 |
| FR-6 | TC-10、TC-11 |

---

## 📝 缺陷与豁免

| 项 | 说明 | 处置 |
|---|---|---|
| `tests/test_spinning_guard.py` 4 failed | `_should_skip_webhook_generation` 已从 `CronEngine` 移除（历史遗留） | 豁免，不计入本任务回归 |
| vite chunk 1,433 kB 告警 | 既有打包体积告警 | 不在本任务范围 |
| 静态测试注释中的路径示例 | 全部统一为 `/api/v1/memory/*`，无合法旧路径残留 | 已由 TC-1 守护 |

## ✍️ 修订记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-05 | v1.0 | 初版：TC-1~TC-11 执行记录与 FR 追溯闭环 |
