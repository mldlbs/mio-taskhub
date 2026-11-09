# 测试验收：能力优化验证轮 v0.7

## 文档信息

| 项 | 值 |
|---|---|
| 任务 ID | c8316737 |
| 文档类型 | test |
| 版本 | v1.0 |
| 日期 | 2026-11-04 |
| 测试环境 | Windows / Python 3.12 venv / pytest（隔离库 MIO_TEST_DB_PATH） |

## 2. 测试范围

### 2.1 各层覆盖

| 层 | 测什么 | 不测什么 |
|---|---|---|
| 单元 | FR-n 映射构建（_build_fr_map）、路径匹配（_match_fr）、doc_paths 解析（_load_doc_paths） | git 行级 diff（移交 Next） |
| 集成（TestClient） | 路由 404/403/405/200 全状态码；run 证据聚合；工作树证据 fail-open；只读性（不落事件不改状态） | 真实 git 仓快照（diff_gate.snapshot 在 test_run_diff_gate 已覆盖） |
| E2E | 不做（轮内封顶不启 E2E 层） | 生产环境冒烟（exe 重打包属分发流程，不在本轮） |

### 2.2 明确不测

- 行级 diff（A-P6：超 0.5 人日口径，移交 Next）。
- 并发场景（单开发者自用，E5 证实评审/多角色环节未启用）。
- git 不可用环境（diff_gate fail-open 语义已由既有测试覆盖）。

## 3. 用例清单（TC-n ↔ FR-n 追溯）

| TC | 覆盖 FR | 前置 | 步骤 | 预期 | 结果 |
|---|---|---|---|---|---|
| TC1 | FR-07 | 库中无该任务 | GET /tasks/nonexist/diff | 404，detail.code=task.not_found | passed |
| TC2 | FR-07 | 任务+run（diff_summary.after 含 2 路径）+requirement 文档含「### FR-01」与 src/foo.py | GET /tasks/t7a00001/diff | 200；foo.py 标注 FR-01；bar.md 标注 UNMAPPED；source=run:id | passed |
| TC3 | FR-07 | 任务指向空目录（非 git 仓） | GET /tasks/t7a00002/diff | 200；reason=no_changes；changed_files=[] | passed |
| TC3b | FR-07 | 任务 workspace 为空字符串 | GET /tasks/t7a00003/diff | 200；reason=no_workspace | passed |
| TC4 | FR-07 | 任务 workspace 指向不存在目录 | GET /tasks/t7a00004/diff | 403；detail.code=task.workspace_unreadable | passed |
| TC5 | FR-07 | 任务存在 | POST/PUT/DELETE /tasks/{id}/diff | 三者均 405 | passed |
| TC6 | FR-07 | 任务+run 持久化 | GET 后重读 run.diff_summary 与 task.state | 与调用前完全一致（只读） | passed |

覆盖核对：requirement v1.0 中 FR-01~FR-06 为只读实验/信息收集（E1/E5/E6 已执行并落证据报告于 evidence/，E2/E3/E4 待真人访谈与观察窗口），FR-07 为本轮唯一功能代码需求——已由 TC1-TC6 全覆盖。

## 4. 验收标准

- FR-07 全部断言通过（TC1-TC6）。
- 只读性验证：不触发状态机、不写质量分、不落事件、不改 run/task 字段（TC6）。
- 棘轮：用例总数只升不降（+7）。
- 回归：diff_gate 与 API 既有行为零破坏。

## 5. 回归验证

| 范围 | 结果 | 说明 |
|---|---|---|
| tests/test_task_diff.py（新增） | 7 passed | FR-07 用例 |
| tests/test_run_diff_gate.py + test_diff_gate_stats.py + test_api.py | 58 passed | 既有 diff_gate 与 API 行为无回归 |

- 棘轮核对基准：执行前全量基线 1235（2026-10-10 口径）。

## 6. 边界与遗留

- FR 映射是确定性文本启发式（路径 token ↔ 最近 FR 标题），映射缺失标 UNMAPPED，不猜、不调 LLM。
- diff_gate.snapshot 采集失败时 fail-open：返回 run 级证据子集，不 500。

## 7. 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-11-04 | FR-07 实现 + 7 用例全过 + 58 回归全过 |
