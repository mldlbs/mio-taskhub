# 测试验收：研发评审会

## 文档信息

| 项 | 值 |
|---|---|
| 任务 ID | a20f3f9f |
| 文档类型 | test |
| 版本 | v1.1 |
| 状态 | passed |
| 负责人 | 产品管理专家团 |
| 日期 | 2026-11-26 |
| 上游 | requirement.md（FR-1~18）/ api.md / data-model.md |
| 范围 | TC-1~17 覆盖 FR-1~15（P0）；P1 用例随实现补 |

## 1. 验收标准

- pytest 新增用例全部通过，受影响回归套件零失败。
- 提取与 H5 判定用例落 `tests/test_review_extract.py`（23 项）与 `tests/test_review_h5_replay.py`（真实文档盲回放 1 项，业务仓不在本机时 skip）。
- api.md 的 8 个端点均有契约测试：成功路径 + 404/403/409/422 边界。
- 质量门：api.md 保持 100 分、requirement/spec ≥80 分；FR↔TC 无孤儿需求。
- 实弹验证：以 `docs/taskhub/decision-entity/api.md` 回放全链路成功。
- H5 pilot 报告产出，Recall/anchor 命中数据齐全。
- exe 打包后 PYZ 含新模块，重启后 MCP 新工具可见。

## 2. 测试范围

| 层 | 覆盖 | 工具 |
|---|---|---|
| 单元 | 提取规则、去重、排序分、版本 sha256 口径 | pytest |
| 契约 | 8 REST 端点参数/响应/错误码 | FastAPI TestClient |
| 集成 | Discussion/TaskReview/Decision/Subtask 副作用落库 | pytest + 临时 DB |
| 前端 | 三栏评审页、六动作、进度条、门控可视化 | 手工 + 零依赖正则校验 |
| 实弹 | 真实文档回放 + H5 pilot | REST + 脚本 |

- 不覆盖：LLM 真实调用（mock）、多角色会签（P1）、plan 及其他 17 类文档（P1）。
- 测试数据库用 `MIO_TEST_DB_PATH` 隔离；全量跑用 `--basetemp=<仓库外>`。

## 3. 用例清单

| TC | 关联 FR | 类型 | 前置 | 步骤 | 预期结果 |
|---|---|---|---|---|---|
| TC-1 | FR-1 | 单元 | errors=0 的 spec | 触发提取 | review_point 新增 ≥1 行；errors>0 时推进被拒且不产点 |
| TC-2 | FR-2 | 单元 | requirement 含 FR-n、api 含错误码表 | 提取 | 每个 FR-n / 错误码行 ≥1 点；anchor 含标题链+行号+行键，quote 逐字 ≤200 字，claim ≤120 字 |
| TC-3 | FR-3 | 单元 | mock LLM 返回"删除某点" | 提取 | 删点指令被忽略、点数不减；LLM 超时 → rule 点全留、LLM 字段空、origin=rule |
| TC-4 | FR-4 | 单元 | 同 task 同版本 | 跑两次提取 | id 集合相同；source_doc.version 与 ReadEvidence.document_version 完全相等 |
| TC-5 | FR-5 | 单元 | 两份文档同 dedup_key | 聚类 | cluster_id 相同；LLM 建议合并被人工拒 → 两点都留 |
| TC-6 | FR-6 | 单元 | 1 个 high 点 + 多 kind | 勾选 | high 自动 shortlisted；每 kind≥1；总数 ≤10 |
| TC-7 | FR-7 | 单元 | 已驳回点 | 查询 + 重跑 | 仍返回 status=rejected；重跑不产生重复 |
| TC-8 | FR-8 | 契约 | task review 状态 | 创建会议两次 | 第一次生成 Discussion(mode=review)；第二次 409；review_id/decision_id 字段存在 |
| TC-9 | FR-9 | 集成 | 6 个 shortlist 点 | 开会 | ≥6 条 DiscussionMessage 带 RP-id，role ∈ roles |
| TC-10 | FR-10 | 契约 | selected 点 | 逐动作裁决 | 六动作各自落对状态；驳回/暂缓缺理由 → 422；转 Decision 回填 decision_id；转 TC 生成 action_item |
| TC-11 | FR-11 | 集成 | 分歧点 | 转 Decision → resolve | question=claim 原文、source=RP-*；resolve 后点异步回写 resolved |
| TC-12 | FR-12 | 契约 | 存在 discussing 点 | 关会 | 409 + gaps 列出缺口；宽口径 decisions 计入采纳/修改 |
| TC-13 | FR-13 | 集成 | 全部点终态 | 关会两次 | 第一次追加 1 条 TaskReview + 五段齐全；第二次 409 且不新增 |
| TC-14 | FR-14 | 集成 | 2 条 action_item | 关会 | 2 条 Subtask 且标题含 RP-id |
| TC-15 | FR-15 | 契约 | 关会后 | 推 design | 有 TaskReview+讨论记录 → 可推进；缺讨论记录 → 422；关会本身未改文档状态 |
| TC-16 | FR-1~15 | 契约 | 不存在的 id | POST 裁决/关会 | 404 而非 403；存在非本人 → 403；状态非法 → 409/422 |
| TC-17 | FR-10 | 集成 | 已裁决点 | 会议内 undo | 点回裁决前状态且生成审计记录 |

### 补充契约用例

| TC | 关联 FR | 步骤 | 预期 |
|---|---|---|---|
| TC-18 | FR-1 | kinds 传 plan | 422 人话提示"首版只支持四类文档" |
| TC-19 | FR-6 | 候选 <5 点 | 全入 shortlist，不被截断 |
| TC-20 | FR-12 | decisions=1 | 关会 409，gaps 含"decisions 需 ≥2" |
| TC-21 | FR-4 | 文档正文被改后再提取 | version sha256 变化，生成新点不覆盖旧点 |
| TC-22 | FR-10 | reject 理由 5 字 | 422 人话提示 ≥8 字 |

## 4. H5 pilot 实验

- 目的：验证"提前提取能否命中历史 review 真发现的问题"。
- 步骤：
  - 1. 从历史已评审 task 分层抽 15 份（requirement/spec/api/decision 覆盖），记录评审开始时版本。
  - 2. 从 TaskReview.checklist/comments + Discussion(mode=review) + Decision 抽真问题清单并标 anchor，双人交叉校验。
  - 3. 按评审时版本跑提取，不注入历史结论。
  - 4. 真问题与候选按 anchor 重叠或 claim 相似度匹配，算指标。
  - 5. 1-2 名主审从候选勾 5-10 点，看真问题被勾中比例。
- 判定：

| 指标 | 定义 | 门槛 |
|---|---|---|
| Recall（主） | 命中真问题数 / 真问题总数 | ≥70% 通过；50-70% 半通过；<30% 降级 |
| anchor 准确率 | 可定位原文的候选点占比 | ≥95% |
| 裁剪命中率 | 勾选点中命中真问题的比例 | ≥50% |
| 增强采纳率 | LLM 的 why/risk 被原样保留比例 | 观察项，不设门槛 |

- 样例集：历史已评审文档（如 `docs/taskhub/2f0be5e7/review.md`）做盲回放（2026-11-26 拍板）。
- 失败退路：<30% 时价值主张从"提前发现风险"改为"结构化留痕 + 逐条裁决"，功能照常落地；2026-11-26 实测 44%，尚未触发降级线，但价值叙事已按观察区固定。

### H5 pilot 实测结果（2026-11-26）

| 样本 | 真问题 | 严格命中 | 剔弱命中 | 最终命中 | 命中率 |
|---|---|---|---|---|---|
| N1 四级阈值 | 11 | 9 | 1 | 8 | 72% |
| 需求19 管段健康度 | 9 | 8 | 0 | 8 | 88% |
| N5 阈值配置 | 10 | 2 | 1 | 1 | 10% |
| N7 查询接口 | 9 | 2 | 1 | 1 | 11% |
| N9 去假数据 | 10 | 4 | 0 | 4 | 40% |
| 合计 | 49 | 25 | 3 | 22 | 44% |

- 剔除的 3 个弱命中：N1「τ倍数」（只命中 τ 无倍数语义）、N5「JsonProperty」（命中 `toutCm/tinCm` 字段名，无 `@JsonProperty` 注解）、N7「六态枚举」（只命中两态约定，无六态语义）。
- 保留的真命中：N5「tauMin派生」（原文有"tauMin 为派生只读"）、N7「tunnelCodes≤500」（原文有"`tunnelCodes` 超 500"）。
- 口径演进链（勿混）：初版 51%（提取 bug 漏点）→ 修提取后 86%（关键词过宽 + 跨卡误配）→ 严格去伪 51%（scope 归属约束）→ 剔弱命中 **44%**（最终口径）。
- 结论：44% 落 30-50% 观察区（≥70% 通过 / 50-70% 半通过 / <30% 降级），价值叙事固定为"防遗漏 + 结构化留痕 + 逐条裁决"，代码不回滚。

## 5. 实弹演练（decision-entity/api.md）

- 样例：`docs/taskhub/decision-entity/api.md`（task 89a90af6，v1.1，approved）。
- 步骤：回放至 review 触发点 → 提取出 11 个 RP 点 → 去重（-01/02/03 归簇 C1，LLM 合并建议被人工拒）→ shortlist 6 点（含 2 high）→ 开会写 6 条消息 → 逐条裁决（-07 转 Decision）→ 关会写五段 + TaskReview(approve) → 回溯 -07 链。
- 对比基线：`docs/taskhub/2f0be5e7/review.md` 的 AC-1..AC-9 + P1/P2/P3，本机制五段应能原样承载。
