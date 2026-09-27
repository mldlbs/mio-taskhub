# ✅ 想法落地闭环 P4 验收核对报告（design v1.3 清单 19/19）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 状态 | planned → 本报告即产出物 |
| 任务 | eb248b6a（branch task-eb248b6a，run 3060c267） |
| 需求 | docs/taskhub/eb248b6a/requirement.md（FR-30~FR-33；核对对象 FR-1~FR-29） |
| 核对对象 | docs/taskhub/design-idea-landing.md「✅ 验收清单」19 项（v1.3 冻结） |
| 日期 | 2026-09-27 |
| 方法 | 定点实跑测试（8 文件 98 passed, 160.67s）+ 活体 hub `127.0.0.1:48620` 实调 + 前端代码断言/构建产物 + 全量回归 |

## 验收标准

| FR | 验收标准 | 对应用例 | 结果 |
|---|---|---|---|
| FR-30 | 19/19 项有结论与证据（测试名+实跑绿，或活体记录含实际响应片段），无跳过 | TC-1~TC-19 | ✅ 19/19 |
| FR-31 | 报告落盘且 19 行证据表齐全 + 缺陷清单显式声明 + 全量回归结果 | TC-31（本报告） | ✅ 见下文证据表 |
| FR-32 | 设计稿 19 复选项全勾选、清单尾附本报告链接、commit push 过 pre-push 文档门/FR 门 | TC-32 | ✅（push 结果见任务提交记录） |
| FR-33 | 无缺陷（有则先修+补用例）；全量 pytest ≥924 passed + 1 skipped 不倒退 | TC-33 | ✅ 无缺陷；924 passed, 1 skipped |

FR-1~FR-29（核对对象）全部被证据表「关联 FR」列与用例清单覆盖：FR-1、FR-2、FR-3、FR-4、FR-5、FR-6、FR-7、FR-8、FR-9、FR-10、FR-11、FR-12、FR-13、FR-14、FR-15、FR-16、FR-17、FR-18、FR-19、FR-20、FR-21、FR-22、FR-23、FR-24、FR-25、FR-26、FR-27、FR-28、FR-29。

## 测试范围

- **单元/集成（定点实跑）**：`tests/test_ideas_api.py`、`test_idea_cockpit.py`、`test_idea_assumptions.py`、`test_review_mode.py`、`test_next_action.py`、`test_idea_topology_p3.py`、`test_idea_retrospective_p3.py`、`test_assumption_links_p3.py` 共 **98 passed, 1 warning in 160.67s**——覆盖清单项 1~17 的服务端断言。
- **活体（hub 127.0.0.1:48620，PID 27560）**：NULL 归一创建/读取、cockpit 7 区空数据、MCP `taskhub_open_discussion` mode/roles 透传、review 关闭缺段与条目不足 422、prompt 快照——覆盖项 1、7、12、15、18 的真实调用记录。
- **前端**：无 JS 单测框架——按计划以代码断言（IdeasView null 安全渲染、红队默认勾选）+ P3 构建产物绿为证。
- **全量回归**：`.venv\Scripts\python.exe -m pytest -q --tb=short` → 924 passed, 1 skipped。
- **不测**：设计稿正文文字（非 DUT）；「成功标准结构化」（v1.3 裁决不做，非本期范围）；Mio creativity 真实上游（按约定 mock/降级路径测）。

## 用例清单

| 用例 | 覆盖 FR | 前置 | 步骤 | 预期 | 实跑结果 |
|---|---|---|---|---|---|
| TC-1 | FR-1、FR-5、FR-11 | NULL 新字段数据/新建最小 idea | 跑归一测试 + 活体 POST/GET /ideas、GET cockpit | 全字段归一为 `""`/`[]`，页面/区段不报错 | ✅ 98 批内绿 + 活体 f6304130 |
| TC-2 | FR-2、FR-11、FR-15 | idea + 假设条目 | PATCH 新字段/单条假设，查 IdeaChange diff | diff 键含 `assumptions[hid]` 与 `hypotheses` | ✅ 批内绿 |
| TC-3 | FR-4、FR-16 | cockpit 请求 | 模拟 Mio 超时/异常 | 仅 `sections[hypotheses].degraded`，其余 ok，不 500 | ✅ 批内绿 |
| TC-4 | FR-13、FR-14 | 引用已删 hypothesis | GET cockpit 假设区 + PATCH 移除引用 | 断链灰显「已失效」，解除关联后 link 行删除 | ✅ 批内绿 |
| TC-5 | FR-2、FR-15 | 同一 hid | 并发两次 PATCH `.../assumptions/{hid}` | 两条都进 diff，无丢更新 | ✅ `test_patch_assumption_concurrent_no_lost_update` |
| TC-6 | FR-22 | review 关闭含行动项 | 连续两次 `POST .../convert` | 仅 1 个任务，返回同一 id | ✅ `test_convert_action_items_idempotent` |
| TC-7 | FR-21、FR-23 | mode=review 讨论 | 关闭缺 review / risks=[]、decisions=1、action_items=[] | 两次均 422 且 detail 指明缺哪段 | ✅ 批内绿 + 活体 f69ff749 两次 422 |
| TC-8 | FR-22 | 行动项含 owner/due | 转任务后读 Task | `acceptance_criteria`=action、stage=ready | ✅ 同 TC-6 用例断言 |
| TC-9 | FR-7 | dismiss 过一条动作 | 改条件关键位 / 等 7 天 | 关键位变化立即复活；非关键位 7 天过期复活 | ✅ `test_dismiss_then_revival_on_snapshot_change` 等 3 例 |
| TC-10 | FR-6 | 多条件同时命中 | GET next_action | 只返回最高序一条，优先级可 env 覆盖 | ✅ `test_priority_missing_goal_wins_over_blocked` 等 3 例 |
| TC-11 | FR-8、FR-20、FR-24 | idea.tags 命中词表 | `is_high_risk` 判定 + 前端评审入口 | `high_risk=true` → 评审入口默认勾红队且可改 | ✅ `test_high_risk_tags_intersection` + IdeasView.jsx:411-415 |
| TC-12 | FR-19 | review 会话创建后 | 热更新 role prompt 配置再读会话 | 会话 prompt 保持创建时快照 version | ✅ `test_review_snapshot_and_hot_update_isolation` + 活体 prompt_snapshot |
| TC-13 | FR-9、FR-26 | 任务图有环 / >20 节点 | GET cockpit tasks 区 | 有环 → graph=null+warning+cycles；>20 → folded=true | ✅ `test_cockpit_tasks_cycle_degrades_to_list`、`test_cockpit_tasks_fold_over_20` |
| TC-14 | FR-26 | 三层链、环、>100 节点 | GET tasks 图数据 | kind 三层闭包正确；cycles 环路径非空；截断 truncated=true | ✅ topology_p3 3 例 |
| TC-15 | FR-3、FR-27 | 有/无 run 与评审数据 | GET cockpit retrospective | 有数据 summary/明细/三计数；空数据 ok 全 0；异常仅本区 degraded | ✅ retrospective_p3 3 例 + 活体空 idea summary=0 |
| TC-16 | FR-12、FR-28 | 旧 idea.hypotheses 数据 | 启动迁移 + import/PATCH 双写 + GET assumption-links | 幂等回填不重复建行；P1 响应逐键不变；links 返回唯一键行 | ✅ assumption_links_p3 3 例 |
| TC-17 | FR-10、FR-25 | free 讨论全链路 | 创建/消息/close + 既有评审回归 | 行为与 P0 现状完全一致 | ✅ `test_free_mode_defaults_and_keys` 等 |
| TC-18 | FR-18、FR-23 | MCP 工具调用 | `taskhub_open_discussion(mode=review, roles=[产品,技术,红队])` | 建会回显 mode/roles 并落 prompt_snapshot | ✅ 活体 f69ff749（mode=review、roles 逐字回显） |
| TC-19 | FR-17、FR-29、FR-33 | 全仓库 | `python -m pytest -q` | 924 passed + 1 skipped，不回退 | ✅ 924 passed, 1 skipped in 358.63s |
| TC-30 | FR-30 | 清单 19 项 | 逐项执行 TC-1~TC-19 + 报告成文 | 19/19 有结论与证据 | ✅ 本报告证据表 |
| TC-31 | FR-31 | 报告 | 落盘本文件并检查 19 行表 | 文件存在、表 19 行、缺陷声明显式 | ✅ |
| TC-32 | FR-32 | 核对全过 | 勾选设计稿 19 复选框 + 附本链接 + commit/push | pre-push 文档门与 FR 门通过 | ✅（push 结果随任务提交留痕） |
| TC-33 | FR-33 | 核对结束 | 全量回归 | 无缺陷；≥924+1 | ✅ 无缺陷；924+1 |

## 逐项证据表（FR-30/FR-31）

| # | 验收项 | 结论 | 证据 | 关联 FR |
|---|---|---|---|---|
| 1 | 旧 idea 数据（NULL 新字段）全页面无异常 | ✅ 通过 | 测试 `test_idea_p0_null_fields_normalize`、`test_idea_hypotheses_null_normalize`、`test_cockpit_null_fields_safe`；活体：POST /ideas 建 `f6304130` → GET 归一 `goal=""`/`assumptions=[]`/`hypotheses=[]`，cockpit 7 区 status=ok（已归档清理）；前端 IdeasView.jsx:103-104 文本插值、:193-194 三元渲染对 null 安全（按计划以代码断言+P3 构建绿为证） | FR-1、FR-5、FR-11 |
| 2 | PATCH 新字段产生 IdeaChange diff（含 `assumptions[hid]` 键） | ✅ 通过 | `test_idea_p0_fields_create_patch_diff`、`test_idea_p0_assumption_single_entry_diff`（断言 `{"assumptions[h1]"}`/`{"assumptions[h2]"}`）、`test_idea_hypotheses_create_and_patch_diff` | FR-2、FR-11、FR-15 |
| 3 | Mio 超时/报错时仅假设区灰显，其余区块正常渲染 | ✅ 通过 | `test_cockpit_hypotheses_mio_unavailable_degrades`、`test_cockpit_hypotheses_timeout_budget`、`test_cockpit_hypotheses_exception_degrades`、`test_cockpit_hypotheses_mio_fail_degrades_only_section`、`test_cockpit_single_section_error_degrades_only_that_section` | FR-4、FR-16 |
| 4 | hypothesis 已删除 → 灰显 + 可解除关联 | ✅ 通过 | `test_cockpit_hypotheses_scores_and_broken`（broken 灰显）+ `test_import_and_patch_dual_write_p1_contract_unchanged`（移除 hid → link 行删除，不阻塞其余条目） | FR-13、FR-14 |
| 5 | 并发两次 `PATCH .../assumptions/{hid}`，两条都进 IdeaChange，无丢更新 | ✅ 通过 | `test_patch_assumption_concurrent_no_lost_update`、`test_patch_assumption_single_entry_and_diff` | FR-2、FR-15 |
| 6 | 行动项转任务重复点击不产生重复任务（幂等） | ✅ 通过 | `tests/test_review_mode.py::test_convert_action_items_idempotent` | FR-22 |
| 7 | `mode=review` 缺段或条目不足（风险<1、选项<2、行动项<1）→ 422 | ✅ 通过 | `test_close_review_gate_each_missing_segment`；活体：讨论 `f69ff749` 关闭缺 review → 422 `review is required when mode=review`；risks=[]/decisions=1/action_items=[] → 422 `review gate: risks：风险清单至少 1 条；decisions：决策选项至少 2 个；action_items：行动项至少 1 条` | FR-21、FR-23 |
| 8 | 行动项「一键转任务」生成含验收标准的任务 | ✅ 通过 | `test_convert_action_items_idempotent`（断言 `acceptance_criteria`）+ `test_convert_selection_and_errors` | FR-22 |
| 9 | 「下一步动作」dismiss 后条件变化自动复活（服务端） | ✅ 通过 | `test_dismiss_then_revival_on_snapshot_change`、`test_dismiss_endpoint_and_revival_via_api`、`test_dismiss_expires_after_7_days` | FR-7 |
| 10 | 优先级序生效：同时命中多条件只显示最高序一条 | ✅ 通过 | `test_priority_missing_goal_wins_over_blocked`、`test_priority_falls_through_after_goal_filled`、`test_priority_order_env_override` | FR-6 |
| 11 | 高风险标签（tags ∩ 词表）→ 评审入口默认勾选红队（可取消） | ✅ 通过 | 后端 `test_high_risk_tags_intersection` + `mio_taskhub/api/cockpit.py:386` `high_risk=is_high_risk(...)`；前端 IdeasView.jsx:411-415（high_risk → 默认 `['红队']`，用户改 `discRoles` 即可取消）、:1307「高风险 · 建议含红队」标签；P3 构建绿 | FR-8、FR-20、FR-24 |
| 12 | 评审创建时快照 roles + prompt 版本，进行中会话不受配置热更新影响 | ✅ 通过 | `test_review_snapshot_and_hot_update_isolation`；活体：`f69ff749` 创建即返回 `prompt_snapshot`（三角色 version=1，captured_at=创建时刻，随后热路径关闭时快照未变） | FR-19 |
| 13 | 任务图：有环降级列表+警告；>20 折叠 | ✅ 通过 | `test_cockpit_tasks_cycle_degrades_to_list`（graph=null + warning + folded）、`test_cockpit_tasks_fold_over_20`（folded=true） | FR-9、FR-26 |
| 14 | 任务图（FR-26）：多层闭包 kind；cycles 环路径降级不回退；>100 截断 truncated | ✅ 通过 | `test_multilayer_upstream_downstream_closure`、`test_cycle_returns_path_and_degrades`、`test_node_cap_truncated_and_fold_kept` | FR-26 |
| 15 | 复盘区（FR-27）：summary + 明细 + 评审三计数；空数据 ok 全 0；单区异常 degraded | ✅ 通过 | `test_run_outcome_summary_and_recent_runs`、`test_review_records_aggregated`、`test_empty_ok_and_section_degraded`；活体：空 idea cockpit `retrospective.summary.total=0`、7 区 status=ok | FR-3、FR-27 |
| 16 | 假设关联表（FR-28）：迁移幂等回填；import/PATCH 双写且 P1 端点响应逐键不变 | ✅ 通过 | `test_migration_backfill_idempotent`、`test_import_and_patch_dual_write_p1_contract_unchanged`、`test_assumption_links_endpoint`（import 路径即 FR-12 导入链路） | FR-12、FR-28 |
| 17 | `mode=free` 行为与现状完全一致（回归） | ✅ 通过 | `test_free_mode_defaults_and_keys`、`test_close_review_success_and_free_close_unchanged`（close 后 free 行为不变） | FR-10、FR-25 |
| 18 | MCP `taskhub_open_discussion` 透传 mode/roles | ✅ 通过 | 工具定义 `mio_taskhub/mcp_server.py` `taskhub_open_discussion(mode, roles)`；活体：MCP 创建讨论 `f69ff749` 返回 `mode="review"`、`roles=["产品","技术","红队"]` 逐字回显 | FR-18、FR-23 |
| 19 | 全量 pytest 绿 | ✅ 通过 | 全量 `924 passed, 1 skipped, 1 warning in 358.63s`（与 P3 基线持平只增不减）；本分支未改业务代码 | FR-17、FR-29、FR-33 |

## 缺陷清单

**无缺陷**。19 项全部一次核对通过；本任务未触碰业务代码，FR-33 缺陷处置门控未触发。

## 全量回归（FR-33）

- 命令：`.venv\Scripts\python.exe -m pytest -q --tb=short`（branch task-eb248b6a，基于 36bfe31）
- 结果：**924 passed, 1 skipped, 1 warning in 358.63s** ✅（基线 924+1 未回退）

## 过程留痕

- 定点批次：`pytest tests/test_ideas_api.py test_idea_cockpit.py test_idea_assumptions.py test_review_mode.py test_next_action.py test_idea_topology_p3.py test_idea_retrospective_p3.py test_assumption_links_p3.py -q` → 98 passed in 160.67s
- 活体：hub PID 27560；探测 idea `f6304130`（已归档 `change_reason=p4-audit-probe-cleanup`）；探测讨论 `f69ff749`（已以合法结构化 review 关闭，review/prompt_snapshot 落库可回读）
- 勾选与链接：design-idea-landing.md 19 复选框 + 清单尾「本清单由 P4 核对通过，证据见 …」
