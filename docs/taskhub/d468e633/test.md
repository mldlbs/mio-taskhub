# 测试验收 · 窄门禁（空 diff 拦截）

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 状态 | planned（待执行） |
| 最后更新 | 2026-10-20 16:40 |
| 负责人 | 项目作者（单人） |
| 关联任务 | d468e633 |
| 需求 | `docs/taskhub/d468e633/requirement.md`（FR-1 ~ FR-11） |
| 设计 | `docs/taskhub/d468e633/spec.md` |
| 契约 | `docs/taskhub/d468e633/api.md` |

## 验收标准

逐条对应需求编号，全部 FR 被覆盖才可验收：

| FR | 验收判据 | 对应用例 |
|---|---|---|
| FR-1 | 判定证据由服务端采集，Agent 上报的改动信息不参与判定 | TC-1 TC-2 |
| FR-2 | 空改动 + success → 422 `run.empty_diff`，且 run 状态、exit_code、attempt 均不变 | TC-1 TC-6 |
| FR-3 | 5 种豁免/采集失败场景全部 200 放行，且标注写明原因 | TC-3 TC-4 TC-5 |
| FR-4 | 连续被拒达上限后判 run 失败并进入既有重试流程 | TC-7 |
| FR-5 | 拦截后 `Event` 与 `HistoryEvent` 两表均有记录（查库确认） | TC-8 |
| FR-6 | 标注只提示不拦、不弹 toast，文案含「不影响放行」 | TC-9 |
| FR-7 | 422 detail 含 code/message/hint/rejections，错误码不复用通用文案 | TC-1 |
| FR-8 | `MIO_DIFF_GATE` 三档生效，off 即时全量放行 | TC-10 |
| FR-9 | 迁移幂等，存量任务自动豁免 | TC-9（迁移子用例） |
| FR-10 | 任务详情 `runs[]` 增补 3 字段 | TC-6 |
| FR-11 | 任务详情能取到 diff_summary / annotations / rejections | TC-6 |

**总体验收判据**：存在至少一例真实场景，能证明「声明成功但零改动」被拦下（FR-2 的最终判据，见计划批次 D4）。

## 测试范围

### 测什么

- **单元**：`diff_gate.py` 的 `snapshot` / `differs` / `check_diff_gate` / `merge_annotations` 纯函数逻辑，含各种边界输入。
- **集成**：`POST /runs/{id}/result` 的完整门禁路径（状态码、错误体、落库、状态不变）。
- **迁移**：新增列存在性、幂等性、存量任务豁免。
- **前端接线**：`ApiError` 错误码读取、`HIST_TYPE_META` 标签、完成报告标注渲染分支。

### 不测什么（明确排除）

- **不测真实测试执行**——本期不实现测试执行器（需求 N-1）。
- **不测多任务并发下的快照一致性**——并发场景按「跳过判定」处理，行为是确定的（`reason=concurrent_run`），只验该分支返回 200。
- **不测 WebSocket 重连**——属既有能力，不在本次变更范围。
- **不测 PyInstaller 打包**——打包验证另有流程。

### 测试环境

- 隔离库：`MIO_TEST_DB_PATH=$(mktemp -d)/verify.db`（**必须隔离**，否则污染生产库 `~/.mio_taskhub/taskhub.db`）。
- git 场景用临时目录建仓库，不在真实仓库上跑。
- 全量跑加 `--basetemp=<独立目录>`；并行跑用不同 `MIO_TEST_DB_PATH`。

## 用例清单

| 编号 | 覆盖 FR | 前置 | 步骤 | 预期 |
|---|---|---|---|---|
| TC-1 | FR-1 FR-2 FR-7 | 任务有 workspace（临时 git 仓库），`MIO_DIFF_GATE=block` | 采集前快照 → 不做任何改动 → `POST /runs/{id}/result` 带 `success=true` | 422；`detail.code=run.empty_diff`；`detail` 含 message/hint/files_changed/rejections/max_rejections；run 状态不变、无 exit_code、attempt 未增 |
| TC-2 | FR-1 FR-2 | 同上 | 修改一个文件但不提交 → 再提交 success | 200；`diff_summary.changed=true`；`reason=has_changes` |
| TC-3 | FR-1 FR-2 | 同上 | 修改后 `git commit` → 再提交 success | 200；`diff_summary.changed=true`（HEAD 变化路径） |
| TC-4 | FR-3 | workspace 指向非 git 目录 | 提交 success | 200；标注 `reason=collect_failed`；无 422 |
| TC-5 | FR-3 | `task.diff_gate_exempt=true` | 提交 success | 200；标注 `reason=task_exempt` |
| TC-6 | FR-2 FR-3 FR-10 | 任务 `workspace` 为空；`task_kind=idea_review` | 提交 success | 均 200；标注原因分别为 `no_workspace` / `task_kind_exempt`；`GET /tasks/{id}` 的 `runs[]` 含 `diff_summary`/`annotations`/`rejections` 三字段 |
| TC-7 | FR-4 | `MIO_DIFF_GATE=block` | 连续 3 次提交 success（均零改动） | 前 2 次 422（可重试）；第 3 次 `rejections > MAX_REJECTIONS` → 409 `run.empty_diff_repeated`，run 判失败（exit_code=1、state=finished） |
| TC-8 | FR-5 | 同 TC-1 | 拦截后**直接查库** | `Event` 表有 `type='run.result_rejected'` 且 `entity='task'` 的记录；`HistoryEvent` 表有 `type='rejected'` 的记录；run.annotations 含 `level='block'`；三条都不可缺失（验证「先 commit 再抛」的时序） |
| TC-9 | FR-6 FR-9 | `MIO_DIFF_GATE=warn` | 零改动提交 success | 200（不拦）；`annotations` 含 `level='warn'` 且 message 含「不影响放行」；不产生 WS toast 事件；迁移子用例：老库启动后新列存在、存量任务豁免为真、重复执行迁移不报错 |
| TC-10 | FR-8 | 同一零改动场景，分别置 `MIO_DIFF_GATE=off` / `warn` / `block` | 三次提交 | off → 200 且**无标注**；warn → 200 且有 warn 标注；block → 422 |

### 补充用例（实现时新增）

| 编号 | 覆盖 FR | 场景 | 预期 |
|---|---|---|---|
| TC-11 | FR-5 | `GET /runs/{id}/diff-evidence` 只读自查 | 200；返回 mode/would_block/reason/before/after；**不改 run 任何字段**（state 仍 running、rejections=0、annotations 空） |
| TC-12 | FR-2 | `differs()` 纯函数边界 | 采不到信息（ok=False）一律返回 True（视为有改动，不拦）；head 变 / porcelain 变 / 增删行>0 三条判据各自命中 |
| TC-13 | FR-6 | `merge_annotations()` 同 code 去重 | 同 code 只保留最新一条；不同 code 累加 |
| TC-14 | FR-8 | `MIO_DIFF_GATE` 非法值 | 未设 / 非法值 → 按 `warn` 处理；大小写不敏感（`BLOCK` → `block`） |
| TC-15 | FR-3 | `snapshot()` 非 git 目录 / 空路径 | ok=False + error，不抛异常 |

## 验收流程

- **谁验收**：项目作者（单人项目，本人即验收人）。
- **通过条件**：TC-1 ~ TC-10 全过；`quality.errors = 0`；灰度期零误伤；能举出一例真实拦截。
- **不通过的回退路径**：
  1. 立即 `MIO_DIFF_GATE=off` 全量放行（不需回滚代码）。
  2. 按失败用例定位到批次（A/B/C），只回退该批次。
  3. 若判据本身设计有误（如误伤面过大），改判据后重跑 TC 集，**不改**「只在明确假完成上拦截」这一总原则。
- **失败排查顺序**：先查 `run.diff_summary.reason`（判定原因码）→ 再查 `Event`/`HistoryEvent` 是否有记录（可见性）→ 最后查前端渲染分支。
