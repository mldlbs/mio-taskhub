# 🗓️ 想法落地闭环 P1 实现计划（包 B 假设关联）

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | 8442e38d |
| 需求 | docs/taskhub/requirement-idea-landing-p1.md（FR-11~FR-17） |
| 设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结·包 B） |
| 更新日期 | 2026-09-27 |

---

## 🎯 目标

实现 Idea ⇅ Mio 发酵假设的引用关联全链路（导入 → 展示 → 断链 → 人工回写）与降级契约（3s 超时 / 5min 缓存）。守则：**真源=Mio 只读拉取**、**回写必须人工确认走单条端点**、**跨服务失败只灰假设区**。分支命名 `task-8442e38d`。

---

## 📦 前置条件

| 项 | 状态 |
|---|---|
| requirement approved（100 分） | ✅ |
| spec approved（v1.2 冻结稿，P0 已批） | ✅ 随本计划确认 |
| P0 底座（cockpit 区块级降级、assumptions[hid] diff 键、build16） | ✅ |
| 测试基线 | 885 passed, 1 skipped |

---

## 🔨 交付步骤

### 步骤 ① `hypotheses` 引用字段 + 白名单（FR-11/NFR-1/3）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/models.py`、`mio_taskhub/migrations.py`、`mio_taskhub/api/ideas.py` |
| 要点 | `Idea` 增可空 JSON 列 `hypotheses`（list[str]，hyp id 引用）；迁移补列默认 NULL；POST/PATCH 白名单接受 `hypotheses`（值校验为字符串列表，否则 422）；`_idea_json` 输出；增删进 `IdeaChange` diff |
| 测试 | `tests/test_ideas_api.py` 扩：NULL 透传、非数组 422、增删进 diff |
| 完成定义 | 旧数据无异常，引用字段可读写可回放 |

### 步骤 ② 假设导入（FR-12）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/api/ideas.py`（或独立 import 路由） |
| 要点 | `POST /api/v1/ideas/{id}/hypotheses/import` body `{ids: [..]}`：**合并去重**写入（已关联不重复，幂等）；列表数据源复用 `GET /api/v1/mio/ferment`（已有 active 假设与分数）；Mio 不可用 → 503 明确报错（导入是显式动作，不静默） |
| 测试 | 导入幂等（同 ids 两发只留一份）、空列表、Mio 不可用 503 |
| 完成定义 | 导入端点幂等、错误语义明确 |

### 步骤 ③ cockpit 假设区真实聚合 + 降级契约（FR-13/FR-16/NFR-2）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/api/cockpit.py`（`_build_hypotheses`） |
| 要点 | 读 `idea.hypotheses` → 拉 Mio 分数三元组 + status；**超时 3s**（P0 `SECTION_TIMEOUTS["hypotheses"]=3.0` 已预留）；**结果缓存 5min**（进程内 TTL 缓存，key=idea_id 或 hyp id 集合，输出 `cached_at`）；Mio 失败 → `sections[hypotheses].status=degraded` + reason，其他区块不受影响；查不到的 id → 条目标 `broken: true`（供 FR-14 灰显）；分数只读不回写 |
| 测试 | `tests/test_idea_cockpit.py` 扩：分数透传、超时降级仅灰该区、缓存命中（5min 内不再跨服务调用）、断链标 broken |
| 完成定义 | 验收 1/2/6 通过 |

### 步骤 ④ 人工回写独立端点（FR-15/NFR-4）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/api/ideas.py` |
| 要点 | `PATCH /api/v1/ideas/{id}/assumptions/{hid}` body `{status, note, confirmed_by}`：只改单条 `assumptions[hid]`（不整列表覆盖）；复用 P0 diff 键 `assumptions[hid]` 进 `IdeaChange`；读-改-写在事务内保证并发两发都进 diff；hid 不在 → 404；body 校验失败 → 422；**无自动调用方**（仅端点暴露，前端带确认弹窗） |
| 测试 | 新建 `tests/test_idea_assumptions.py`：单条回写 diff、并发两发无丢更新、404/422、键格式断言 |
| 完成定义 | 验收 4/5 通过 |

### 步骤 ⑤ 前端：导入 / 展示 / 断链 / 回写（FR-12/13/14）

| 项 | 内容 |
|---|---|
| 文件 | `web/src/components/IdeasView.jsx`、`web/src/api.js`、`web/src/index.css` |
| 要点 | 假设区改真实渲染：三元分徽章 + 状态 chip + `broken` 灰显「已失效」+「解除关联」（PATCH hypotheses 移除）；「从发酵假设导入」按钮 → 勾选弹层（数据源 `mioFerment()`）→ 调 import 端点 → `reloadDetail()`；回写按钮带确认弹窗 → assumptions PATCH；degraded 灰条沿用 P0 样式 |
| 测试 | `npm run build` 绿；新旧想法渲染无回归 |
| 完成定义 | 导入/徽章/断链/回写四交互可用，构建绿 |

### 步骤 ⑥ API 文档 + 回归（FR-17/NFR-5）

| 项 | 内容 |
|---|---|
| 文件 | `docs/taskhub/8442e38d/api.md`（新，P1 端点契约） |
| 要点 | 契约含：hypotheses 字段、import、assumptions 单条回写、cockpit hypotheses 区结构（含 broken/cached_at）；过质量门（≥80 且 errors=0）→ approved |
| 测试 | 全量 pytest 只增不减（基线 885）；`npm run build`；pre-push 门控 spec/api/plan approved + FR-11~17 引用 |
| 完成定义 | 验收 7/8 通过，四文档全绿 |

---

## 🧪 测试与构建

| 项 | 命令 |
|---|---|
| 后端全量 | `.venv\Scripts\python.exe -m pytest`（基线 885 passed, 1 skipped，只增不减） |
| 前端构建 | `npm run build`（`web/`） |
| 打包 | `packaging\build.ps1 -Quick`（命令文本不得含仓库名字面） |
| 门控 | 提交前 `taskhub_doc_quality`；pre-push 需 spec/api/plan approved + FR 真实引用 |

---

## 🔗 FR 追溯

| 步骤 | FR |
|---|---|
| ① | FR-11 |
| ② | FR-12 |
| ③ | FR-13、FR-16 |
| ④ | FR-15 |
| ⑤ | FR-12、FR-13、FR-14 |
| ⑥ | FR-17（回归） |

---

## ⚠️ 风险与对策

| 风险 | 对策 |
|---|---|
| Mio 调用拖垮假设区 | 3s 超时 + 5min 缓存 + 区块级 degraded，验收 2 专测 |
| 并发回写丢更新 | 单条读-改-写进事务；验收 4 并发专测 |
| 分数被误回写 | 只读拉取，代码无回写调用方；测试断言 Mio 侧无写 |
| 导入列表与 Mio 双套数据漂移 | 引用只存 id、分数实时拉取；断链即标 broken 可解除 |
| 缓存陈旧 | TTL 5min + `cached_at` 透出；手动刷新走重开详情 |
