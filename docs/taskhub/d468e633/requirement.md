# 需求规格 · 窄门禁（空 diff 拦截）

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.1 |
| 契约版本 | 1.0 |
| 状态 | approved |
| 最后更新 | 2026-10-20 16:32 |
| 负责人 | 项目作者（单人） |
| 关联任务 | d468e633 |
| 关联决策 | `docs/taskhub/product-decision-20261020.md` |
| 关联评估 | `docs/taskhub/product-evaluation-20261020.md` |

## 背景与问题

现状（2026-10-20 审计实测，非文档转述）：

- 生产库 `C:\Users\admin\.mio_taskhub\taskhub.db` 共 1928 任务 / 5170 次运行；运行中 `exit_code=1`（失败）4984 次，占 **96.5%**；任务 CANCELLED 1676 条（86.7%），其中 1571 条带 `failed_at`，实为失败后被自动清理。
- 全仓**无变更识别逻辑**：grep `git diff` / `changed_files` 在 `mio_taskhub/` 源码内无匹配；`Run` 模型（`models.py:262-274`）无任何改动字段；`GitRef`（`models.py:283-288`）只存分支引用，不存 diff。
- 全仓**无测试执行器**：`test` 仅为一种文档 kind（`doc_chain.py:31`、`doc_lifecycle.py:48-51`），不是运行器。
- `submit_result` 成功路径（`api/runs.py:271-318`）只校验三件事：Run 所有权、状态可提交、ReadEvidence 版本一致；随后**直接采信 Agent 自报的 `success: bool`**（`api/runs.py:282`）。
- 读证指纹（`read_evidence.py:68-79`）**只覆盖被读文档，不覆盖代码**。

结论：当前「完成」这一状态**只代表 Agent 自己说完成了**。系统没有任何服务端可验证的证据能区分「真干完了」与「什么都没干但报告干完了」。评估结论把它记为 P1-3「验收空心化」：宣称「提升产出质量」缺依据，179 个 COMPLETED 任务也无收益佐证。

本需求要补的正是这一处：**用服务端自己采集的改动证据，替代 Agent 自报作为「完成」的前置条件**。

## 目标与非目标

### 目标（可衡量）

- G-1 「声明成功但零改动」的提交被服务端拒绝，**拒绝率 100%**（在 `MIO_DIFF_GATE=block` 且任务未豁免的前提下）。
- G-2 拦截对**人可见**：被拦事件在 4 秒内出现在任务详情的「动态」tab，并以红色 toast 提示。
- G-3 合法完成**零误伤**：灰度期内被误拒的合法完成数为 0。
- G-4 误伤可即时回退：出现误伤时，改一个环境变量即可全量放行，**不需要回滚代码或数据**。

### 非目标（明确排除，防止范围蔓延）

- N-1 **不实现测试执行器**：本期不真跑 pytest。跑测试要先解决「跑哪个测试、怎么隔离工作区、耗时上限、失败如何归因」，属独立立项。
- N-2 **不把 diff 全文入库**：只存文件数、增删行数、路径列表；不存 patch 内容。
- N-3 **不新增前端面板**：全部复用既有 TaskDetail 抽屉与完成报告区域。
- N-4 **不改既有状态机语义**：不新增任务状态、不改合法转换表。
- N-5 **不改动 Agent 侧调用协议**：Agent 仍调同一个 `submit_result`，只是可能收到 422。
- N-6 **不引入 CI 集成**：不接外部 CI 平台。

## 功能需求

| 编号 | 需求描述 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-1 | 服务端**自行采集**本次 run 期间工作区的改动证据，不采信 Agent 自报：run 创建时拍「前」快照（`git rev-parse HEAD` + `git status --porcelain`），submit 时拍「后」快照 | P0 | 快照由服务端发起；Agent 在 submit body 中报的任何改动信息都不参与判定 |
| FR-2 | 判定「本次有改动」= `head_after ≠ head_before` **或** `porcelain_after ≠ porcelain_before` **或** 增删行数 > 0 | P0 | 三条判据任一命中即算有改动；只比 HEAD 会漏「改了没 commit」，只比工作区会漏「已 commit」，两条快照缺一不可 |
| FR-3 | 当 `success=true` 且判定为**零改动**且任务**未豁免**时，返回 422 且错误码 `run.empty_diff` | P0 | 拒绝时**不改变 run 状态**（不置 finished、不写 exit_code）、**不消耗 attempt**、**不触发任务状态流转** |
| FR-4 | 豁免与 fail-open 规则：以下任一成立即**跳过判定**（只写标注不拦）：`workspace` 为空、workspace 非 git 仓库或 git 不可用、`task_kind ∈ {idea_review, change_tracking}`、`diff_gate_exempt=true`、同一 workspace 存在 ≥2 个活跃 run | P0 | 上述 5 种场景下 submit **返回 200**，且标注中写明跳过原因；采集失败绝不导致拦截 |
| FR-5 | 拒绝次数上限：`MAX_REJECTIONS=2`。同一 run 内被拒 2 次以内可继续重试（不消耗 attempt）；达到上限则判本次执行失败（`exit_code=1`、state=finished、reason=`empty_diff_repeated`），交既有重试机制处理 | P0 | 连续拒绝达上限后任务进入既有 retry/failed 流程；不存在无限重试路径 |
| FR-6 | 拦截对**人可见**，三条链路同时落：① WebSocket 事件 `run.result_rejected` → 右上角红色 toast；② `HistoryEvent(type="rejected")` → 「动态」tab 红色「已拦截」行；③ `Run.annotations` → 「完成报告」中该次执行的红色拦截卡片 | P0 | 拦截后**查库确认** `Event` 与 `HistoryEvent` 两张表均有记录（不能只看界面）；界面上三处都能看到 |
| FR-7 | 标注项（测试未执行、diff 质量存疑等）**只提示不拦**，且不弹 toast；文案必须写明「不影响放行」 | P0 | 标注只出现在「完成报告」；不产生任何 toast；文案含「不影响放行」字样 |
| FR-8 | 独立错误码 `run.empty_diff` + 三段式专用文案（发生了什么 / 为什么 / 下一步），**不复用**通用 422 文案「数据校验失败，请检查必填项」 | P0 | 422 detail 含 `code`/`message`/`hint`/`files_changed`/`rejections`/`max_rejections`；前端能按 code 分支渲染 |
| FR-9 | 环境开关 `MIO_DIFF_GATE`：`off` \| `warn` \| `block`，**默认 `warn`**（只标注不拦）；`off` 立即全量放行 | P0 | 灰度期默认 warn；切 block 前需 3–5 个真实 run 零误伤记录；改开关无需重启以外的任何操作 |
| FR-10 | 迁移幂等：新增列存在即跳过；**存量任务全部置 `diff_gate_exempt=1`**，保持迁移前行为不变 | P0 | 老库启动后新增列存在、存量任务豁免为真、迁移可重复执行不报错 |
| FR-11 | `GET /api/v1/tasks/{task_id}` 响应中 `runs[]` 增补 `diff_summary` / `annotations` / `rejections` 三个字段 | P0 | 任务详情接口能取到改动证据与标注，供前端渲染 |

## 非功能需求

| 维度 | 要求 |
|---|---|
| 性能 | 单次判定新增 ≤ 4 次 git 子进程调用（before 快照 2 次 + after 快照 2 次），端到端增量 P95 ≤ 300ms；git 调用超时 5s，超时按 fail-open 跳过 |
| 可靠性 | 判定逻辑为纯函数（给定 before/after 必得同一结论），可单测；git 异常、路径不存在、仓库损坏一律不抛出到 submit 主链路 |
| 安全 | 只读 git（`rev-parse` / `status` / `diff --numstat`），不执行任何写操作；不把 diff 全文写入日志；采集失败不泄露本地绝对路径到对外响应 |
| 兼容性 | 存量库可直接启动（迁移幂等）；存量任务行为不变（豁免）；`MIO_DIFF_GATE` 未设置时按 `warn` 处理 |
| 可观测 | 每次判定都留痕：`Run.diff_summary` 记 before/after 摘要；跳过判定必须写明原因，便于事后判断「为何没拦」 |
| 幂等 | 同一 run 重复提交同一结果，判定结果与拒绝计数不因重复请求而错乱；拒绝不写 run 终态 |
| 文档 | 契约变更走 `api` 文档并标版本；错误码进统一错误码表；FR-n 与 TC-n 双向可追溯 |

## 边界与约束

- 依赖既有读证门控：`submit_result` 的校验顺序固定为 **404 → 所有权 → 状态可提交 → ReadEvidence → 变更门禁**，变更门禁放最后（它要起 git 子进程，最贵）。
- 事务约束：`get_session`（`db.py:52-54`）退出时只 close 不 commit，**抛 422 前必须显式 `db.commit()`**，否则拦截事件随会话丢弃、人永远看不到。
- 广播约束：`events.py:43-48` 的 `MESSAGE_TYPES` 不含 `run`，事件必须用 `entity="task"`，否则广播成 `event_update` 被前端忽略。
- 分支与提交：本任务在 `task-d468e633` 分支上开发；push 前需 `spec` / `api` / `plan` 三份文档处于 approved。
