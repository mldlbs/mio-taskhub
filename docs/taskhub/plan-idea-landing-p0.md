# 🗓️ 想法落地闭环 P0 实现计划

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | 2fe56e6a |
| 需求 | docs/taskhub/requirement-idea-landing-p0.md（FR-1~FR-10） |
| 设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结，含 P0 实现注记附录） |
| 更新日期 | 2026-09-27 |

---

## 🎯 目标

按设计稿 6 步交付顺序实现 P0；守则：**字段模型一次定型**、**每区块独立降级**。分支命名 `task-2fe56e6a`。

---

## 📦 前置条件

| 项 | 状态 |
|---|---|
| requirement approved（100 分） | ✅ |
| spec approved（≥80） | 本计划批准前完成 |
| 讨论记录（design 门控） | ✅ 13469bbf |
| 测试基线 | 854 passed, 1 skipped |

---

## 🔨 交付步骤

### 步骤 ① Idea 字段 + POST/PATCH + IdeaChange diff（FR-1/FR-2/NFR-1/3）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/models.py`、`mio_taskhub/migrations.py`、`mio_taskhub/api/ideas.py` |
| 要点 | `Idea` +8 可空字段（goal/success_metric/constraints/out_of_scope/assumptions/risks/mvp_scope/tags，JSON 列）；迁移补列默认 NULL；POST/PATCH 接受白名单扩充；diff 序列化支持 `assumptions[hid]` 键；`_idea_json` 输出新字段 |
| 测试 | `tests/test_ideas_api.py` 扩：旧数据 NULL 透传、PATCH 进 IdeaChange、并发单条回写两条都进 diff |
| 完成定义 | 新建/编辑/列表/详情接口全绿，旧记录无异常 |

### 步骤 ② GET /cockpit（FR-3/FR-4/NFR-2）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/api/cockpit.py`（新）、`mio_taskhub/main.py`（挂路由） |
| 要点 | `sections` 字典每区 `{status, reason, cached_at}`；并行聚合 `gather(return_exceptions=True)`；区独立超时（Mio 3s/其余 1s）、总上限 5s；失败仅该区 degraded；返回 `next_action` 占位（步骤④接规则引擎） |
| 测试 | `tests/test_idea_cockpit.py`：结构骨架、注入单区超时仍 200 且他区 ok、总超时裁剪 |
| 完成定义 | 响应符合设计稿 JSON 示例，无整包 degraded 字段 |

### 步骤 ③ 驾驶舱只读分节渲染（FR-5）

| 项 | 内容 |
|---|---|
| 文件 | `web/src/components/IdeasView.jsx`、`web/src/api.js`、`web/src/index.css` |
| 要点 | 详情页改 8 区块顺序渲染（下一步动作置顶占位）；空字段灰显引导；degraded 区灰条 + reason 文案；空壳区块不报错 |
| 测试 | `npm run build` 通过；手动验收：新旧想法均正常渲染 |
| 完成定义 | 分节可见、降级灰条生效、构建绿 |

### 步骤 ④ 下一步动作引擎（FR-6/FR-7/NFR-4）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/next_action.py`（新）、`mio_taskhub/models.py`+迁移（`IdeaUserPref` 表）、`mio_taskhub/api/cockpit.py`、`web/src/components/IdeasView.jsx` |
| 要点 | 5 级默认序（可配置）；`condition_snapshot` 结构化布尔位；dismiss 服务端存、7 天过期、snapshot 变化立即复活；cockpit `next_action` 接规则引擎；前端 dismiss 按钮 + 复活即时重现 |
| 测试 | `tests/test_next_action.py`：多条件命中取最高序、dismiss 过期、snapshot 变化复活、优先级配置覆盖 |
| 完成定义 | 验收 3/4 通过 |

### 步骤 ⑤ tags + 高风险判定（FR-8）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/next_action.py`（或 `risk.py`）、词表常量 + env 读取 |
| 要点 | `tags ∩ 词表`（默认 高风险/合规/用户数据/花钱）非空即 high_risk；env `MIO_IDEA_RISK_TAGS` 覆盖；不做正文匹配；cockpit 输出 `high_risk` 供前端 |
| 测试 | 命中/未命中/空 tags/环境变量覆盖 4 例 |
| 完成定义 | 判定可配置、可复现 |

### 步骤 ⑥ 任务图 P0（FR-9）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/api/cockpit.py`（tasks 区）、`web/src/components/IdeasView.jsx`、`index.css` |
| 要点 | 关联任务 + 一层下游（谁依赖我）；环检测（DFS）→ 列表 + 警告条；>20 折叠展开；degraded 沿用步骤② |
| 测试 | 用例：正常下游、含环降级、21 任务折叠阈值 |
| 完成定义 | 验收 6 通过 |

---

## 🧪 测试与构建

| 项 | 命令 |
|---|---|
| 后端全量 | `.venv\Scripts\python.exe -m pytest`（基线 854 passed, 1 skipped，只增不减） |
| 前端构建 | `npm run build`（`web/`） |
| 打包 | `packaging\build.ps1 -Quick`（命令文本不得含仓库名字面） |
| 门控 | 提交前 `taskhub_doc_quality`；pre-push 需 spec/api/plan approved + FR 引用 |

---

## 🔗 FR 追溯

| 步骤 | FR |
|---|---|
| ① | FR-1、FR-2 |
| ② | FR-3、FR-4 |
| ③ | FR-5 |
| ④ | FR-6、FR-7 |
| ⑤ | FR-8 |
| ⑥ | FR-9 |
| 全程 | FR-10（回归） |

---

## ⚠️ 风险与对策

| 风险 | 对策 |
|---|---|
| 字段后补改表 | 步骤①一次定型 8 字段，评审已冻结 |
| Mio 拖垮 cockpit | 区块级超时 + 并行 + 总上限，验收 7 专测 |
| dismiss 频繁复活 | snapshot 只存结构化布尔位，不存文本 |
| 下游方向理解偏差 | 实现与测试均以「谁依赖我」为准（设计稿注记 #4） |
