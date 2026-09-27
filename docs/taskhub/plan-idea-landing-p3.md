# 🗓️ 想法落地闭环 P3 实现计划（包 D 拓扑 + 复盘 + 关联表）

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | d4bdf530 |
| 需求 | docs/taskhub/requirement-idea-landing-p3.md（FR-26~FR-29） |
| 设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结·包 D）+ 讨论 3c3b4c90 |
| 更新日期 | 2026-09-27 |

---

## 🎯 目标

实现任务拓扑完整版（多层上下游闭包 + 环路径 + 节点上限）、复盘区真实聚合（run 成败 + P2 评审记录）、假设关联表（建表 + 幂等迁移 + 双写兼容）、成功标准结构化「本期不纳入」裁决记录。守则：**既有键只增不改**、**P1 端点签名与响应逐键不变**、**单区降级不拖垮整包**。分支命名 `task-d4bdf530`。

---

## 📦 前置条件

| 项 | 状态 |
|---|---|
| requirement approved（100 分，FR-26~29） | ✅ |
| spec approved（v1.2 冻结稿，P0 已批） | ✅ |
| 设计阶段讨论结论 | ✅ 3c3b4c90 |
| P0/P1/P2 底座（区块机制、Run/Discussion 数据源、P1 端点） | ✅ |
| 测试基线 | **915 passed, 1 skipped** |

---

## 🔨 交付步骤

### 步骤 ① 任务拓扑完整版（FR-26/NFR-2）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/api/cockpit.py`（`_build_tasks`、`_has_cycle` 改造） |
| 要点 | 多层 BFS：`idea_id` 直接任务为根；上游=`depends_on` 传递闭包、下游=反向边传递闭包；全表一次 SELECT 后内存构图（沿用现状）；节点上限 **100**，超限截断置 `truncated: true`；`graph.nodes[].kind` 扩 `direct\|upstream\|downstream`；环检测改回传 `cycles: list[list[str]]`（DFS 记录栈取环段），`has_cycle` 布尔保留，含环仍 `graph: null` + warning + cycles（P0 降级不回退）；`folded`>20 语义不变；`data` **只增键**（新增 `truncated`/`cycles`/`upstream_total` 等） |
| 测试 | 新 `tests/test_idea_topology_p3.py`：三层链全入图且 kind 正确（FR-26）、环返回非空 cycles 且降级警告照旧/无环 cycles=[]（FR-26）、>100 截断 truncated=true 且 folded 保留（FR-26） |
| 完成定义 | 验收 1/2/3 通过 |

### 步骤 ② 假设关联表 + 迁移 + 双写（FR-28/NFR-1/4）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/models.py`（新表 `IdeaAssumptionLink`：唯一键 `(idea_id, hypothesis_id)` + status/note/confirmed_by/updated_at）、`mio_taskhub/migrations.py`（建表 + 幂等回填）、`mio_taskhub/api/ideas.py`（import/PATCH 双写 + 新读端点） |
| 要点 | 启动迁移：遍历 `Idea.hypotheses[]` 按 hid 建行，status/note/confirmed_by 从 `idea.assumptions` JSON 按 hid 匹配回填（容忍 `{hid: {...}}` 与含 id 对象两种形态），已有行跳过（幂等）；`POST /ideas/{id}/hypotheses/import` 内部在原逻辑外对新增 hid 建行；`PATCH /ideas/{id}/assumptions/{hid}` 在原逻辑外 upsert 行（status/note/confirmed_by/updated_at）；移除关联时删行；**两端点签名/参数/响应结构与 P1 逐键一致**；读路径（`_build_hypotheses`/`_idea_json`）零改动；新 `GET /api/v1/ideas/{idea_id}/assumption-links` 返回全部行 |
| 测试 | 新 `tests/test_assumption_links_p3.py`：迁移回填正确 + 二次启动不重复建行（FR-28）、import 与 PATCH 双写一致性 + P1 响应契约逐键回归（FR-28）、assumption-links 端点唯一键行（FR-28） |
| 完成定义 | 验收 5/6 通过，P1 回归绿 |

### 步骤 ③ 复盘区真实聚合（FR-27/NFR-3）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/api/cockpit.py`（`_build_retrospective`） |
| 要点 | 数据源全本地：① `Task.idea_id` 关联取 Run——finished 按 `exit_code==0`/非 0 分 success/failure，claimed/running/retrying 计 pending → `summary: {success, failure, pending, total}`；② finished run 按 `finished_at` 倒序最近 5 条 → `{task_id, task_title, run_id, exit_code, finished_at, result_excerpt(截 200 字)}`；③ `Discussion.idea_id` 且 `mode=review`+`status=closed` 倒序 5 条 → `{id, topic, ended_at, decision_count, action_item_count, converted_count}`；空数据 `items: []` + summary 全 0 且 status=ok；异常照走 SectionDegraded（1s 预算不变）；沿用 `data` 只增键 |
| 测试 | 新 `tests/test_idea_retrospective_p3.py`：run 成败统计 + 最近明细排序/截断（FR-27）、review 记录三计数（FR-27）、空数据 ok 全 0 + 模拟异常仅本区 degraded 不 500（FR-27） |
| 完成定义 | 验收 4 通过 |

### 步骤 ④ 前端同步渲染（FR-26/27 前端面）

| 项 | 内容 |
|---|---|
| 文件 | `web/src/components/IdeasView.jsx`、`web/src/api.js`（如需）、`web/src/index.css` |
| 要点 | 任务图多层节点按 kind 渲染（upstream/downstream 样式区分）；`has_cycle` 警告条展示 `cycles` 环成员 id→标题；`truncated`/`folded` 提示条；复盘区渲染 summary 卡片 + 最近 run 明细表 + review 摘要列表（空数据展示空态而非报错）；assumption-links 端点如前端有消费位（假设区状态展示）则接入，否则仅 API 层交付 |
| 测试 | `npm run build` 绿；新旧数据渲染无回归 |
| 完成定义 | 验收 1/4 前端可观察，构建绿 |

### 步骤 ⑤ 裁决记录（FR-29②）

| 项 | 内容 |
|---|---|
| 文件 | `docs/taskhub/design-idea-landing.md`（变更记录/裁决区追加，spec 文档本体小改） |
| 要点 | L356「成功标准结构化 P3 再考虑」更新为「P3 已裁决**不做**：字段模型一次定型别回头改——结构化会改 Idea 模型+旧数据迁移+goal 区渲染契约，收益不匹配 P3 主线，后续有进度追踪需求另立项」；追加变更记录条目（v1.3） |
| 测试 | 设计稿含该裁决条目（FR-29② 人工核对 + doc_quality 无死链） |
| 完成定义 | 验收 7 通过 |

### 步骤 ⑥ API 文档 + 全量回归（FR-29/NFR-5）

| 项 | 内容 |
|---|---|
| 文件 | `docs/taskhub/d4bdf530/api.md`（新，P3 端点契约） |
| 要点 | 照 9 必需 H2 模板（范围/基础/鉴权/响应结构/错误码/字段规范/幂等/接口清单/接口明细 + 变更记录）：cockpit tasks/retrospective 区新增键契约、assumption-links 端点、错误码（404 idea 等）、P1 端点「签名不变」说明；过质量门（errors=0 且 ≥80）→ draft→review→approved |
| 测试 | 全量 pytest 只增不减（**基线 915 passed + 1 skipped**）；`npm run build`；pre-push 门控 spec/api/plan approved + FR-26~29 真实引用 |
| 完成定义 | 验收 8 通过，四文档全绿 |

---

## 🧪 测试与构建

| 项 | 命令 |
|---|---|
| 后端全量 | `.venv\Scripts\python.exe -m pytest -q --tb=short`（基线 915 passed, 1 skipped，只增不减；两实例不并发防抢临时库） |
| 前端构建 | `npm run build`（`web/`） |
| 打包 | `packaging\build.ps1 -Quick`（命令文本不得含仓库名字面，build19） |
| 门控 | 提交前 `taskhub_doc_quality`；pre-push 需 spec/api/plan approved + FR 真实引用 |

---

## 🔗 FR 追溯

| 步骤 | FR |
|---|---|
| ① | FR-26 |
| ② | FR-28 |
| ③ | FR-27 |
| ④ | FR-26、FR-27（前端面） |
| ⑤ | FR-29②（裁决记录） |
| ⑥ | FR-29（回归） |

---

## ⚠️ 风险与对策

| 风险 | 对策 |
|---|---|
| 多层闭包改坏 P0 验收（一层下游/环降级/>20 折叠） | 既有 P0 tasks 测试全量回归 + 新增环路径专测断言降级警告照旧 |
| 环路径算法漏回边/多环遗漏 | DFS 栈取环段；构造双环用例断言 cycles 至少含每个环 |
| 复盘聚合拖慢 cockpit（大库全表扫描） | 限定 `Task.idea_id` 索引过滤 + Run 按 task_id 索引取；1s 区块预算 + SectionDegraded 兜底 |
| 关联表迁移破坏 P1 响应契约 | 双写策略：读路径零改动；P1 端点响应逐键回归用例锁定 |
| 假设缓存 JSON 形态不一致导致回填漏字段 | 两种形态（`{hid:{...}}` / 含 id 对象）都容忍；漏匹配时 status 默认 `unverified` 不报错 |
| 节点 100 上限在密集库截断误伤直接任务 | BFS 先放直接关联（根），再按层入上游/下游，截断只砍深层 |
| spec 文档（v1.2→v1.3）改动使 spec ReadEvidence stale | 改设计稿后用当前 run_id 重读 spec，`read_status` 自查 |
