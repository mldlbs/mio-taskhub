> 软件项目文档链 · 7 件套  
> 上游：[接口契约](docs/taskhub/decision-entity/api.md) | 下游：—  
> 任务：89a90af6（Decision 实体）

# 测试验收（Test & Acceptance）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 最后更新 | 2026-11-04 13:40 |
| 状态 | passed |
| 负责人 | agent:workbuddy |
| 适用范围 | tests/test_decisions.py + inbox 集成回归 + 实弹链路验证 |

## 1. 验收标准

- FR-01：Decision 表 15 字段齐全，create_all 零迁移自动建表（conftest 每用例重建验证）
- FR-02：五个 REST 端点行为符合 api.md 契约；422 人话化、409 终态护栏、404 不存在
- FR-03：inbox decide 队列含 open 决策（kind=decision、question/action/age_days 齐全）；summary.decide = decide_tasks + decide_decisions；裁决后即时出队
- FR-04：MCP 工具注册成功且错误文案人话化（语法级错误不允许——曾实测 f-string 转义炸 import）
- FR-05：Web 构建通过（vite build）；决策行与裁决对话框可用（人工验收）
- 全量回归：tests/ 全套通过（除既有 pre-existing 失败名单）

## 2. 测试范围

- 单元/接口：REST 校验逻辑（options 形态、deadline 解析、终态护栏）
- 集成：inbox 队列分桶/排序/裁决出队
- 回归：tests/test_mcp_server.py（工具面）、tests/test_inbox.py（旧三队列语义不变）
- 实弹：源码 hub 48620 六步链路（create → inbox → resolve → 出队 → 回溯 → 409）
- 不覆盖：前端交互逻辑（无 jsdom 测试框架，走构建 + 人工验收）

## 3. 用例清单

| TC | 关联 FR | 用例 | 结果 |
|----|---------|------|------|
| TC-1 | FR-02 | 创建缺 question → 422 含「你要做的是」 | 通过 |
| TC-2 | FR-02 | options 三形态：{label,note} / 纯字符串 / 缺省 | 通过 |
| TC-3 | FR-02 | options 非法：字符串、缺 label → 422 | 通过 |
| TC-4 | FR-02 | deadline 非法 422 / 合法 ISO 200 | 通过 |
| TC-5 | FR-02 | 列表默认 open、created_at 降序；非法 status 422 | 通过 |
| TC-6 | FR-02 | 详情 404 | 通过 |
| TC-7 | FR-02 | 裁决缺 choice → 422 列出可选 label | 通过 |
| TC-8 | FR-02 | 裁决生命周期：200 → 原文保留 → 再裁决 409 → resolved 列表可见 | 通过 |
| TC-9 | FR-02 | 裁决 404 | 通过 |
| TC-10 | FR-02 | 作废：successor 不存在 422 → 正常 200 → 再作废 409 | 通过 |
| TC-11 | FR-03 | inbox 含 open 决策（question/title/options/action/age_days）；summary 分解恒等 | 通过 |
| TC-12 | FR-03 | 裁决后决策从 decide 队列消失 | 通过 |
| TC-13 | FR-03 | decide 队列最老的在前 | 通过 |
| TC-14 | FR-04 | test_mcp_server.py 全套通过（工具导入/注册无语法错误） | 通过 |
| TC-15 | 全量 | pytest tests/ 全量回归 | 见执行记录 |

## 4. 实弹执行记录

- 2026-11-04 源码 hub（48620）：六步链路全通——① create 21e43365 ② inbox 出现（summary.decide_decisions=1）③ resolve choice=闭环 ④ 队列消失 ⑤ question/options 原文回溯保留 ⑥ 终态再裁决 409
- 前端：vite build 通过；hub 服务 hash 与 web/dist 一致（index-ci0HK-il.js）
