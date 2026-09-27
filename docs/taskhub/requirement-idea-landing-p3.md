# 📋 想法落地闭环 P3 需求规格（包 D 拓扑完整版 + 复盘 + 假设关联表）

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | d4bdf530 |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结稿·包 D） |
| 上游需求 | requirement-idea-landing-p0.md（FR-1~FR-10）、-p1.md（FR-11~FR-17）、-p2.md（FR-18~FR-25） |
| 设计讨论 | 讨论 3c3b4c90（task d4bdf530，2026-09-27，P3 包 D 落地勘察定案） |
| 更新日期 | 2026-09-27 |

---

## 🔙 背景与问题

P0~P2 交付了结构化字段、驾驶舱、假设关联与结构化评审，但三块仍停在「能展示、不能深挖」：

1. **任务图只有一层**：`_build_tasks` 仅直接关联 + 一层下游，多层依赖链断裂；环检测只回布尔，降级警告无法告诉用户**环在哪**（哪些任务互相卡住）；
2. **复盘区是空壳**：`_build_retrospective` 恒返回 `items: []`，执行结果（run 成败）与 P2 结构化评审记录都落库了却没有聚合出口，「想法做完复盘」无处可看；
3. **假设回写状态寄存在 JSON 缓存**：P1 把回写 status/note/confirmed_by 放 `idea.assumptions` JSON 列按 hid 键存，无法按假设反查、无唯一约束、无更新时间，设计稿 L137 已裁决 P3 起用 `idea_assumption_link` 关联表（视用法启动——本包即用法）；
4. **成功标准结构化裁决待定**：设计稿 L356 记「P3 再考虑 `{metric, from, to, deadline}`」。

为什么现在做：P0/P1/P2 已把数据源（Run、Discussion.review、Idea.hypotheses/assumptions）全部备齐，P3 是设计稿冻结的收官分期；不做则驾驶舱「任务图—执行—复盘」链路断头、假设回写状态无正经存储。

---

## 📌 概述

本规格定义 P3 范围：**任务拓扑完整版**（多层闭包 + 环路径）、**复盘区真实聚合**（run 成败 + P2 评审记录）、**假设关联表**（建表 + 启动迁移 + 双写兼容）、**成功标准结构化的取舍裁决**。

> **守则**：P0/P1/P2 契约**只增键不改键**；关联表走双写，读路径零改动；成功标准结构化**本期不做**（字段模型一次定型别回头改）。

---

## 🎯 目标与非目标

| 类型 | 内容 |
|---|---|
| ✅ 目标 | 任务图升级为多层上下游闭包，含环时返回环路径而非仅布尔 |
| ✅ 目标 | 复盘区真实聚合关联任务的 run 成败与结构化评审记录 |
| ✅ 目标 | 假设回写状态迁入 `idea_assumption_link` 表，P1 端点签名与响应不变 |
| 🚫 非目标 | 成功标准结构化 `{metric, from, to, deadline}`（本期不纳入，理由见 FR-29 裁决记录） |
| 🚫 非目标 | 驾驶舱新增端点/新分区（全部在既有 sections 内扩展） |
| 🚫 非目标 | 跨项目/全局拓扑视图、DAG 自动布局算法（前端布局沿用现状） |

---

## 📦 范围（P3）

| # | 交付项 | 说明 |
|---|---|---|
| 1 | 任务拓扑完整版 | 多层 BFS 上下游闭包、节点上限、环路径 `cycles`、truncated 标记 |
| 2 | 复盘区聚合 | Run 成败统计 + 最近 5 条 run 明细 + review 记录摘要，区块级降级 |
| 3 | 假设关联表 | 建表 + 启动迁移 + import/PATCH 双写，P1 契约不变 |
| 4 | 前端同步 | 任务图多层渲染、环路径警告条、复盘区真实数据展示 |
| 5 | 裁决记录 | 成功标准结构化「本期不做」写入设计稿变更记录 |

---

## 📋 功能需求（FR）

| FR | 需求描述 | 优先级 | 验收要点 |
|---|---|---|---|
| FR-26 | 任务拓扑完整版（`sections.tasks.data` 结构**只增键**）：多层 BFS——以 `idea_id` 直接关联任务为根，**上游**=`depends_on` 传递闭包（我依赖谁）、**下游**=反向边传递闭包（谁依赖我），均无层数上限但**节点总数上限 100**，超限截断并置 `truncated: true`；`graph.nodes` 增 `kind: direct\|upstream\|downstream`，`edges` 含闭包内全部边；`folded`（>20 折叠）语义保留不变。环检测升级：`has_cycle` 布尔保留，新增 `cycles: list[list[str]]`（每个环的节点路径，首尾同节点可省略回边，至少回传一个环）；**含环时仍按 P0 降级**——`graph: null` + `warning` 文案 + `cycles` 供前端展示环成员（P0 验收「有环降级列表+警告」不回退） | P3 | 三层依赖链全部入图；上游节点 kind=upstream；>100 截断 truncated=true；环返回非空 cycles 且降级警告照旧；无环时 cycles=[] |
| FR-27 | 复盘区 `_build_retrospective` 从空壳到真实聚合（数据源全部 hub 本地，不跨服务）：① **run 成败**——经 `Task.idea_id` 关联取该想法全部任务的 `Run`，`state=finished` 按 `exit_code==0` 计 `success`、非 0 计 `failure`，`state=retrying/claimed/running` 计入 `pending`，输出 `summary: {success, failure, pending, total}`；② **最近明细**——按 `finished_at` 倒序取最近 5 条 finished run：`{task_id, task_title, run_id, exit_code, finished_at, result_excerpt}`（result 截断 200 字符）；③ **评审记录**——`Discussion.idea_id` 匹配且 `mode=review`、`status=closed`，倒序取最近 5 条：`{id, topic, ended_at, decision_count, action_item_count, converted_count}`（converted = action_items 中 `task_id` 非空数）。单区异常/超时照走既有 SectionDegraded 仅本区 `degraded`（1s 预算不变，接口不 500） | P3 | 有 run 的想法返回非空 summary 与明细；空数据 `items: []` + summary 全 0 且 status=ok；异常仅本区 degraded；既有键不改 |
| FR-28 | 假设关联表 `idea_assumption_link`：`(idea_id, hypothesis_id)` 唯一键 + `status`(str, 默认 `unverified`) / `note`(str) / `confirmed_by`(str) / `updated_at`(datetime)。① **启动迁移**：对每个 `Idea.hypotheses[]` 的 hid 建行，status/note/confirmed_by 从 `idea.assumptions` JSON 缓存按 hid 匹配回填（缓存条目形如 `{hid: {...}}` 或含 id 键的对象，容忍两种形态），迁移幂等（已有行跳过）；② **双写兼容**：`POST /ideas/{id}/hypotheses/import` 与 `PATCH /ideas/{id}/assumptions/{hid}` **签名、参数、响应结构与 P1 完全一致**，内部在原逻辑外同步写/删关联表行（import 新增 hid → 建行；PATCH 回写 → upsert status/note/confirmed_by/updated_at；移除关联 → 删行）；读路径（`_build_hypotheses`、`_idea_json`）零改动；③ 新增读端点 `GET /api/v1/ideas/{idea_id}/assumption-links` 返回全部关联行（供复盘/后续消费） | P3 | 迁移后旧数据回填 status/note 正确且重复启动不重复建行；P1 两端点回归测试全绿（响应与改造前逐键一致）；links 端点返回唯一键行 |
| FR-29 | 回归与取舍：① P0/P1/P2 全部既有行为不受 P3 影响（含 cockpit 其余区块、free/review 讨论、convert 幂等、next_action）；全量 pytest 绿（**基线 915 passed + 1 skipped 只增不减**）+ `npm run build` 绿；② **裁决记录**：成功标准结构化 `{metric, from, to, deadline}` **不纳入本期**——设计稿 L356「P3 再考虑」按本任务裁决更新为「P3 已裁决不做：字段模型一次定型别回头改，结构化会改 Idea 模型+旧数据迁移+goal 区渲染契约，收益不匹配 P3 主线；后续有进度追踪需求另立项」 | P3 | 全量回归零失败；设计稿变更记录含该裁决条目 |

---

## ⚙️ 非功能需求（NFR）

| NFR | 描述 |
|---|---|
| NFR-1 | 契约兼容：tasks/retrospective 区 `data` 只增键不改/删既有键；P1 端点签名与响应逐键不变 |
| NFR-2 | 性能：复盘与拓扑均为本地 SQL 聚合，tasks/retrospective 沿用 1s 区块预算；节点上限 100、明细上限 5 防止大想法拖垮 cockpit |
| NFR-3 | 降级：单区异常仅该区 degraded，接口不 500（沿用 P0 FR-4 机制，不新增整包降级字段） |
| NFR-4 | 迁移幂等：关联表启动迁移可重复执行；补列/建表走既有 migrations 机制，只增不改既有列 |
| NFR-5 | 测试：新增用例覆盖多层闭包/环路径/截断、复盘三源聚合与空数据/降级、关联表迁移幂等与双写一致性、P1 端点回归、全量基线不回退 |
| NFR-6 | 时间语义：沿用 naive UTC 存储与 `_now()` 约定；run 明细时间以 `finished_at` 序 |

---

## 🔗 依赖与约束

| 项 | 内容 |
|---|---|
| 上游 | 设计稿 v1.2 包 D（L137 关联表、L178 复盘、L180-186 任务图规则、L327 分期、L356 成功标准裁决） |
| 上游 | P0 FR-3/FR-4/FR-9（任务图一层规则本包升级为多层，降级/区块机制不变） |
| 上游 | P1 FR-13~FR-16（hypotheses 区契约不变）、P2 FR-18~FR-25（review 记录与 action_items.task_id 为复盘数据源） |
| 下游 | 后续迭代可消费 `idea_assumption_link` 做假设进度视图（本期只到读端点） |
| 约束 | 不引入新外部服务依赖；SQLite 迁移只建表/补列不改既有列语义 |
| 约束 | 驾驶舱不新增端点；MCP 工具本期无签名变更（重启约束不触发） |

---

## ✅ 验收标准

1. 三层及以上依赖链在任务图完整呈现（direct/upstream/downstream 分类正确）
2. 含环时 `cycles` 非空且降级为列表 + 警告（P0 行为不回退），无环时 `cycles=[]`
3. 节点 >100 截断置 `truncated=true`；>20 折叠语义保留
4. 有 run/评审数据的想法复盘区返回真实 summary/明细/review 摘要；空数据 status=ok 全 0；单区异常仅本区 degraded
5. 关联表迁移幂等回填正确；P1 import/PATCH 端点响应与改造前逐键一致（回归用例锁定）
6. `GET .../assumption-links` 返回唯一键关联行
7. 设计稿含成功标准结构化「本期不纳入」裁决记录
8. 全量 pytest 绿（基线 915 passed + 1 skipped 只增不减）+ `npm run build` 绿

---

## 🎎 追溯

| FR | 设计稿章节 | 测试用例 |
|---|---|---|
| FR-26 | L180-186 任务图 P0 渲染规则（多层升级）、L327 P3 分期 | `tests/test_idea_topology_p3.py::test_multilayer_upstream_downstream_closure`、`::test_cycle_returns_path_and_degrades`、`::test_node_cap_truncated_and_fold_kept` |
| FR-27 | L178 复盘 = task_outcome + 评审记录、L58 区块降级机制 | `tests/test_idea_retrospective_p3.py::test_run_outcome_summary_and_recent_runs`、`::test_review_records_aggregated`、`::test_empty_ok_and_section_degraded` |
| FR-28 | L137 `idea_assumption_link` 长期演进条（P3 视用法启动） | `tests/test_assumption_links_p3.py::test_migration_backfill_idempotent`、`::test_import_and_patch_dual_write_p1_contract_unchanged`、`::test_assumption_links_endpoint` |
| FR-29 | 实施分期 P3、L356 成功标准结构化裁决、验收清单 | 全量 `pytest -q`（基线 915 passed + 1 skipped 只增不减）+ `npm run build`；设计稿变更记录含裁决条目 |
