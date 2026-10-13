# 实施计划：claim 注入 lessons

## 文档信息

| 项 | 值 |
| --- | --- |
| 任务 ID | `28f8fceb` |
| 对应需求 | `docs/taskhub/28f8fceb/requirement.md` |
| 对应设计 | `docs/taskhub/28f8fceb/spec.md` |
| 预估工时 | 90 分钟 |
| 状态 | approved |
| 基线 commit | `1e7d00e`（分支 `task-516bd0c7`） |

## 实施步骤

### 步骤 1：新增 `mio_taskhub/api/task_lessons.py`

1. 定义 `_TEST_PATTERNS`：测试命令特征关键词列表（大小写不敏感）。
2. 实现 `_extract_test_hints(results, max_chars)`：
   1. 逐条 result 按句切分（中文句号 + 英文句号 + 换行）。
   2. 句中命中关键词则保留。
   3. 去重、按原出现顺序排列。
   4. 单条截断到 `max_chars`。
3. 实现 `_extract_file_layout(path_lists, max_chars)`：
   1. 取路径的目录前缀部分。
   2. 统计出现频次，降序排列。
   3. 取前 N 条并截断。
4. 实现 `_extract_pitfalls(failures, max_chars)`：取失败任务的 `review_result` 或 summary。
5. 实现主函数 `recent_lessons(db, workspace, project, limit=5, max_chars=200)`：
   1. 按 `workspace` 查近期已完成任务；workspace 为空时退化到 `project`。
   2. 关联取这些任务的 `Run.result`、`files`、`deliverables`。
   3. 调用上述三个提取函数。
   4. 全部异常捕获，返回 `{"test_hints": [], "file_layout": [], "pitfalls": [], "source_count": 0}`。

**注意**：`limit` 约束的是参与提取的任务数上限，`max_chars` 约束的是每个列表内单条长度上限，两者独立。

### 步骤 2：挂进 `read_evidence.build_claim_context`

1. 在函数返回 dict 中追加 `lessons` 键。
2. 调用 `recent_lessons`，**用独立 try/except 包裹**，异常时置空结构。
3. **不改动** `branch` / `required_reads` / `required_fr` / `documents` 任何既有键。

**核对点**：`api/tasks.py:302-306`（Run 复用路径）与 `api/tasks.py:331-335`（正常认领路径）都调用此函数并 `resp.update(...)`，改一处即覆盖两条路径。`claim.py` 本身不需要改。

### 步骤 3：接通 `memory_store`

1. 在 `api/runs.py` 的 `_handle_success` 成功路径末尾调用新函数 `_write_experience(task, run)`。
2. `_write_experience` 实现：
   1. 从 `run.result` 提取测试线索（复用步骤 1 的提取函数）。
   2. 从 `task.files` / `deliverables` 提取布局。
   3. 组装 `observations`，**每条前置项目名**（满足 project 子串匹配）。
   4. 调 `memory_store.add_entity(name, "experience", observations)`。
   5. 整个函数用 try/except 包裹，失败只 `logger.warning`，**不影响任务状态**。
3. 在 `recent_lessons` 中调 `search_entities([project], kind="experience")` 补充 `pitfalls`。

### 步骤 4：补测试

1. 新建 `tests/test_task_lessons.py`：
   1. `test_lessons_empty_when_no_history`
   2. `test_lessons_extracts_test_hints`
   3. `test_lessons_truncates_long_text`
   4. `test_lessons_respects_limit`
   5. `test_lessons_falls_back_to_project`
   6. `test_lessons_never_raises`
2. 扩充 `tests/test_read_evidence.py`：
   1. `test_claim_context_includes_lessons`
   2. `test_claim_context_survives_lessons_error`（mock `recent_lessons` 抛异常，断言 `build_claim_context` 仍正常返回）
3. 扩充 claim 端点测试：断言响应含 `lessons` 键。

### 步骤 5：验收自查

1. `grep -c "HTTPException(422"` 改动前后数量一致（**不得增加**）。
2. `grep -c "HTTPException(409"` 同上（不加阻断）。
3. `Run` 模型字段数不变（不加 `base_sha` / `post_sha`）。
4. 跑全量 pytest，对比基线：`1235 passed, 4 failed`（4 个 `test_spinning_guard` 为 pre-existing）。
5. 检查 `memory_store.health()` 返回正常，`~/.mio_taskhub/` 下 JSONL 有实际写入。

## 改动清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `mio_taskhub/api/task_lessons.py` | 新增 | 约 120 行，只读查询 + 三个提取函数 |
| `mio_taskhub/read_evidence.py` | 修改 | `build_claim_context` 追加一个键 |
| `mio_taskhub/api/runs.py` | 修改 | 成功后调用 `_write_experience` |
| `tests/test_task_lessons.py` | 新增 | 6 个用例 |
| `tests/test_read_evidence.py` | 修改 | 2 个用例 |

## 风险与应对

| 风险 | 概率 | 应对 |
| --- | --- | --- |
| 提取逻辑拖慢 claim | 低 | 单次查询 + 内存提取；`limit` 限住任务数 |
| lessons 内容过长撑爆响应 | 中 | `limit` × `max_chars` 双重限制，总量上限约 1000 字 |
| memory_store JSONL 不可写 | 低 | try/except 降级，记日志 |
| 经验内容质量差污染后续任务 | 中 | 首版不做审核，观察一段时间后再定 |
| 实现时不小心加了 422 阻断 | 中 | 步骤 5 有 grep 自查项 |
| 后台自动派单路径（`background.py:418`）拿不到 lessons | 高 | 本次不处理，见下方说明 |

## 已知缺口（本次不做）

| 缺口 | 说明 |
| --- | --- |
| 后台自动派单无 lessons | `background.py:418` 走 `_claim_for`，不经 `build_claim_context`。该路径占实际派单的相当比例，若要覆盖需在 `task_assigned` 事件里另行注入 |
| MCP 侧无 lessons 工具 | agent 若主要走 MCP，需补 `taskhub_lessons` 工具才能拿到 |
| 经验准确性评估 | 无指标判断 lessons 是否真的提升了代码质量，需后续对照实验 |

## 回滚方案

| 改动 | 回滚方式 |
| --- | --- |
| `task_lessons.py` | 删除文件（无其他模块强依赖） |
| `build_claim_context` 追加键 | 删除 `lessons` 一行即恢复 |
| `_write_experience` 调用 | 删除调用行；已写入的 JSONL 经验无害 |
| 测试 | 删除新增文件、回退测试文件改动 |

全部改动为**纯增量**，无既有逻辑替换，回滚不影响既有功能。
