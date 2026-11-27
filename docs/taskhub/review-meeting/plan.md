# 实现计划：研发评审会

## 文档信息

| 项 | 值 |
|---|---|
| 任务 ID | a20f3f9f |
| 文档类型 | plan |
| 版本 | v1.0 |
| 状态 | approved |
| 负责人 | 产品管理专家团 |
| 日期 | 2026-11-26 |
| 上游 | requirement.md / spec.md / api.md / data-model.md |
| 总量 | 约 15 人日（S1 5 / S2 6 / S3 4），按 2 人投入约 2-3 周 |

## 1. 批次划分原则

- 简单 + 低影响 + 可演示：每批结束有可跑的演示物，不堆到最后一次交付。
- 关键路径：数据模型 → 提取 → 会议状态机 → 六动作 → Web → 关会集成 → pilot。

## 2. S1 提取骨架（第 1 周，约 5 人日）

| 项 | 内容 | 产出 |
|---|---|---|
| 数据模型 | models.py 增 review_point 表 + 索引 + 唯一约束 | create_all 零迁移 |
| 提取规则 | 复用 doc_quality 解析器，四类文档分派规则 | rule-only 提取 |
| 字段与版本 | 15 P0 字段、RP-id 可复现、version sha256 口径 | 与 ReadEvidence 对齐 |
| 去重排序 | dedup_key 聚类、强制入会、排序分 | 候选清单可分组 |
| 候选永久保留 | 幂等重跑、驳回不删除 | — |
| 演示物 | 给 decision-entity/api.md 跑提取，CLI 输出 ≥11 个 RP 点 | 可演示 |

- 本批不做：LLM 增强、Web、Decision 集成。
- 验证：TC-1~7、TC-18、TC-19、TC-21 通过。

## 3. S2 逐条评审（第 2 周，约 6 人日）

| 项 | 内容 | 产出 |
|---|---|---|
| 会议容器 | Discussion(mode=review) 创建、单 active 约束、roles 快照 | POST /meetings/open |
| 逐条讨论 | 每点写 DiscussionMessage（带 RP-id、role） | 消息可过滤 |
| 六动作 | confirm/amend/reject/decide/test/defer + 理由必填 + undo 审计 | POST /points/{id}/decide |
| 副作用最小实现 | 转 Decision 走现有端点、转 TC 生成 Subtask | 端到端可走通 |
| Web 三栏页 | ReviewMeetingPage + PointList/Detail/ActionBar/GateBar | 键盘 J/K + 数字键 1-6 |
| 演示物 | Web 上逐条点 6 个点，6 条消息带 RP-id，进度条实时更新 | 可演示 |

- 本批不做：多角色会签、agent 摘要、plan 及剩余 17 类文档。
- 验证：TC-8~11、TC-16、TC-17、TC-22 通过。

## 4. S3 关会 + 验证（第 3 周，约 4 人日）

| 项 | 内容 | 产出 |
|---|---|---|
| 关会门控 | 点全终态 + decisions≥2（宽口径）+ action_items≥1 | 409 + gaps |
| 五段与总账 | Discussion.review 五段 + 追加不可变 TaskReview | 只追加不修改 |
| 回溯视图 | GET trace + 最小 Web 展示 | FR-16 基础版 |
| H5 pilot | 历史文档盲回放 + 命中率报告 | 验证报告 |
| 打包 | 前端构建 + PyInstaller 打包 + PYZ 校验 + MCP 重启验证 | exe 可分发 |
| 演示物 | §5 端到端演练 + H5 报告 | 可演示 |

- 本批不做：parent_id 父子归并、LLM 语义≥0.85 自动合并、跨任务聚类大图、E2E 测试（对齐 review.md 的 P3 现状，先保 build + 接口契约）。
- 验证：TC-12~15、TC-20 通过；H5 报告产出。

## 5. 端到端验收场景

- 输入：`docs/taskhub/decision-entity/api.md`（task 89a90af6，v1.1，approved）。
- 步骤：提取 11 点 → 去重归簇 → shortlist 6 点（2 high）→ 开会 6 条消息 → 逐条裁决（-07 转 Decision）→ 关会五段 + TaskReview(approve) → 回溯 -07。
- 通过标准：每步 REST 返回符合 api.md，副作用落库，回溯链完整。

## 6. 风险与缓冲

| 风险 | 缓冲 |
|---|---|
| LLM 超时 / 空返回 | 降级路径先实现，LLM 作为增强后加 |
| 历史评审记录不全 | pilot 真值人工补全 + 缩小样本 |
| 双写入口冲突 | 会议为 review 阶段唯一推荐写入口，单 active 约束 |
| MCP 重启才可见 | 发版说明标注，验收时重启宿主 |
| 限流 429 | 批量写加 ≥0.4s 间隔 + 429 重试 |

## 7. 完成定义（DoD）

- 8 REST 端点 + 7 MCP 工具 + 6 Web 组件可用。
- TC-1~22 全绿；H5 pilot 报告产出。
- exe 重建并重启验证；文档链四份核心文档维持 ≥80 分。
- 关会后文档状态不被自动推进（符合 2026-11-26 拍板）。
