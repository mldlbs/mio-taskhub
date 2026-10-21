# 变更记录：任务 28f8fceb

## 2026-10-10 实现完成

本任务分三轮实施：FR-1~ FR-3（lessons 注入 + 经验沉淀）、两条派单路径补齐、FR-4（质量可见化）。

### 新增文件

| 文件 | 说明 |
| --- | --- |
| `mio_taskhub/api/task_lessons.py` | 同项目参考信息提取（test_hints / file_layout / pitfalls + source_count）与经验写入。**不 import fastapi，任何异常一律降级** |
| `tests/test_task_lessons.py` | 21 个用例，覆盖提取逻辑、截断、限条、退化匹配、异常不外泄、经验沉淀 |
| `tests/test_delivery_quality.py` | 10 个用例，覆盖 FR-4 指标的正确性与「不参与判定」 |

### 修改文件

| 文件 | 说明 |
| --- | --- |
| `mio_taskhub/read_evidence.py` | `build_claim_context` 新增 `lessons` 键；新增 `_lessons_for`；补 `logging` 导入 |
| `mio_taskhub/api/runs.py` | `_handle_success` 末尾调 `write_experience`（纯旁路）；补 `logging` 导入 |
| `mio_taskhub/background.py` | idle 派单的 `task_assigned` 事件载荷加 `lessons`，异常降级为 `{}` |
| `mio_taskhub/mcp_server.py` | claim 工具描述 + 服务 instructions 补充 lessons 说明（可发现性） |
| `mio_taskhub/api/board.py` | 新增 `_delivery_quality()`；`overview` 与 `summary` 两个端点均挂 `delivery_quality` 字段 |
| `tests/test_read_evidence.py` | 新增 2 个用例（lessons 注入 + 异常降级） |
| `tests/test_mcp_server.py` | 新增 2 个用例（MCP 能拿到 lessons + 异常不阻断） |

### 设计要点

| 点 | 说明 |
| --- | --- |
| 挂载点选 `build_claim_context` | claim 的两条响应路径（已有 Run 复用 / 正常认领）共用该函数，改一处覆盖两条。**`claim.py` 一字未改** |
| 不依赖 git | 只读 DB 的 task / run —— 真实库有 16 条 workspace 根本不是 git 仓库 |
| lessons 只送不拦 | 异常降级为空结构；`recent_lessons` 顶层 try/except 包住全部 |
| MCP 无需新工具 | `taskhub_claim` 走同一 REST 端点且返回完整 claim 响应，lessons 自动透传 |

### 实现中抓到的三个问题

| # | 问题 | 修法 |
| --- | --- | --- |
| 1 | **失败任务永远查不到**：首版用「已完成任务 ids」查 FAILED，但两状态互斥，`pitfalls` 恒为空 | 改为 `_query_failures` 独立查询 |
| 2 | **短测试结论被丢弃**：沿用审计脚本的 `len(result)>20` 阈值，「测试：npm run build 成功」（17 字）整条丢失 | 改为 `>=4`，与提取函数最小句长一致 |
| 3 | **MCP 层「送到了但没人看」**：工具描述未提及 lessons，agent 拿到 JSON 多半直接开工 | 描述与 instructions 显式提示「动手前先看 test_hints」，并说明空数组不是错误 |

### 验收结果

| 项 | 基线 | 实测 | 判定 |
| --- | ---: | ---: | --- |
| `HTTPException(422)` 数量 | 80 | 80 | 未增加 |
| `HTTPException(409)` 数量 | 14 | 14 | 未增加 |
| `Run` 模型字段数 | 12 | 12 | 未加 base_sha / post_sha |
| 全量 pytest | 1235 passed / 4 failed | 1270 passed / 4 failed | 新增 35 用例全通过，无回归 |

4 个失败（`test_spinning_guard` 的 webhook_guard）经 `git stash` 对照确认为**改动前既已存在**，与本次无关。

### 端到端验证

| 项 | 验证内容 | 结果 |
| --- | --- | --- |
| REST claim 注入 | 同 workspace 已完成任务含 pytest 证据 | 200，`lessons` 带回 test_hints 与 file_layout |
| MCP claim 注入 | agent 主要入口 | lessons 随响应返回；打桩抛异常时仍 200 且降级为空 |
| 经验沉淀 | `submit_result` 200 后 | `search_entities` 可查到，observations 含项目名与测试命令 |
| FR-4 指标 | 真实库 284 条 FINISHED run | 30.6% 含测试证据、1.1% 列出改动文件 —— 指标有观察价值 |

### 未做

- FR-4 的前端展示（后端字段已就绪，前端未接）
- lessons 的效果量化评估（需真实任务对照实验，当前只有观察指标）
- MCP 侧未加 lessons 专用工具（走 claim 返回即可，加了属无需求扩展）
