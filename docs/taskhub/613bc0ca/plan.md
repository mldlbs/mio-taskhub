# 实现计划 · 性能修复（列投影 + SQL 聚合 + 轮询收敛）

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.1 |
| 状态 | approved |
| 最后更新 | 2026-11-18 17:34 |
| 负责人 | 项目作者（单人） |
| 关联任务 | 613bc0ca |
| 需求 | `docs/taskhub/613bc0ca/requirement.md`（FR-1 ~ FR-8） |
| 设计 | `docs/taskhub/613bc0ca/spec.md` |
| 契约 | `docs/taskhub/613bc0ca/api.md` |
| 测试 | `docs/taskhub/613bc0ca/test.md` |

## 实施原则

- **契约零变更优先**：任何一处改动若导致响应字段集合变化，先回退再想别的办法。
- **先测后改**：改前先在生产库副本上跑一遍端点计时与响应体快照，作为对照基线。
- **可回退**：改动集中在两个后端文件 + 三个前端文件，`git revert` 即可。
- **引用 FR-n**：代码与测试注释引用需求编号。

## 批次 0 · 流程前置

| 步骤 | 动作 | 完成条件 |
|---|---|---|
| 0.1 | 建任务并生成文档骨架 | 任务 `613bc0ca`，requirement/spec/api/test 已落盘 |
| 0.2 | 写四份文档并推进到 approved | 质量分 ≥ 80、errors = 0 |
| 0.3 | 补讨论记录（进 design 前置） | 讨论 `5ec981d9` 已创建 |
| 0.4 | 推进阶段到 ready | brainstorming → design → planning → ready |
| 0.5 | 建分支 | `task-613bc0ca` |

## 批次 A · 建立对照基线（先测后改）

| 步骤 | 动作 | 对应 FR | 完成条件 |
|---|---|---|---|
| A1 | 复制生产库到临时目录（只读副本，数据规模一致） | FR-7 | 副本可用，行数与生产一致（task 2039 / run 5258） |
| A2 | 在副本上起隔离实例（48621），记录三个端点的**改前**中位耗时与响应体大小 | FR-7 | 基线表落盘：`/tasks` 308ms/988KB、`board/summary` 1008ms/2.1KB、`/ideas` 17ms/117KB |
| A3 | 记录改前**响应体快照**（`/tasks` 与 `/board/summary` 各存一份 JSON） | FR-6 | 快照落盘，作为字段级 diff 基线 |

## 批次 B · 后端改造

| 步骤 | 动作 | 对应 FR | 文件 | 完成条件 |
|---|---|---|---|---|
| B1 | 列表端点改列投影，抽出 `_PROJECTION` 常量 | FR-1 FR-2 FR-8 | `api/tasks.py:265-300` | 响应 dict 构造逻辑**一行未改**，只换取数方式 |
| B2 | 新增 `_counts_from_rows`（吃 SQL 聚合行），与 `_count_by_stage` 输出**逐项相等** | FR-3 | `api/board.py` | 两函数对同一数据输出完全相同 |
| B3 | board 计数改 `group by stage, state` | FR-3 | `api/board.py:254` | 不再 `select(Task).all()` |
| B4 | `_ready_queue` / `_running_tasks` / `_check_alerts` 改定点限量查询；依赖检查只针对 `depends_on` 非空的任务 | FR-4 | `api/board.py` | 单请求加载 Task 实体 ≤ 100 |
| B5 | `_delivery_quality` / `_recent_done` **不动** | — | `api/board.py` | 已有限制，无需改 |

## 批次 C · 前端改造

| 步骤 | 动作 | 对应 FR | 文件 | 完成条件 |
|---|---|---|---|---|
| C1 | 合并两次 tasks 调用为一次，客户端分组取消态 | FR-2 | `web/src/App.jsx` | 单周期只发 1 次 `/tasks` |
| C2 | `ideas` 轮询 5 s → 30 s | FR-5 | `web/src/App.jsx` | 轮询频次降至 1/6 |
| C3 | `board/summary` 轮询 5 s → 30 s | FR-5 | 调用方组件 | 同上 |
| C4 | `IdeasView` / `MemoryView` 详情轮询 5 s → 30 s | FR-5 | 视图组件 | 同上 |
| C5 | **不得改** `App.jsx:233` 的 250 ms WS 重连倒计时与 `App.jsx:162` 的 30 s 版本检查 | — | — | 实时性与版本提示不受影响 |

## 批次 D · 验证与对比

| 步骤 | 动作 | 对应 FR | 完成条件 |
|---|---|---|---|
| D1 | 重启隔离实例，在**同一份库副本**上测改后耗时与响应体 | FR-1 FR-3 FR-7 | `/tasks` ≤ 80ms、`/board/summary` ≤ 100ms |
| D2 | 字段级 diff：改前快照 vs 改后响应 | FR-6 FR-8 | **零差异** |
| D3 | 跑 TC-3 过滤语义与 TC-9 既有用例（不改断言） | FR-2 FR-8 | 全过 |
| D4 | 统计单请求加载的 Task 实体数 | FR-4 | ≤ 100 |
| D5 | 观察 3 分钟日志，核对轮询频次 | FR-5 | `board/summary`/`ideas` 降至 1/6 |
| D6 | 对比表落盘 `docs/taskhub/613bc0ca/perf-compare.md` | FR-7 | 表含端点/前后耗时/前后体积/达标结论 |
| D7 | 生产实例择机重启并复测（hub 停几秒，需择时） | — | 线上 CPU 下降可观测 |

## 涉及文件清单

| 文件 | 批次 | 动作 |
|---|---|---|
| `mio_taskhub/api/tasks.py` | B1 | 改 |
| `mio_taskhub/api/board.py` | B2 B3 B4 | 改 |
| `web/src/App.jsx` | C1 C2 | 改 |
| `web/src/components/IdeasView.jsx` | C4 | 改 |
| `web/src/components/MemoryView.jsx` | C4 | 改 |
| `tests/test_task_list_projection.py` | D | 新增 |
| `docs/taskhub/613bc0ca/perf-compare.md` | D6 | 新增（对比表） |

## 风险与回退

| 风险 | 表现 | 回退动作 |
|---|---|---|
| 投影漏字段 | 响应缺键 → 前端白屏或字段 `undefined` | TC-2 字段级 diff 会直接抓到；`git revert` |
| 聚合口径不一致 | 看板数字与改前对不上 | TC-4 逐项比对；先只改 board 不改 tasks |
| 顺序漂移 | 任务列表顺序变化 | **不得新增 order_by**；若已加则移除 |
| 前端合并调用漏改分支 | 取消态任务不显示 | 逐处检查消费 `tasks` 的组件 |
| 轮询放宽后实时性下降 | 看板数据滞后 | WS 事件推送本就是实时主力；若仍不足，把 `board/summary` 回调 5 s |
| 生产重启断服 | 页面断几秒 | 择机重启；先在隔离实例验证通过 |

## 后续独立立项（本期不做）

- 列表分页（破坏性契约变更，需通知全部消费方）。
- 日志轮转（console.log 80 MB 无上限）。
- 僵尸任务归档（task 表 1506 条 FAILED）。
- 内存优化——已实证无泄漏，不需要做。
