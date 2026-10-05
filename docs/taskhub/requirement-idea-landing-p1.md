# 📋 想法落地闭环 P1 需求规格（包 B 假设关联）

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | 8442e38d |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结稿·包 B） |
| 上游需求 | docs/taskhub/requirement-idea-landing-p0.md（FR-1~FR-10） |
| 更新日期 | 2026-09-27 |

---

## 🔙 背景与问题

P0 已交付 Idea 8 结构化字段、驾驶舱与下一步动作，但「🔬 关键假设与验证」区仍是**空壳**：假设数据只存在于 Mio creativity 的假设库里，与 taskhub 想法是两套数据——

1. **两套数据互不关联**：Mio 发酵出的假设（带 novelty/feasibility/impact 三维分与状态）无法进入想法详情与驾驶舱，验证状态无处呈现；
2. **无断链语义**：假设在 Mio 侧被删除/拒绝后，想法侧无从感知，旧缓存会当成有效数据继续展示；
3. **回写缺安全通道**：若把回写混进整列表 PATCH，JSON 整表覆盖会丢更新；且回写必须**人工确认**，不能自动双向同步分数（设计裁决）；
4. **降级已留骨架但无真实数据**：P0 区块级 sections 机制已就绪，P1 需要接上真实跨服务调用并落实 3s/5min 契约。

为什么现在做：P0 底座已上线（build16），包 B 是设计稿冻结时明确的下一期；不做则驾驶舱假设区永远空壳，P2 模式推荐（依赖假设验证状态）失去数据基础。

---

## 📌 概述

本规格定义 P1 范围：Idea ⇅ Mio 发酵假设的**引用关联**（导入/展示/断链/人工回写）与**降级契约**（3s 超时 + 5min 缓存）。真源策略：Idea 侧只存 hypothesis id 引用，分数/状态实时经 Mio 拉取，`assumptions` 降级为本地录入缓存与展示兜底，**不双向同步分数**。

> **守则**：回写只走独立单条端点（人工确认）；跨服务失败只灰假设区，绝不拖垮驾驶舱。

---

## 🎯 目标与非目标

| 类型 | 内容 |
|---|---|
| ✅ 目标 | 想法与 Mio 发酵假设建立引用关联，驾驶舱一屏看到假设分数与状态 |
| ✅ 目标 | 断链（假设已删除）可感知、可解除；回写有并发安全的独立通道 |
| ✅ 目标 | 跨服务调用有明确超时/缓存/降级契约，外部依赖不拖垮整包 |
| 🚫 非目标 | 分数双向同步（真源=Mio，只读拉取） |
| 🚫 非目标 | 假设独立关联表 `idea_assumption_link`（P3 视用法启动） |
| 🚫 非目标 | 结构化评审 mode/roles、行动项转任务（P2）；完整任务拓扑/复盘（P3） |

---

## 📦 范围（P1）

| # | 交付项 | 说明 |
|---|---|---|
| 1 | `hypotheses` 引用字段 + 导入 | Idea 增列 id 引用列表；「从发酵假设导入」勾选 active 假设 |
| 2 | 驾驶舱分数展示 | novelty/feasibility/impact 三元分 + 状态徽章 |
| 3 | 断链降级 | 已删除 → 灰显「已失效」+ 解除关联 |
| 4 | 人工回写独立端点 | `PATCH /ideas/{id}/assumptions/{hid}`，单条、diff、并发安全 |
| 5 | 跨服务降级契约 | 超时 3s + 缓存 5min + 区块级 degraded |

---

## 📋 功能需求（FR）

| FR | 需求描述 | 优先级 | 验收要点 |
|---|---|---|---|
| FR-11 | `Idea` 新增可空字段 `hypotheses`(JSON, list[str])，只存 Mio hypothesis id **引用**；导入与解除关联均产生 `IdeaChange` diff（键 `hypotheses`） | P1 | 旧数据 NULL 无异常；引用增删可 diff 回放 |
| FR-12 | 假设导入：想法详情提供「从发酵假设导入」，列出 active hypotheses（复用 `GET /api/v1/mio/ferment` 数据源），勾选写入引用；已关联 id 不重复写入（幂等）；支持全选/取消 | P1 | 导入后引用落库；重复导入不产生重复项 |
| FR-13 | 驾驶舱假设区实时展示：对每个引用 id 经 Mio creativity 拉取 `novelty/feasibility/impact` 三元分 + `status` 徽章；**真源=Mio，只读拉取，不回写分数**；拉取结果按 FR-16 缓存 | P1 | 三元分 + 徽章渲染；分数不回写 Mio |
| FR-14 | 断链：引用的 hypothesis 在 Mio 已删除/查不到 → 该条灰显「已失效」标记 + 提供「解除关联」（从 `hypotheses` 移除），不阻塞其余条目展示 | P1 | 删除场景灰显且可解除；其他条目正常 |
| FR-15 | 人工回写独立端点 `PATCH /api/v1/ideas/{id}/assumptions/{hid}`，body `{status, note, confirmed_by}`：只改单条假设（不整列表覆盖）、**人工确认才调用**、变更进 `IdeaChange` diff（键 `assumptions[hid]`）；并发两次调用均进 diff、无丢更新；hid 不存在 → 404 | P1 | 并发两发都进 diff；404/校验 422 场景覆盖 |
| FR-16 | 降级契约：假设区跨服务调用**超时 3s**、结果**缓存 5min**（缓存期内不重复跨服务调用，带 `cached_at`）；Mio 超时/报错 → 仅 `sections[hypotheses].status=degraded` + reason，其余区块正常、整包不 500；沿用 P0 区块级骨架与总上限 5s | P1 | 超时灰该区；缓存命中不发二次调用 |
| FR-17 | 回归：P0 全部行为（下一步动作、任务图、高风险判定、`mode=free` 讨论、既有编辑评审）不受 P1 影响 | P1 | 全量 pytest 绿 |

---

## ⚙️ 非功能需求（NFR）

| NFR | 描述 |
|---|---|
| NFR-1 | 兼容性：`hypotheses` 可空，旧数据零迁移；复用现有 JSON 列迁移机制 |
| NFR-2 | 性能：跨服务调用超时 3s、缓存 5min；假设区失败不阻塞其他区块（P0 预算不变） |
| NFR-3 | 可追溯：引用增删与单条回写全部进 `IdeaChange` 版本历史，可 diff 回放 |
| NFR-4 | 安全边界：分数只读；回写必须人工触发，禁止任何自动双向同步 |
| NFR-5 | 测试：新增用例覆盖导入幂等、断链、并发回写、超时降级、缓存命中；全量基线不回退 |

---

## 🔗 依赖与约束

| 项 | 内容 |
|---|---|
| 上游 | 设计稿 v1.2 包 B（真源策略、降级契约、回写契约三节，唯一依据） |
| 上游 | Mio `mio.creativity_list` / `GET /api/v1/mio/ferment`（已有端点，可复用） |
| 下游 | P2 模式推荐读取假设验证状态；P3 可能拆 `idea_assumption_link` 表（端点签名不变） |
| 约束 | 不引入新外部服务依赖；不改 P0 字段模型（仅增 `hypotheses` 一列，包 B 设计内） |
| 约束 | 服务端时间语义遵循既有约定：naive UTC 存储、前端 `parseUtc` 解析 |

---

## ✅ 验收标准

1. 导入 active 假设后，驾驶舱「关键假设与验证」区显示三元分 + 状态徽章
2. Mio 超时/报错时仅假设区 `sections[hypotheses].status=degraded`，其余区块正常，整包不 500
3. 已删除 hypothesis 灰显「已失效」+ 可解除关联
4. 并发两次 `PATCH /ideas/{id}/assumptions/{hid}` 都进 IdeaChange（键 `assumptions[hid]`）、无丢更新
5. 回写走人工确认、不自动回写分数
6. 跨服务结果缓存 5min，缓存期内不重复调用
7. P0 行为（含 `mode=free`）回归不受影响
8. 全量 pytest 绿 + 新增用例覆盖 FR-12/FR-14/FR-15/FR-16

---

## 🎎 追溯

| FR | 设计稿章节 | 测试用例 |
|---|---|---|
| FR-11/FR-12 | 包 B 真源策略-导入 | `tests/test_idea_assumptions.py::test_hypotheses_import_happy_dedupe_idempotent`、`::test_hypotheses_import_merges_existing`、`::test_hypotheses_import_unknown_id_422`、`::test_hypotheses_import_mio_unavailable_503`、`::test_hypotheses_import_validation_422`、`::test_hypotheses_import_empty_noop_skips_mio`；`tests/test_ideas_api.py::test_idea_hypotheses_null_normalize`、`::test_idea_hypotheses_create_and_patch_diff`、`::test_idea_hypotheses_must_be_string_list` |
| FR-13/FR-14 | 包 B 展示、断链 | `tests/test_idea_cockpit.py::test_cockpit_hypotheses_scores_and_broken`、`::test_cockpit_hypotheses_empty_skips_mio` |
| FR-15 | 包 B 回写契约、验收清单-并发 | `tests/test_idea_assumptions.py::test_patch_assumption_single_entry_and_diff`、`::test_patch_assumption_hid_not_found_404`、`::test_patch_assumption_validation_422`、`::test_patch_assumption_replay_is_noop`、`::test_patch_assumption_concurrent_no_lost_update` |
| FR-16 | 包 B 降级契约、P0 注记-超时预算 | `tests/test_idea_cockpit.py::test_cockpit_hypotheses_mio_fail_degrades_only_section`、`::test_cockpit_hypotheses_mio_unavailable_degrades`、`::test_cockpit_hypotheses_exception_degrades`、`::test_cockpit_hypotheses_timeout_budget`、`::test_cockpit_hypotheses_scores_and_broken`（缓存命中断言） |
| FR-17 | 实施分期 P1、验收清单 | 全量 `pytest` 回归（基线 885 只增不减） |
