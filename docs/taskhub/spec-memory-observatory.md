# 📐 mneme 观测层迁移 技术规格（memory-observatory）

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | 8a966293 |
| 上游需求 | docs/taskhub/requirement-memory-observatory.md v1.0（FR-1~FR-10） |
| 上游依据 | mneme/README.md、mneme/docs/observatory-design.md、mneme/hermes-server.js（commit 3ce2fbe） |
| 更新日期 | 2026-10-05 |

---

## 📌 概述

把 mneme（Node 只读记忆观测层）的功能迁入 mio-taskhub 记忆模块：后端纯 Python 投影模块 + 现有 memory router 新端点；前端观测 UI React 化进 Rail。**投影语义与 mneme 逐案一致**（公式/清洗/映射/统计/缓存契约），迁移不扩展边界。

mneme → mio-taskhub 对应关系：

| mneme | 迁移目标 | 说明 |
|---|---|---|
| `hermes-server.js` 投影逻辑（222 行） | `mio_taskhub/memory_observatory.py` | 纯 Python 重写，语义对齐 |
| `GET /api/data`（3456 端口） | `GET /api/v1/memory/observatory/data` | 挂现有 memory router |
| `hermes-graph.html`（995 行，React 化） | `web/src/components/MemoryObservatoryView.jsx` + Rail 注册 | 用户裁决：React 化进 Rail，非孤页 |
| `tests/server.test.js`（136 行） | `tests/test_memory_observatory.py` | 覆盖面对标迁移 |
| `MNEME_HIDE_LOGS` / `PORT` / `HOST` / `MNEME_CORS_ORIGIN` | 仅 `MIO_OBSERVATORY_HIDE_LOGS` | 端口/绑定/CORS 归 hub 既有策略，环境变量随进程消亡 |
| `start.bat`、README 快速开始 | 不迁移 | hub 自带启动方式 |

---

## 📋 模块职责与边界

| 模块 | 职责（做什么） | 边界（不做什么） |
|---|---|---|
| `mio_taskhub/memory_observatory.py` | 只读读取 MIO_HOME 两文件，投影为图谱 JSON（实体/关系/meta + reuse_score + 证据统计），mtime 缓存 | 不写任何文件、不碰 hub DB、不调 Mio MCP、不做权威检索/评分裁决/生命周期 |
| `GET /api/v1/memory/observatory/data` | 以只读端点暴露投影，判定 logs/env 视图开关 | 不提供 POST/写方法、不做鉴权扩展（hub 既有策略）、与 Memory Gateway 现有端点互不干扰 |
| `MemoryObservatoryView`（Rail「记忆观测」） | 呈现观察视图卡片网格、类型筛选、本地过滤、复用热度、出入边 ego、提示词片段、项目/来源聚合 | 不做全图力导向、不写回记忆、不提供权威检索入口（本地过滤明确标注非权威）、不引入新前端依赖 |
| 与 `memory_store.py` / Memory Gateway 的边界 | 共存于记忆模块：Gateway 管 hub 本地库读写，观测层管 MIO_HOME 只读投影 | 两者数据源与写路径不交叉，不互相 import 写逻辑 |

---

## 🏗️ 架构设计

```
MIO_HOME（Mio 唯一 Source of Truth，只读）
 ├─ memory.jsonl            (id/timestamp/kind/content/tags/project/source/scope)
 └─ experience_reuse.jsonl  (experienceId/reuse/outcomeImproved/...)
        │  mtime 缓存
        ▼
mio_taskhub/memory_observatory.py     ← 纯函数 + 模块级缓存（threading.Lock）
        │  GET /api/v1/memory/observatory/data?logs=
        ▼
mio_taskhub/api/memory.py（现有 router，prefix /api/v1/memory）
        │  fetch（同源）
        ▼
web/src/components/MemoryObservatoryView.jsx → Rail「记忆观测」
（与 MioRuntimeView 并列；不依赖 Node / 3456 / 任何 mneme 进程）
```

**关键设计决策**：

| 决策 | 选择 | 理由 |
|---|---|---|
| 模块归属 | 独立 `memory_observatory.py`，**不并入** `memory_store.py` | memory_store 是 hub 本地知识库（record/query 写路径），数据源与语义不同（MIO_HOME 只读投影）；混入违背单一职责 |
| 端点挂载 | 加进现有 `api/memory.py` 的 router | prefix 一致（`/api/v1/memory`），不新开 router/前缀，OpenAPI 聚合自然 |
| 缓存并发 | 模块级 `_cache` + `threading.Lock` + 双检 | mneme 单线程 Node 无此问题；FastAPI sync 端点跑线程池，必须防并发重建 |
| 错误契约 | `{error}` JSONResponse 直出 | 与 mneme `/api/data` 失败契约逐字一致（不用 HTTPException 的 `detail` 包装） |
| 前端形态 | 独立 React 组件进 Rail（用户裁决） | 与 MioRuntimeView 同级；不走「原 HTML 直迁」方案 |
| 环境变量 | `MIO_OBSERVATORY_HIDE_LOGS`（重命名） | 归属 mio-taskhub 命名空间；`MIO_HOME` 经 `mio_runtime.home()` 与 hub 一致 |

---

## 🔍 详细设计

### 后端模块 `mio_taskhub/memory_observatory.py`

#### 常量

```python
MIO_KIND_MAP = {"decision": "rule", "context": "context", "problem": "problem",
                "note": "note", "experience": "experience"}   # 未知 kind → "note"
KIND_WEIGHTS = {"decision": 3, "context": 2, "problem": 2, "experience": 2, "note": 0}
DECISION_WORDS_RE = r"决定|采用|必须|禁止|架构|原因|约定"      # 命中 +1
LOG_TAG = "task-outcome"                                       # 小写比较
```

#### 函数（与 mneme 一一对应）

| 函数 | 对应 mneme | 行为 |
|---|---|---|
| `data_paths()` | `dataPaths` | `MIO_HOME = mio_runtime.home()`；返回 `mioMemFile`/`mioReuseFile` 路径 |
| `read_jsonl(path, bucket, skipped)` | `readJsonl` | 逐行 JSON 解析；空行跳过；坏行 `skipped[bucket]+=1` 不中断 |
| `clean_mio_content(text)` | `cleanMioContent` | 按行去 `^\[[^\]]*\]\s*` 前缀，去空行 |
| `derive_mio_title(lines)` | `deriveMioTitle` | 首个 ≥4 字符行（无则第一行），>60 截断 + `…` |
| `load_evidence(paths)` | `loadMioEvidence` | 按 `experienceId` 聚合 `{reuse, improved}`；返回 `(map, records)` |
| `compute_reuse_score(mem, tags, ev)` | `computeReuseScore` | `5×reuse + 3×improved + (0 if source endswith '-observer' else 3) + KIND_WEIGHTS[kind](-0 for 未知) + (1 if decision words in content) - (2 if LOG_TAG in tags.lower) `，`max(0, …)` |
| `parse_mio(paths, show_logs)` | `parseMio` | 逐条：`memTotal+=1`（**先于 log 过滤**）→ `isLog = (kind=='note' and LOG_TAG in tags)` → `isLog and not show_logs` 则跳过实体但已计数；实体 `mio-<id>`/`mio-src-<source>`/`mio-proj-<project>`（重复 name 去重）；关系 `来自`(→source)/`属于`(→project)；返回 `stats={memTotal, memWithEvidence, reuseRecords}` |
| `build_data(show_logs)` | `buildData` | name→顺序 id 编号；关系端点缺 id 丢弃；组装 `meta={generatedAt, showLogs, skipped, counts, evidence}` |
| `build_data_cached(show_logs)` | `buildDataCached` | `key = f"{sig(mem)}|{sig(reuse)}|{show_logs}"`，`sig=(mtime_ns, size)`；`Lock` 内双检命中直返；重建前 `_skipped` 清零 |
| `_reset_cache()` / `_get_skipped()` | `_resetCache`/`_getSkipped` | 测试注入点 |

#### 行为细则（对齐 mneme 边角）

1. `memory.jsonl` 不存在 → 返回空投影（`entities/relations=[]`，stats 全 0），**不报错**；
2. `show_logs`：默认 `False`（观察视图）；`logs=1` → `True`；env `MIO_OBSERVATORY_HIDE_LOGS` 为真时**强制 False**（`logs=1` 无效），键值在端点层判定，`build_data` 不感知 env；
3. `meta.skipped` 每次重建清零重算（mneme 行为）；
4. 单元格读全文件（与 mneme 相同；733→50k 条实测见性能节），缓存命中不重读；
5. 模块**零写入**：不 import 写路径、不触碰 hub DB、不调用 Mio MCP（FR-10/边界）。

### 涉及文件清单

| 动作 | 文件 | 说明 |
|---|---|---|
| 新增 | `mio_taskhub/memory_observatory.py` | 投影模块（函数/缓存/常量） |
| 新增 | `tests/test_memory_observatory.py` | 对标 server.test.js 的 pytest |
| 新增 | `web/src/components/MemoryObservatoryView.jsx` | Rail「记忆观测」视图 |
| 新增 | `web/src/lib/observatoryAdjacency.js` | 邻接表纯函数（ego view 数据源） |
| 修改 | `mio_taskhub/api/memory.py` | +1 GET 端点（现有 router 内） |
| 修改 | `web/src`（Rail 注册表，实施时定位） | 新增视图入口「记忆观测」 |
| 修改 | `README.md` / `CHANGELOG.md` | 端点与视图说明、`MIO_OBSERVATORY_HIDE_LOGS` |
| 不动 | `mio_taskhub/memory_store.py`、Memory Gateway 既有端点、`E:\work\code\agent-dev\mneme\*` | FR-8/FR-10 |

### 请求时序

```
Browser                api/memory.py             memory_observatory.py         MIO_HOME
   │ GET .../observatory/data?logs=1                   │                             │
   │──────────────────────────────────────────────────▶│                             │
   │                    │ 判定 env MIO_OBSERVATORY_HIDE_LOGS → show_logs             │
   │                    │ build_data_cached(show_logs) ────────────────────────────▶│
   │                    │            │ key=两文件 sig|show_logs 命中? ── 命中 → 直接返回
   │                    │            │ 未命中 → Lock → 重读两文件 → parse_mio → 编号 → meta
   │ 200 {entities,relations,meta} ◀───────────────────│◀────────────────────────────│
   │ useMemo buildAdjacency(relations)（O(E) 单次）     │                             │
   │ 渲染卡片网格 + 详情 ego + 聚合 + 片段               │                             │
```

---

## 🌐 接口设计：`GET /api/v1/memory/observatory/data`

| 项 | 定义 |
|---|---|
| 方法/路径 | `GET /api/v1/memory/observatory/data` |
| 查询参数 | `logs`（bool，默认 false；FastAPI 接受 `logs=1/true/yes`）；env `MIO_OBSERVATORY_HIDE_LOGS` 真时忽略该参数 |
| 副作用 | 无（只读两源文件 + 进程内缓存） |

**200 响应**（与 mneme `/api/data` 契约一致）：

```json
{
  "entities": [
    {"id": 0, "name": "mio-abc123", "type": "rule",
     "obs": ["来源 mcp · 项目 agent-dev · decision #架构", "第一行清洗后内容", "..."],
     "title": "标题（首句 ≤60 字）", "reuse_score": 8}
  ],
  "relations": [{"source": 0, "target": 1, "rel": "来自"}],
  "meta": {
    "generatedAt": "2026-10-05T07:20:00.000Z",
    "showLogs": false,
    "skipped": {"mio": 0, "reuse": 0},
    "counts": {"entities": 45, "relations": 60},
    "evidence": {"memTotal": 733, "memWithEvidence": 20, "reuseRecords": 20}
  }
}
```

- `entities[].type` 枚举：`rule|context|problem|note|experience|log|source|project`
- `entities[].relations` 不内嵌；前端由 `relations` 索引构建邻接表（ego view）
- **500**：`{"error": "<message>"}`（JSONResponse 直出，`Content-Type: application/json; charset=utf-8`，与 mneme 一致）

**注册**：`api/memory.py` 内新增端点函数，`from mio_taskhub import memory_observatory as obs`，复用现有 `router`；不新增 CORS/鉴权/限流（hub 既有策略）。

---

## 🖥️ 前端设计：`MemoryObservatoryView`（Rail「记忆观测」）

**位置与注册**：`web/src/components/MemoryObservatoryView.jsx`；Rail 注册点与 `MioRuntimeView` 同级（实施时定位现有注册表新增条目，命名「记忆观测」）。

### 状态与数据流

```jsx
const [data, setData] = useState(null);        // 投影 JSON
const [showLogs, setShowLogs] = useState(false);
const [typeFilter, setTypeFilter] = useState('all');   // rule/context/problem/experience/note/log/source/project
const [query, setQuery] = useState('');         // 本地过滤（仅已加载数据）
const [selectedId, setSelectedId] = useState(null);
const [aggTab, setAggTab] = useState('project'); // 聚合面板 project|source
const [fragTab, setFragTab] = useState(false);   // 提示词片段展开
// fetch(`/api/v1/memory/observatory/data${showLogs ? '?logs=1' : ''}`)
// useEffect 依赖 [showLogs] + 手动刷新；卸载 abort；错误 → 错误条 + 重试（不白屏）
const adj = useMemo(() => buildAdjacency(data?.relations ?? []), [data]); // O(E) 单次
```

### 布局（自上而下）

1. **头部**：标题「记忆观测」+ 证据覆盖率 `复用证据 {memWithEvidence}/{memTotal}（无复用证据 ≠ 无价值）` + `日志 开/关`（对应 `showLogs`）+ 刷新按钮 + `reuseRecords` 次复用记录提示；
2. **工具条**：类型 chips（含各类型计数）+ 本地过滤输入（placeholder「本地过滤（仅当前已加载数据）」，右侧标注「非权威检索」）；
3. **主体**：左侧卡片网格 + 右侧详情面板（选中时）；
   - 卡片：`title`、类型徽标（`rule` 决策/`context` 上下文/`problem` 问题/`experience` 经验/`note` 笔记/`log` 日志/`source` 来源/`project` 项目，色阶区分）、复用热度徽标（`复用高/中/低`，**阈值取 hermes-graph.html 现值**，实施时核对）、hover 显示 `obs[0]` 摘要；
   - 详情面板：meta 行（来源/项目/kind/#tags）→ obs 正文行 → **出入边**（`来自`/`属于` 关系列表，点击跳转对应实体，即 ego view，不渲染全图）；
4. **聚合面板**（tab：按项目/按来源）：每个项目/来源聚合 记忆数、decision(`rule`) 数、高复用数（`reuse_score` ≥ 徽标高阈值）；**最近活动时间不做**（投影无 per-entity timestamp，列入非目标）；
5. **提示词片段**（折叠区）：筛选 `rule`/`experience` 且按 `reuse_score` 降序 → 每条渲染 title + 首行内容 + 来源（`mio-<id>` 可点击跳详情）+ 徽标 → 复制 Markdown / 下载 `.md`；区头标注「启发式片段，非权威规则（约 30% 可能非真规范）」。

### 交互与安全

- 本地过滤：`title/obs` 子串大小写不敏感匹配（等价 mneme `includes`）；命中行高亮可选；
- 全部文本 React 默认转义，**禁用** `dangerouslySetInnerHTML`（保 mneme XSS 回归语义）；
- 文案约束（NFR-4）：不出现「高价值/规则提取/权威检索」措辞，搜索框一律「本地过滤」；
- 邻接表 `buildAdjacency` 提为纯函数（`web/src/lib/observatoryAdjacency.js`），供组件复用且可单测；
- Rail 内嵌不引入新依赖（不加 d3/cytoscape；卡片网格 + 列表即满足 FR-7，全图力导向属非目标）。

---

## 🧪 测试计划

**`tests/test_memory_observatory.py`**（对标 mneme `tests/server.test.js` 136 行覆盖面）：

| 用例 | 对标 | 断言要点 |
|---|---|---|
| `test_only_reads_mio_home` | 只投影 MIO_HOME | monkeypatch `MIO_HOME=tmp`，结果仅来自两文件，无 legacy 数据源 |
| `test_default_view_hides_logs` | 默认观察视图 | `kind=note+task-outcome` 实体不出现；`meta.showLogs=false`；`memTotal` 仍计 |
| `test_logs_param_expands` | `?logs=1` | log 实体出现，`showLogs=true` |
| `test_env_force_hides_logs` | `MNEME_HIDE_LOGS` | `MIO_OBSERVATORY_HIDE_LOGS=1` + `logs=1` → 仍隐藏 |
| `test_reuse_score_formula` | computeReuseScore | 空证据=基准分；observer 源无 +3；各 kind 权重；决策词 +1；task-outcome −2；下限 0（各 ≥1 断言） |
| `test_kind_map_clean_title` | MIO_KIND_MAP/clean/title | `decision→rule`、未知→`note`、`[agent]` 前缀剥离、>60 截断 `…` |
| `test_relations_and_ids` | buildData 编号 | `来自`/`属于`、顺序编号、重复 name 单条、关系端点缺 id 丢弃 |
| `test_evidence_stats` | loadMioEvidence/stats | memTotal 含 log；memWithEvidence 仅 reuse>0；reuseRecords 计数 |
| `test_bad_lines_skipped` | readJsonl | 坏行进 `skipped` 不抛异常 |
| `test_mtime_cache` | fileSig/buildDataCached | 二次调用不重读（monkeypatch 计数）；touch 文件后重建；`showLogs` 不同 key 不串 |
| `test_api_endpoint` | /api/data 端点 | TestClient 200、schema keys 齐、`logs=1`、异常注入 → 500 `{error}` |

- 前端：`npm run build` 必绿；`buildAdjacency` 纯函数如 web 端测试基建存在则加用例（实施时确认，不存在则记录为验收观察项）；
- 回归：全量 `pytest` 只增不减；既有 `/api/v1/memory/*` 端点用例不动。

---

## ⚡ 性能与容量

| 记忆量 | mneme Node 基线（冷/缓存命中/载荷） | Python 目标 |
|---|---|---|
| 733 | 14ms / 0.3ms / 0.2MB | 冷 <50ms、命中 <5ms，实施时实测记录进测试注释 |
| 5,000 | 51ms / 0.5ms / 1.3MB | 冷 <200ms、命中 <5ms |
| 20,000 | 207ms / 0.6ms / 5.4MB | 仅记录，无硬门槛 |

- 默认观察视图已裁剪 log（733→约 45 实体），载荷与 DOM 数量级下降；
- 分页/limit **不引入**（沿用 mneme 决定，待浏览器 benchmark）；
- 前端 5k 实体可交互的保障 = 默认视图裁剪 + 邻接表 O(E) 单次 + 网格渲染。

---

## ⚠️ 风险与对策

| 风险 | 对策 |
|---|---|
| Python 投影慢于 Node | mtime 缓存兜底（命中不重读）；冷构建实测记录，超目标再优化（预读/行缓存） |
| FastAPI 线程池并发重建缓存 | `threading.Lock` + 双检；测试注入并发调用（可选） |
| 995 行 HTML React 化语义走样 | 以 requirement FR-7 清单 + README 能力表逐项验收；`reuse_score` 阈值等原实现细节从 hermes-graph.html 核对 |
| 与 MioRuntimeView 概念混淆 | 视图命名「记忆观测」，头部注明「只读观测 · Mio 为唯一数据源」 |
| 双视图数据口径混淆（hub 本地 memory vs Mio 记忆） | 观测视图数据源仅 MIO_HOME；Memory Gateway 端点不动 |
| env 名变更导致原配置失效 | 原 `MNEME_*` 属 mneme 进程，不迁移；新名写进 README/CHANGELOG |

---

## ✅ 验收标准（映射 FR）

| FR | 验证方式 |
|---|---|
| FR-1 | `test_api_endpoint` + 响应 schema keys 断言 |
| FR-2 | 三视图用例（默认/logs=1/env 强制） |
| FR-3 | `test_reuse_score_formula` 各边界 |
| FR-4 | `test_kind_map_clean_title` + `test_relations_and_ids` |
| FR-5 | `test_evidence_stats` + `test_bad_lines_skipped` |
| FR-6 | `test_mtime_cache` |
| FR-7 | 前端构建 + 手工验收清单（Rail 打开/默认视图/日志展开/类型筛选/本地过滤/详情出入边/片段导出跳转/聚合面板/无 3456 引用 grep） |
| FR-8 | 全量 pytest + `npm run build` |
| FR-9 | doc_quality/ratchet + pre-push 门控记录 |
| FR-10 | 完成时 `git -C mneme status` 干净 |

---

## 📎 追溯

| FR | 本规格章节 | 测试用例 |
|---|---|---|
| FR-1 | 接口设计 | `test_api_endpoint`、`test_only_reads_mio_home`、`test_relations_and_ids` |
| FR-2 | 后端行为细则 2 | `test_default_view_hides_logs`、`test_logs_param_expands`、`test_env_force_hide_logs` |
| FR-3 | 后端函数表 compute_reuse_score | `test_reuse_score_formula` |
| FR-4 | 后端函数表 parse_mio/常量 | `test_kind_map_clean_title`、`test_relations_and_ids` |
| FR-5 | 后端函数表 load_evidence/parse_mio | `test_evidence_stats`、`test_bad_lines_skipped` |
| FR-6 | 后端函数表 build_data_cached | `test_mtime_cache` |
| FR-7 | 前端设计 | `npm run build` + Rail 手工验收清单（buildAdjacency 纯函数，web 无测试基建 → 验收观察项） |
| FR-8 | 测试计划-回归 | 全量 pytest（观测层 11 用例全绿）+ `npm run build` 绿 |
| FR-9 | （本文档链） | `taskhub_doc_quality` 无 errors + 棘轮不回退 + pre-push 通过 |
| FR-10 | 概述-对应关系 | `git -C mneme status --short` 为空 |
