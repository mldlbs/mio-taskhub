# 📋 想法落地闭环 P2 需求规格（包 C 结构化评审）

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | 286dd112 |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结稿·包 C） |
| 上游需求 | docs/taskhub/requirement-idea-landing-p0.md（FR-1~FR-10）、requirement-idea-landing-p1.md（FR-11~FR-17） |
| 更新日期 | 2026-09-27 |

---

## 🔙 背景与问题

P0/P1 交付了想法结构化字段、驾驶舱与假设关联，但「开会」仍是**自由文本会话**，评审与闲聊无法区分：

1. **无结构化评审语义**：讨论没有 mode/roles，评审会缺角色视角（产品/技术/合规/红队）与责任署名，无法保证评审覆盖面；
2. **关闭无门控**：`close` 只收 summary/conclusions 自由文本，评审可以「无风险清单、无决策、无行动项」就关闭，`next_action` 的「评审缺行动项」规则只能事后提醒，无法在关闭时拦截；
3. **行动项不闭环**：讨论里写的行动项不产生任务，靠人工二次录入，容易漏、也无法幂等防重；
4. **角色 prompt 无处配置**：Agent 角色提示词（评审视角的行为约束）没有存储与版本概念，无法按 roles 快照回看「当时用的哪版」；
5. **词表仍是 P0 临时方案**：高风险词表靠硬编码常量 + env，非开发人员改不了（P0 FR-8 已注明 P2 迁 DB）。

为什么现在做：设计稿 v1.2 包 C 已冻结且验收清单明确；P1 完成后假设验证数据可用，P2 的模式推荐（评审入口预选）数据基础已就绪；不做则评审质量与行动项闭环永远靠人肉纪律。

---

## 📌 概述

本规格定义 P2 范围：讨论**双模式**（`mode=free|review` + `roles`）、**评审关闭结构门控**（五段最小条目）、**行动项幂等转任务**、**Agent 角色 prompt 数据库+缓存+创建时快照**、**高风险词表迁 DB**、MCP 透传与前端模式推荐。

> **守则**：`mode=free` 与现状完全一致（不藏死、不回退）；门控只验**结构**不假装验质量；快照保证「回看可知当时用的哪版」。

---

## 🎯 目标与非目标

| 类型 | 内容 |
|---|---|
| ✅ 目标 | 讨论可分「讨论会 / 评审会」，评审有角色视角与关闭门控 |
| ✅ 目标 | 评审行动项可一键幂等转任务，不重复创建、带验收标准 |
| ✅ 目标 | 角色 prompt 入库可热更新，评审创建时快照版本可回看 |
| ✅ 目标 | 高风险词表迁 DB，非开发人员可改，且 env 覆盖能力不回退 |
| 🚫 非目标 | 评审内容**质量**评估（门控只做结构校验，设计立场） |
| 🚫 非目标 | Agent 自动发起/自动主持评审（本期只做人工与 MCP 发起的结构化流程） |
| 🚫 非目标 | 任务拓扑完整版、复盘（P3）；假设关联表（P3 视用法启动） |

---

## 📦 范围（P2）

| # | 交付项 | 说明 |
|---|---|---|
| 1 | 讨论双模式 | `mode`(free\|review) + `roles`，创建校验，读取回显 |
| 2 | prompt 数据库+快照 | 角色 prompt 表 + 缓存 + 配置端点热更新 + 创建时快照 |
| 3 | 评审关闭门控 | 五段最小条目校验，违规 422，结构化 `review` 落库 |
| 4 | 行动项转任务 | `POST .../convert` 幂等，task_id 回写，批量事务 |
| 5 | MCP 透传 | open 透传 mode/roles；close 透传结构化 review 字段 |
| 6 | 前端模式推荐 | 模板下拉 + 阶段预选 + 高风险红队默认（可取消）+ 行动项 UI |
| 7 | 词表迁 DB | 随配置端点入库，env > DB > 默认常量 |

---

## 📋 功能需求（FR）

| FR | 需求描述 | 优先级 | 验收要点 |
|---|---|---|---|
| FR-18 | `Discussion` 新增可空列：`mode`(str, 默认 `free`)、`roles`(JSON list[str], 默认 `[]`)、`review`(JSON, 可空)、`prompt_snapshot`(JSON, 可空)；`POST /api/v1/discussions` 接收 `mode`/`roles`，`mode=review` 时 roles 缺失或空 → 422；`mode` 非法值 → 422；不传 mode 等价 free；读取端（单条/列表）返回新增字段且**不改既有键** | P2 | review 创建缺 roles 422；free 不传参行为与现状一致；旧讨论读取无异常 |
| FR-19 | Agent 角色 prompt **数据库+缓存**：新表存 `role`(key)/`prompt`(正文)/`version`(递增)/`updated_at`，种子默认角色 = 产品/技术/商业/合规/红队（能力约定按设计稿「角色能力约定」表）；启动加载进进程缓存；配置端点 `GET/PUT /api/v1/config/role-prompts`（PUT 写库、version+1、**使缓存失效**即热更新）；评审会**创建时**按 roles 取当前 prompt 与 version 快照进 `Discussion.prompt_snapshot`；进行中会话与回看一律用快照，不受后续热更新影响 | P2 | 改配置后新评审用新版、进行中评审仍用创建时版本；快照含 roles+各角色 version |
| FR-20 | 高风险词表**迁 DB**（随配置端点存储，默认 4 词 `高风险/合规/用户数据/花钱` 不变）；读取优先级：env `MIO_IDEA_RISK_TAGS` > DB > 默认常量；`is_high_risk` 对外语义不变（cockpit `high_risk` 字段与 P0 一致） | P2 | 改 DB 词表后判定随之变化；设 env 时 env 优先生效；high_risk 契约不回退 |
| FR-21 | 评审关闭门控：`mode=review` 的 `POST /discussions/{id}/close` 须携带结构化 `review` 字段并全部达标，否则 **422**（detail 指明缺哪段）：风险清单 `risks` ≥ 1 条；分歧点 `divergences` 非空段（「无分歧」须写达成一致依据）；建议 `suggestions` 非空段；决策选项 `decisions` ≥ 2 个；行动项 `action_items` ≥ 1 条且每条结构化——必含 `id`(会话内唯一)/`owner`/`action`/`due`(YYYY-MM-DD)/`status`(`pending\|doing\|done`)/`task_id`(可空)。达标 → `review` JSON 落库、`conclusions`/`summary` 照旧、状态置 closed；`mode=free` 的 close 与现状完全一致（不受门控约束） | P2 | 五种缺项分别 422 且 detail 明确；达标落库可回读；free close 回归不变 |
| FR-22 | 行动项幂等转任务：`POST /api/v1/discussions/{id}/convert`，body `{item_ids?: list}`（缺省 = 全部 `task_id` 为空的行动项）：**幂等**——已带 `task_id` 的条目跳过、返回原任务 id 不重复创建；新建 `Task(title=action, description=含 owner/due, acceptance_criteria=action, due_at=due, idea_id=讨论.idea_id, stage=ready)`；成功后 `task_id` 回写该条并持久化；多条转换走**单事务**，任一失败整体回滚；发出转换事件 | P2 | 同一条目转两次只有一个任务；含验收标准且 stage=ready 可领取；部分失败无半截数据 |
| FR-23 | MCP 透传：`taskhub_open_discussion` 增 `mode`/`roles` 参数透传至 `POST /discussions`（缺省 free）；`taskhub_close_discussion` 增结构化 `review` 参数透传至 close，受 FR-21 门控约束、422 detail 原样回传 | P2 | MCP 建评审会带 roles 落库；MCP 缺段关闭收 422 |
| FR-24 | 前端模式推荐与评审 UI：讨论入口模板下拉「**讨论会 / 评审会（结构化）**」；预选规则——想法 `new/fermenting` 且无 goal → `free`，`formed` 或已有 goal → `review`；cockpit `high_risk=true` → 预选 `review` 且 roles 默认含「**红队**」（**可取消**）；评审会关闭表单含五段输入（风险清单/分歧点/建议/决策选项/行动项行内编辑）；行动项列表展示状态，已转条目显示「查看任务」，未转条目「一键转任务」 | P2 | 阶段预选与红队默认生效且可改；缺段关闭被 422 拦住并提示；转任务后按钮变「查看任务」 |
| FR-25 | 回归：`mode=free` 讨论全链路（创建/消息/close）、既有编辑评审 `POST /ideas/{id}/review`、P0/P1 行为不受 P2 影响；全量 pytest 绿（基线 905 只增不减）+ `npm run build` 绿 | P2 | 全量回归零失败 |

---

## ⚙️ 非功能需求（NFR）

| NFR | 描述 |
|---|---|
| NFR-1 | 兼容性：四个新列可空，旧讨论数据零迁移（复用既有补列机制）；读取响应只增字段不改/删既有键 |
| NFR-2 | 快照原子性：评审创建时 roles 对应 prompt 与 version 一次性读取落快照，不出现半新半旧 |
| NFR-3 | 校验边界：门控只验结构与条目数，不校验内容质量（设计立场，不假装） |
| NFR-4 | 可配置：词表与 prompt 均可非开发人员经配置端点修改并即时生效（缓存失效）；env 覆盖词表能力保留 |
| NFR-5 | 测试：新增用例覆盖 422 门控五段、convert 幂等/事务回滚、快照隔离热更新、free 回归、MCP 透传、词表优先级；全量基线不回退 |
| NFR-6 | 时间语义：`due` 校验 YYYY-MM-DD；服务端时间沿用 naive UTC 存储约定 |

---

## 🔗 依赖与约束

| 项 | 内容 |
|---|---|
| 上游 | 设计稿 v1.2 包 C（讨论双模式、prompt 裁决、评审门控表、行动项幂等表、模式推荐表、API/MCP 变更表，唯一依据） |
| 上游 | P0 FR-6/FR-8（`high_risk` 输出供预选；「评审缺行动项」规则——FR-21 落地后该规则仅对 `mode=free` 关闭仍可能命中，行为保持） |
| 上游 | P1 假设验证状态（评审入口展示可参考，不强依赖） |
| 下游 | P3 复盘直接读取结构化 `review` JSON 与 action_items 的 task_id 关联 |
| 约束 | 不引入新外部服务依赖；SQLite 迁移只补列不改既有列语义 |
| 约束 | MCP 工具改动需重启 opencode MCP 进程后生效（运行时约束，验收时注明） |
| 约束 | 不改变 `mode=free` 与既有讨论 API 的任何既有语义 |

---

## ✅ 验收标准

1. `mode=free` 讨论行为与现状完全一致（全量回归）
2. `mode=review` 缺任一必填段或条目不足（风险<1、决策选项<2、行动项<1、行动项非结构化）→ close 返回 422 且 detail 明确
3. 行动项转任务幂等：同一条目重复点击不产生重复任务，成功后 task_id 回写
4. MCP `taskhub_open_discussion` 透传 mode/roles 生效；`taskhub_close_discussion` 结构化关闭受同一门控
5. 评审创建时快照 roles+prompt 版本，进行中会话不受 prompt 配置热更新影响
6. `mode=review` 未传 roles → 创建 422
7. 高风险想法（tags∩词表）评审入口默认勾选红队、可取消
8. 全量 pytest 绿（基线 905 只增不减）+ `npm run build` 绿

---

## 🎎 追溯

| FR | 设计稿章节 | 测试用例 |
|---|---|---|
| FR-18 | 包 C 讨论双模式、API 变更-`POST /discussions` | `tests/test_review_mode.py::test_free_mode_defaults_and_keys`、`::test_review_requires_roles` |
| FR-19 | 包 C Agent 角色 prompt 配置（v1.2 裁决）、角色能力约定 | `::test_review_snapshot_and_hot_update_isolation`、`::test_role_prompts_config_seeds_and_validation` |
| FR-20 | 已裁决-词表存哪（P2 随 prompt 迁 DB）、P0 FR-8 注记 | `::test_risk_vocab_default_db_env_priority`（含 high_risk 契约回归） |
| FR-21 | 包 C 评审门控（五段表）、API 变更-close | `::test_close_review_gate_each_missing_segment`、`::test_close_review_success_and_free_close_unchanged`、`::test_review_closed_at_creation_gated` |
| FR-22 | 包 C 行动项转任务（v1.2 幂等表）、验收清单-幂等/含验收标准 | `::test_convert_action_items_idempotent`、`::test_convert_selection_and_errors` |
| FR-23 | 包 C API/MCP 变更-`taskhub_open_discussion` 透传 | `tests/test_mcp_server.py`（工具签名/行为）+ API 层等价断言（FR-18/21 用例） |
| FR-24 | 包 C 模式推荐（free 不藏死）、前端入口模板下拉 | 无 JS 单测框架——`npm run build` 绿；UI 行为契约见 `docs/taskhub/286dd112/api.md` §8 |
| FR-25 | 实施分期 P2、验收清单-mode=free 回归/全量 pytest 绿 | 全量 `pytest -q`（基线 905 passed 只增不减，P2 新增 10 例） |
