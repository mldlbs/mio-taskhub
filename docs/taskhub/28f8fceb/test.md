# 测试与验收：claim 注入 lessons

## 文档信息

| 项 | 值 |
| --- | --- |
| 任务 ID | `28f8fceb` |
| 对应需求 | `docs/taskhub/28f8fceb/requirement.md` |
| 对应设计 | `docs/taskhub/28f8fceb/spec.md` |
| 对应计划 | `docs/taskhub/28f8fceb/plan.md` |
| 版本 | v1.0 |
| 状态 | passed |

## 验收标准

### 一、FR-1：同类任务参考查询

| 编号 | 验收项 | 判定方式 |
| --- | --- | --- |
| AC-1.1 | 给定有历史任务的 workspace，返回含三类线索的 `lessons` | 单测断言 `test_hints` / `file_layout` / `pitfalls` 非空 |
| AC-1.2 | 无历史任务时返回空数组且 `source_count=0` | 单测 `test_lessons_empty_when_no_history` |
| AC-1.3 | 单条文本被截断到 `max_chars` | 单测构造超长 result 后断言长度 |
| AC-1.4 | 条数不超过 `limit` | 单测构造 10 条历史后断言 ≤ limit |
| AC-1.5 | workspace 为空时退化用 `project` 匹配 | 单测 `test_lessons_falls_back_to_project` |
| AC-1.6 | 任何异常都不外泄 | 单测 `test_lessons_never_raises` |

### 二、FR-2：claim 响应注入

| 编号 | 验收项 | 判定方式 |
| --- | --- | --- |
| AC-2.1 | claim 响应含 `lessons` 字段 | 端点测试断言键存在 |
| AC-2.2 | **两条 claim 路径都含该字段** | 分别测「已有 Run 复用」与「正常认领」 |
| AC-2.3 | 注入抛异常时 claim 仍返回 200 | mock `recent_lessons` 抛异常，断言响应正常 |
| AC-2.4 | 既有字段语义未变 | `required_reads` / `documents` / `branch` / `required_fr` 保持原样 |

### 三、FR-3：经验沉淀与复用

| 编号 | 验收项 | 判定方式 |
| --- | --- | --- |
| AC-3.1 | 任务完成后经验被写入 | 检查 `~/.mio_taskhub/` 下 JSONL 有新条目 |
| AC-3.2 | 写入内容含项目名 | 断言 `observations` 中含 project 字符串 |
| AC-3.3 | 同项目后续任务能检索到 | `search_entities([project], kind="experience")` 有结果 |
| AC-3.4 | 写入失败不影响任务状态 | mock 写异常，断言任务仍正常完成 |

### 四、FR-4：可见化（最低优先级）

| 编号 | 验收项 | 判定方式 |
| --- | --- | --- |
| AC-4.1 | 指标可查询 | 接口返回含「近期自报含测试证据比例」等数值 |
| AC-4.2 | **不参与任何流程判定** | grep 确认该模块无 `HTTPException` |

### 五、约束自查（防回退到硬约束）

| 编号 | 验收项 | 判定方式 | 基线 |
| --- | --- | --- | --- |
| AC-5.1 | 不新增 422 阻断 | `grep -c "HTTPException(422"` 前后对比 | 不得增加 |
| AC-5.2 | 不新增 409 阻断 | `grep -c "HTTPException(409"` 前后对比 | 不得增加 |
| AC-5.3 | 不给 Run 加代码版本字段 | `Run` 模型字段数对比 | 12 个不变 |
| AC-5.4 | 不要求必填字段 | 检查 claim / submit 请求体无新增 required 参数 |

### 六、回归

| 编号 | 验收项 | 判定 |
| --- | --- | --- |
| AC-6.1 | 全量 pytest 无新增失败 | `1235 passed, 4 failed`（4 个 `test_spinning_guard` 为 pre-existing） |
| AC-6.2 | `memory_store.health()` 正常 | 不再是死代码状态 |

## 测试范围

### 覆盖范围

- `task_lessons.py` 的三个提取函数与主查询函数
- `build_claim_context` 的 `lessons` 注入与异常降级
- claim 端点两条路径的响应结构
- `memory_store` 写入与检索的连通性

### 不覆盖范围

- 提取准确率的量化评估（属效果验证，需真实任务对照实验）
- 后台自动派单路径（`background.py:418`，本次不做）
- MCP 侧 lessons 工具（本次不做）
- 性能压测（数据量小，暂不需要）

## 用例清单

| 用例编号 | 对应 FR | 用例名称 | 前置条件 | 步骤 | 预期结果 |
| --- | --- | --- | --- | --- | --- |
| TC-1 | FR-1 | 无历史时返回空集合 | workspace 下无已完成任务 | 1. 调 `recent_lessons`<br>2. 检查返回值 | 四个字段分别为空数组 / 0；不抛异常 |
| TC-2 | FR-1 | 提取测试线索 | 构造含 `pytest ... passed` 的 result | 1. 建任务并置为 completed<br>2. 写入 result<br>3. 调 `recent_lessons` | `test_hints` 含该命令行 |
| TC-3 | FR-1 | 超长文本被截断 | result 含 500 字测试描述 | 1. 同 TC-2 写入<br>2. 调 `recent_lessons(max_chars=100)` | 单条长度 ≤ 100 |
| TC-4 | FR-1 | 条数受限 | 构造 10 个已完成任务 | 1. 调 `recent_lessons(limit=3)` | 三个列表各 ≤ 3 条 |
| TC-5 | FR-1 | 退化到 project 匹配 | 任务 workspace 为空、project 非空 | 1. 调 `recent_lessons(workspace="", project="mio-taskhub")` | 仍能取到历史线索 |
| TC-6 | FR-1 | 异常不外泄 | mock DB 查询抛异常 | 1. 调 `recent_lessons` | 返回空结构，不抛异常 |
| TC-7 | FR-2 | claim 响应含 lessons | 有可领任务 | 1. POST claim<br>2. 检查响应 JSON | 含 `lessons` 键 |
| TC-8 | FR-2 | Run 复用路径也含 lessons | 已存在 active Run | 1. 再次 claim 同一任务<br>2. 检查响应 | 含 `lessons` 键 |
| TC-9 | FR-2 | 注入异常不阻断 claim | mock `recent_lessons` 抛异常 | 1. POST claim | 返回 200，响应正常 |
| TC-10 | FR-2 | 既有字段未受影响 | 任务已登记 requirement | 1. POST claim<br>2. 检查既有键 | `required_reads` / `documents` / `branch` 与改造前一致 |
| TC-11 | FR-3 | 完成后写入经验 | 任务成功完成 | 1. 提交结果<br>2. 检查 JSONL | 新增 `experience` 类型条目 |
| TC-12 | FR-3 | 经验含项目名 | 同 TC-11 | 1. 读 JSONL 条目 | `observations` 含 project 字符串 |
| TC-13 | FR-3 | 同项目可检索 | 同 TC-11 | 1. `search_entities([project], kind="experience")` | 返回该条目 |
| TC-14 | FR-3 | 写入失败不影响任务 | mock `add_entity` 抛异常 | 1. 提交结果 | 任务仍正常 completed |
| TC-15 | FR-4 | 指标可查 | 有多个已完成任务 | 1. 调指标接口 | 返回含测试证据比例等数值 |
| TC-16 | 约束 | 无新增 422 | — | 1. `grep -c "HTTPException(422"` | 与基线一致，不增加 |
| TC-17 | 约束 | Run 字段未变 | — | 1. 查 `PRAGMA table_info(run)` | 仍是 12 个字段 |

## FR ↔ TC 追溯矩阵

| 需求 | 覆盖用例 | 覆盖状态 |
| --- | --- | --- |
| FR-1 | TC-1, TC-2, TC-3, TC-4, TC-5, TC-6 | 已覆盖 |
| FR-2 | TC-7, TC-8, TC-9, TC-10 | 已覆盖 |
| FR-3 | TC-11, TC-12, TC-13, TC-14 | 已覆盖 |
| FR-4 | TC-15 | 已覆盖（最低优先级） |
| 约束自查 | TC-16, TC-17 | 已覆盖 |

## 执行前置条件

1. `requirement` / `spec` / `plan` 均已 `approved`。
2. 任务推进到 `ready` 前，`test` 文档需 approved（`test` 生命周期：`planned → passed/failed`）。
3. 执行前记录基线：`grep -c "HTTPException(422"` 与 `Run` 字段数。
