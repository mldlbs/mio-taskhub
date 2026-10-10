# 📐 记忆视图合并 + 旧路径修复 技术规格（memory-merge）

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | e4c3bc51 |
| 上游需求 | docs/taskhub/requirement-memory-merge.md v1.0（FR-1~FR-6） |
| 上游依据 | web/src 现状（Rail/App/MemoryView/api.js）、mio-taskhub.spec、packaging/build.ps1 |
| 更新日期 | 2026-10-05 |

---

## 📌 概述

把「记忆观测」从独立 Rail 视图降为「记忆」视图内默认 tab，同时修复 Memory Gateway 前端旧路径 404 与一键打包产物路径失配。**零新端点、零后端行为变更**（后端 diff 仅 `api/memory.py` docstring 注释）。

---

## 📋 模块职责与边界

| 模块 | 职责（做什么） | 边界（不做什么） |
|---|---|---|
| `MemoryView.jsx`（改造后） | 「记忆」视图双 tab 容器：观测 tab + 网关 tab，默认观测 | 不做路由级跳转、不改两块内部逻辑 |
| `MemoryObservatoryView.jsx`（迁移） | 观测 tab 内容整体渲染（原样） | 不改数据源/功能/文案，仅随 tab 展示 |
| 网关 tab（MemoryView 原内容） | 存储状态/查询/调用统计/实时事件 | 不改查询语义与指标口径 |
| `api.js` / `App.jsx` 路径修复 | 3 处调用改指既有 `/api/v1/memory/*` | 不新增/改名任何端点 |
| `packaging/build.ps1` | 产物路径对齐 spec COLLECT name | 不改打包步骤顺序、不改 zip 命名策略 |
| `mio_taskhub/api/memory.py` | docstring 与真实路由一致 | 函数体零改动（NFR-1） |

---

## 🏗️ 架构设计

```
Rail（移除 observatory entry，仅 memory）
   │ view === 'memory'
   ▼
MemoryView（双 tab 容器，默认 activeTab='observatory'）
   ├─ tab「记忆观测」→ <MemoryObservatoryView />（原样迁入）
   │                      └─ GET /api/v1/memory/observatory/data（不变）
   └─ tab「记忆网关」→ 原 MemoryView 内容（状态/查询/调用统计/事件）
                          ├─ GET /api/v1/memory/health      ← 修复 api.js:145
                          ├─ GET /api/v1/memory/query        ← 修复查询面板
                          ├─ GET /api/v1/events（不变）
                          └─ POST /api/v1/memory/observer/ingest ← 修复 App.jsx:402
```

**关键设计决策：**

| 决策 | 选择 | 理由 |
|---|---|---|
| 合并形态 | 单容器双 tab（非路由跳转/非分节滚动） | 与 `ScheduledJobsView` 现有 `sjv__tab` 模式一致，零新依赖；用户裁决「一个功能」 |
| 迁移方式 | `MemoryObservatoryView` 组件整体作为 tab pane 渲染 | 任务 8a966293 验收过的功能原样保留，改动面最小 |
| 默认 tab | `observatory` | 用户裁决「记忆观测默认」 |
| 路径修复策略 | 改前端指向既有 v1 端点（**不加** legacy alias） | 后端契约已是 v1（api 契约文档 §17），加 alias 反而制造双路径 |
| build.ps1 修法 | 改 `$distDir` 为 `dist\mio-taskhub-v3`（一处变量，全脚本传导） | spec 是「唯一打包来源」（NFR-4），脚本适配 spec；其余行（校验/zip/启动）均引用 `$distDir`/`$internalDir` 派生变量 |
| observatory 分支处理 | `App.jsx` 中 `view === 'observatory'` 分支删除；Rail entry 删除 | CommandPalette 无 observatory 条目（已核实），无其他引用点 |

---

## 🔍 详细设计

### 1. `MemoryView.jsx` 容器化（FR-1）

```jsx
export default function MemoryView({ liveEvent }) {
  const [tab, setTab] = useState('observatory')   // 默认观测（用户裁决）
  ...
  return (
    <div className="memory-view">
      <div className="memory-tabs" role="tablist">
        <button className={`memory-tab${tab==='observatory' ? ' is-on' : ''}`} role="tab"
                onClick={() => setTab('observatory')}>记忆观测</button>
        <button className={`memory-tab${tab==='gateway' ? ' is-on' : ''}`} role="tab"
                onClick={() => setTab('gateway')}>记忆网关</button>
      </div>
      {tab === 'observatory' && <MemoryObservatoryView />}
      {tab === 'gateway' && (<> ... 原有 header/存储状态/查询/调用统计/实时事件 ... </>)}
    </div>
  )
}
```

- 原网关内容整体保留在同一组件内（条件渲染），`liveEvent`/`reload`/`metrics` 逻辑不动；
- 观测 tab 挂载时按其自身生命周期拉数据（组件自带 fetch + abort），切 tab 卸载即清，行为与独立视图一致；
- 样式：新增 `.memory-tab` / `.memory-tab.is-on`（`index.css`），视觉参数对齐 `sjv__tab`。

### 2. 入口收敛（FR-1）

- `Rail.jsx`：删除 `id: 'observatory'` entry（第 75-82 行），`memory` entry 位置不动；
- `App.jsx`：删除 `view === 'observatory' && <MemoryObservatoryView />` 分支（第 456-458 行）及 `MemoryObservatoryView` import（`MemoryView` 自行 import）。

### 3. 路径修复（FR-2）

| 文件 | 现状（404） | 改为 |
|---|---|---|
| `api.js:145` `memoryHealth` | `fetch('/api/memory/health')` | `fetch('/api/v1/memory/health')` |
| `MemoryView.jsx:77` 查询面板 | `fetch('/api/memory/query?…')` | `fetch('/api/v1/memory/query?…')` |
| `App.jsx:402` ErrorBoundary | `fetch('/api/memory/observer/ingest', …)` | `fetch('/api/v1/memory/observer/ingest', …)` |

- 修复后 `web/src` 全量扫描不含字面 `/api/memory/`（回归用例守住）；
- `App.jsx:253` 提示文案「可通过 /api/memory/record 等端点写入」同步改为 `/api/v1/memory/record`（文案一致性，计入 FR-2 静态扫描范围）。

### 4. docstring 修正（FR-3）

`api/memory.py` 模块 docstring 6 行端点清单加 `v1`（`/api/memory/…` → `/api/v1/memory/…`），并补 `observatory/data` 行已存在。**函数体零 diff。**

### 5. `build.ps1` 修复（FR-4）

| 行 | 现状 | 改为 |
|---|---|---|
| L69 | `$distDir = Join-Path $root 'dist\mio-taskhub'` | `… 'dist\mio-taskhub-v3'` |

- 校验（L74-79 用 `$distDir`/`$internalDir`）、复制（L100-106）、zip（L111 用 `$distDir` 打包源）、启动（L124）均随 `$distDir` 自动传导；
- zip 文件名 `mio-taskhub-win64.zip` 不含目录语义，保持不变；
- 修复后一键打包全流程无人工干预（含校验 5 项 OK、分发文件复制、hub 重启）。

---

## 🌐 接口设计

**本任务无新端点、无端点改名。** 前端修复后指向的既有契约（详见 `docs/taskhub/api-memory-merge.md`）：

| Method | Path | 用途 | 变化 |
|---|---|---|---|
| GET | `/api/v1/memory/health` | 网关健康（修复后前端唯一调用路径） | 仅前端指向修正 |
| GET | `/api/v1/memory/query` | 关键词/类型查询 | 仅前端指向修正 |
| POST | `/api/v1/memory/observer/ingest` | 前端报错回传 | 仅前端指向修正 |
| GET | `/api/v1/events?limit=` | 实时事件（memory_* 过滤） | 不变 |
| GET | `/metrics` | 调用统计 | 不变 |
| GET | `/api/v1/memory/observatory/data` | 观测投影（观测 tab） | 不变 |

旧路径 `/api/memory/*` **不提供兼容 alias**（后端从未在本版本暴露该前缀，前端属失配而非迁移遗留）。

---

## 🖥️ 前端设计

- Tab 结构：容器级两个 tab 按钮（`role="tablist"` / `role="tab"`），激活态 `.is-on`；
- 状态：`useState('observatory')`，无 URL 参数同步（hub 为单页内部视图，沿用现约定）；
- 观测 tab：`MemoryObservatoryView` 原样（FR-7 全区块：头部证据覆盖/日志开关/类型 chips/本地过滤/详情出入边/片段/聚合/错误重试）；
- 网关 tab：原 MemoryView 全部 section 原样（存储状态/查询记忆/调用统计/实时事件），5s 轮询逻辑保留但仅在网关 tab 挂载时生效（组件挂载即生效，条件渲染天然满足）；
- 无新依赖、无 dangerouslySetInnerHTML。

---

## 🧪 测试计划

**新增 `tests/test_memory_merge.py`（4 用例，静态回归守卫）：**

| 用例 | 覆盖 | 断言要点 |
|---|---|---|
| `test_web_no_legacy_memory_paths` | FR-2 | 递归扫描 `web/src/**/*.{js,jsx}` 无字面 `/api/memory/` |
| `test_api_memory_docstring_v1` | FR-3 | `api/memory.py` 含 `/api/v1/memory/health`，不含裸 `/api/memory/` 行 |
| `test_build_script_dist_matches_spec` | FR-4 | 解析 spec COLLECT `name` 与 build.ps1 `$distDir` 目录名一致 |
| `test_rail_single_memory_entry` | FR-1 | `Rail.jsx` 无 `id: 'observatory'`、`App.jsx` 无 `view === 'observatory'`、`MemoryView.jsx` 默认 `useState('observatory')` 且含两 tab 文案 |

**真机验证（手动/脚本 HTTP）：**

| 编号 | 步骤 | 预期 |
|---|---|---|
| TC-2 | `GET /api/v1/memory/health`、`/query?limit=3`、`POST /api/v1/memory/observer/ingest` | 全部 2xx，查询出结果 |
| TC-3 | `packaging/build.ps1 -Quick` 全流程 | 第 4 步 5 项 OK、复制执行、hub 自动重启 |
| TC-4 | Rail 打开「记忆」 | 默认观测 tab；切网关 tab 四区块正常；无「记忆观测」Rail 入口 |

**回归：** 全量 `pytest -q` 只增不减（存量 `test_spinning_guard` 4 失败豁免）；`npm run build` 绿。

---

## ⚡ 性能与容量

| 项 | 结论 |
|---|---|
| 合并对性能影响 | 零：tab 条件渲染，观测/网关不同时挂载，数据拉取时机与独立视图一致 |
| 路径修复 | 网络路径更短（少一次 404 往返），无新增请求 |
| 打包 | 仅路径字符串变化，产物内容与耗时不变 |

---

## ⚠️ 风险与对策

| 风险 | 对策 |
|---|---|
| tab 化后观测功能悄悄回退 | 观测组件零改动（diff 审查确认）；TC-4 按 FR-7 区块清单手工核对 |
| 网关 tab 的 5s 轮询与观测 tab 抢请求 | 轮询随网关 section 条件渲染，观测 tab 下不轮询（组件生命周期天然隔离） |
| build.ps1 改动影响发版 | 变量单点修改，zip 命名/步骤顺序不动；TC-3 实跑全流程验证 |
| 静态扫描用例误报（注释中的示例路径） | 扫描目标为字面 `/api/memory/`，修复后代码与注释统一 v1，无合法残留 |
| 前端无测试基建，tab 交互无自动化 | 用例覆盖静态结构（默认值/entry 移除），交互由 TC-4 手工清单兜底（与 8a966293 同口径） |

---

## ✅ 验收标准（映射 FR）

| FR | 验证方式 |
|---|---|
| FR-1 | `test_rail_single_memory_entry` + TC-4 手工（默认观测/网关四区块/无残留入口） |
| FR-2 | `test_web_no_legacy_memory_paths` + TC-2 真机三连（health/query/ingest） |
| FR-3 | `test_api_memory_docstring_v1` |
| FR-4 | `test_build_script_dist_matches_spec` + TC-3 一键打包实跑 |
| FR-5 | 全量 pytest + `npm run build` + TC-4 观测区块核对 |
| FR-6 | doc_quality / 追溯报告 / pre-push 记录（TC-5） |

---

## 📎 追溯

| FR | 本规格章节 | 测试用例 |
|---|---|---|
| FR-1 | 详细设计 1/2、前端设计 | `test_rail_single_memory_entry`、TC-4 |
| FR-2 | 详细设计 3 | `test_web_no_legacy_memory_paths`、TC-2 |
| FR-3 | 详细设计 4 | `test_api_memory_docstring_v1` |
| FR-4 | 详细设计 5 | `test_build_script_dist_matches_spec`、TC-3 |
| FR-5 | 测试计划-回归 | 全量 pytest + `npm run build` + TC-4 |
| FR-6 | （本文档链） | doc_quality + 追溯 + pre-push（TC-5） |
