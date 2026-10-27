# TH-01 代码与设计审查结论

## 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 最后更新 | 2026-10-27 14:10 |
| 状态 | draft |
| 任务 | 2f0be5e7 |
| 审查对象 | `task-2f0be5e7` @ `7a68dd7`（18 文件 / +2450） |
| 审查方式 | 读 diff + 全量测试 + 契约冒烟实跑 + 冲突场景构造 |
| 结论 | **通过（Approve with follow-ups）** |

---

## 一、验证证据

| 项 | 命令 / 方式 | 结果 |
|---|---|---|
| 新增用例 | `pytest tests/test_mio_route_outcome.py -q` | 31 passed |
| 全量回归 | `pytest -q` | 1481 passed / 2 skipped / 4 deselected |
| 前端构建 | `npm.cmd run build`（web/） | 构建成功（273 modules） |
| 契约冒烟 | `mio_runtime.contract_check()` | 8 探针全 true（含 `task_route`、`experience_list`、`mio_cli_flags`） |
| 上游补丁实测 | 临时 MIO_HOME 跑 `task route --persist-query` | 默认不写查询日志；带 flag 写入且 `agent` 为真实 worker；flag 不污染查询文本 |
| 错误分支 | 伪 CLI 注入 超时 / 坏 JSON / 缺字段 | 分别降级为 `timeout` / `schema_error`，claim 不失败 |

## 二、验收标准逐条结论

| AC | 结论 | 依据 |
|---|---|---|
| AC-1 route schema | 通过 | 断言 `count` / `routes[].experienceId` / `agentHealth.best`，**无** `confidence`；ContractJob 固定 schema |
| AC-2 降级 | 通过 | 四类故障注入均 200，claim 正常派活 |
| AC-3 本地优先 | 通过 | 两条同优先级任务下，route 命中不改变被领取的任务 |
| AC-4 幂等 | 通过 | `MioOutbox.key` 唯一；二次入队返回 False；重启后行仍在 |
| AC-5 retry 各自留痕 | 通过 | 每次 submit 生成独立 run 与独立 outbox 行（`traceId` 含 run_id） |
| AC-6 三态写回 | 通过 | success / failure / aborted 各一例；`traceId` 可反查 `RouteDecision` |
| AC-7a 跨项目经验 | **部分** | 通路已验（伪数据）；真实跨项目命中需环境数据复核 |
| AC-7b confirm 后进 route | **部分** | 通路已验；真实 confirm→route 串联需环境复核 |
| AC-7c 排序变化 | 不适用 | 显式排除，未实现亦未验收 |
| AC-8 写回失败可恢复 | 通过 | 失败置 `failed` 并退避；runtime 整体不可用时不消耗尝试次数；恢复后补发 |
| AC-9 空 project | 通过 | 不启动子进程、不写查询日志、outbox 置 `dead` |

## 三、发现的问题与处理

### P1｜上游补丁会被 npm 升级覆盖

- 事实：`bin/mio.js` 的两处改动（`--persist-query`、`--agent-id`）不在仓库内，`npm i -g` 会覆盖。
- 现状：`ContractJob` 新增 `mio_cli_flags` 探针，读 CLI 源文件确认标志存在，缺失即告警。
- 建议：条件成熟时把补丁提交上游；在此之前保留探针。

### P1｜新表与后台 job 需重启 hub 才生效

- 事实：当前运行的 hub 进程是旧代码，`route_decision` / `mio_outbox` 两张表尚未创建；`MioOutcomeJob` 未启动。
- 影响：重启前的提交不会真正投递（写回路径在旧进程里根本不存在，无副作用）。
- 建议：发布后重启 hub，确认 `mio_outbox` 表存在且 `MioOutcomeJob` 在 thread registry 中。

### P2｜pending 查询有 100 条上限

- 事实：`find_reuse_record` 先查 pending（limit 100），再查全量（limit 100）用于区分「不存在」与「已确认」。
- 影响：积压超过 100 条时，较旧的 pending 确认会误报 404。
- 建议：后续给 `experience list` 加分页/按 id 查询参数；当前量级（auto-claim 逐条确认）风险低。

### P2｜失败来源不可确认是硬约束，不是缺陷

- 事实：失败结果的 auto_claim 写 `outcomeImproved=false`，即使 confirm 也不满足 `verified` 三条件。
- 处理：接口层 409 拦截 + UI 禁用按钮，避免「确认了却永远不生效」的静默陷阱。
- 建议：文档与 UI 文案已写明「确认 = 值得复用」，避免被理解为任务判定。

### P3｜claim 的可领取性与 stage 耦合（既有语义）

- 事实：`pick_candidate_task` 只挑 `stage=ready`；本次因先推进到 `implementing` 导致队列领取返回 204，改用 `task_id` 点名领取成功。
- 归属：既有的调度语义，非本次引入。记录以便后续 agent 不要「先推阶段后领活」。

### P3｜前端无 E2E

- 事实：仅验证 `npm run build` 与接口契约，未做浏览器交互测试。
- 建议：后续引入时优先覆盖「不可确认条目禁用」「确认后刷新」两条路径。

## 四、设计一致性检查

| 检查项 | 结果 |
|---|---|
| route 是否参与排序 | 否：`pick_candidate_task` 一行未改，route 在选定后调用 |
| runtime 失败是否影响业务 | 否：claim / submit 均有 try/except，outbox 入队在 commit 之后 |
| 是否双写 runtime 存储 | 否：只读 `MIO_HOME` + 白名单 CLI；不写 `memory.jsonl` |
| 幂等归属 | taskhub 侧（`MioOutbox.key`），与 runtime 无幂等的事实一致 |
| 失败是否被改写成成功 | 否：`outcomeImproved=false` 强制 409，不可确认 |
| 是否引入 LLM 成本 | 否：只用 `task route` / `record-outcome` / `experience list|confirm` |

## 五、结论与建议后续

**结论**：实现与已批准的需求/规格/契约一致，核心闭环（读建议 → 本地派发 → 异步写回 → 人工确认 → 再读）可验证成立；降级与幂等边界处理到位。建议通过，附三条后续：

1. **P1**：重启 hub 验证新表与投递 job（发布检查项）。
2. **P2**：pending 分页或按 id 查询，消除 100 条上限。
3. **TH-02**：`trace.query` 工具级错误归因，补齐「失败死在哪一步」的排障闭环。
