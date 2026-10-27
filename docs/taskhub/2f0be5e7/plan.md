# TH-01 历史决策闭环 实现计划（Plan）

## 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 最后更新 | 2026-10-27 11:31 |
| 状态 | approved |
| 负责人 | opencode |
| 任务 | 2f0be5e7（分支 `task-2f0be5e7`） |
| 上游需求 | requirement v1.0（FR-1 ~ FR-11，已 approved） |
| 上游规格 | spec v1.0（已 approved） |
| 上游契约 | api v1.0（已 approved，6 个接口） |
| 运行时版本 | `mio-agent-runtime@0.18.0` |

---

## 概述

按 spec 顺序实施，**每一步可独立验证、可单独提交**，代码与测试注释统一引用本任务的 FR 编号（满足 pre-push 的 FR 追溯门控）。

顺序原则：**先补上游能力 → 再建本地数据模型 → 再改读路径 → 再改写路径 → 再接异步投递 → 最后接 UI 与冒烟**。理由是写路径依赖 outbox 表，outbox 依赖数据模型；读路径（claim route）与写路径可并行但读路径更简单，先做能最早暴露契约问题。

总量估计：后端 ~600 行（含新表与新端点），前端 ~150 行，测试 ~400 行。

---

## 前置条件

| 项 | 要求 |
|---|---|
| 分支 | 从 `main` 切 `task-2f0be5e7`（pre-push 钩子据此关联任务） |
| 运行环境 | 本地 taskhub 服务在 `127.0.0.1:48620` 运行；`MIO_HOME` 指向真实 runtime（默认 `~/.mio-intelligence`） |
| 上游补丁 | `D:\node_global\node_modules\mio-agent-runtime\bin\mio.js` 可写；改动前备份为 `mio.js.bak-YYYYMMDD` |
| 测试 | `.venv` 内可跑 `python -m pytest`；前端 `web/` 下 `npm.cmd run build` |
| 门控 | 动手前已通过 Read Evidence 读取 requirement/spec/api（claim 时带 `run_id`） |

---

## 实施步骤

### Step 1 上游补丁：`mio task route --persist-query`（FR-1）

- 文件：`bin/mio.js`（全局安装包），改动点 `taskCommand` 的 route 分支。
- 改动：`persistQuery: flagPresent(flags, '--persist-query')`，即**不传仍为 false**；usage 文本补一行说明「默认只读，不写 queries.jsonl」。
- 备份：`mio.js.bak-20261027`；改完 `node --check bin/mio.js`。
- 验证：
  - `mio task route "x" --json` 后 `queries.jsonl` 行数不变；
  - `mio task route "x" --persist-query --json` 后行数 +1，且 `agent` 为 `cli`、`project` 为传入值。
- 风险：npm 升级会覆盖补丁 → Step 9 的 ContractJob 能力探测负责提前告警。

### Step 2 适配层：`mio_taskhub/mio_runtime.py` 4 个函数（FR-2、FR-8）

- 新增：
  - `task_route(task, agent, project, timeout=1.5)` → `dict | None`
  - `task_outcome(payload, timeout=3.0)` → `dict | None`（CLI 需 `--yes`）
  - `experience_pending(project=None, limit=20)` → `dict | None`
  - `experience_confirm(record_id, by="taskhub", timeout=3.0)` → `dict | None`
- `CLI_SUBCOMMANDS` 增加：`("task","route")`、`("task","record-outcome")`、`("experience","list")`、`("experience","confirm")`。
- 统一走既有 `_json_stdout`，捕获超时/非 JSON/非零退出 → 返回 `None` + 日志，绝不抛到调用方。
- 验证：单测用 monkeypatch 模拟 CLI 成功 / 超时 / 坏 JSON / 缺失 四种返回。

### Step 3 数据模型：`RouteDecision` 与 `MioOutbox`（FR-4、FR-5）

- 文件：`mio_taskhub/models.py`，按 spec 2.1 声明两张表；`key` 建 `unique=True` 索引。
- 建表：依赖 `SQLModel.metadata.create_all`（无 ALTER、无存量迁移）。
- 验证：`pytest` 内建表后插入重复 `key` 必须抛 `IntegrityError`。

### Step 4 claim 注入 route（FR-2、FR-3、FR-4）

- 文件：`mio_taskhub/api/tasks.py` 的 `claim_task`。
- 改动：
  1. 新领取分支在 `resp.update(build_claim_context(task))` 之后调用 `task_route`；
  2. `task.project` 为空 → 不启动子进程，`degraded_reason=empty_project`；
  3. 写 `RouteDecision`（`id=rd-<run_id>`），写入包在 try/except，失败只记日志；
  4. `resp["route"]` 按 api 契约组装，可为 `null`；
  5. **existing run 分支**只回填已存在的 `RouteDecision`，不新建。
- 不改：`pick_candidate_task`、`build_relevance`、`atomic_claim` 一行不动。
- 验证：route 命中与否不改变被领取任务（构造两条队列对照）。

### Step 5 submit 入队（FR-5、FR-6）

- 文件：`mio_taskhub/api/runs.py` 的 `submit_result`。
- 改动：在 `db.commit()` / `db.refresh()` 之后、构造返回值之前，调新增的 `enqueue_outcome(db, task, run, body, success)`：
  - `payload` 按 spec 2.3 映射表构造；`traceId="taskhub:<task_id>:<run_id>"`；
  - `task.project` 为空 → 直接写 `status="dead"`，不投递；
  - `IntegrityError` → 已入队直接返回；其它异常 → 日志后返回。
- 不改：Read Evidence / diff gate / completion gate 的执行顺序与响应体。
- 验证：三类结果（success / failure / timeout→aborted）各产生 1 条 outbox；重复提交不新增行。

### Step 6 异步投递 job（FR-7）

- 文件：`mio_taskhub/mio_runtime.py` 新增 `MioOutcomeJob(Scheduler)`；`background.py` 注册 `mio-outcome`。
- 逻辑：60s 一轮，取 `status in (pending, failed) and next_at <= now` 至多 20 条；退避 `min(30s*2^attempts, 1800s)`；`attempts >= 8` → `dead`；`MIO_OUTCOME_DISABLED=1` no-op。
- 验证：假 CLI 连续失败 3 次后状态为 failed 且 `next_at` 递增；恢复后转 sent；到上限转 dead。

### Step 7 查询端点（FR-8）

- 文件：`mio_taskhub/api/mio_runtime.py`。
- 新增 4 个端点：`GET /experience/pending`、`POST /experience/{id}/confirm`、`GET /route-decisions/{run_id}`、`GET /outbox`。
- confirm 规则：
  - `outcomeImproved=false` → 409 `experience.not_confirmable`，**不调用 runtime**；
  - runtime 返回 `alreadyConfirmed` → `state=already`；
  - runtime 返回 `rejected` → 422 `experience.not_auto_claim`；
  - runtime 不可用 → 503 `runtime.unavailable`。
- 验证：四类分支各有单测；重复 confirm 幂等。

### Step 8 前端（FR-9）

- 文件：`web/src/api.js` 增加 4 个方法；`web/src/components/MioRuntimeView.jsx` 增加「经验待确认」段；`web/src/index.css` 增加样式。
- 交互：只对 `confirmable=true` 的条目显示按钮；失败来源显示「不可确认（outcome 未改善）」且禁用；runtime 不可用时整段显示降级文案。
- 验证：`npm.cmd run build` 通过；无 Node/3456 等外部依赖引用。

### Step 9 契约冒烟（FR-10）

- 文件：`mio_taskhub/mio_runtime.py` 的 ContractJob 探针表。
- 增加：`task route`（`count/routes/agentHealth`）、`task record-outcome`（preview 语义）、`experience list`（`count/total/records`）、`--persist-query` 能力探测。
- 验证：删掉 `--persist-query` 后 ContractJob 报告该项失败（模拟）。

### Step 10 测试、文档回填与交付（FR-11）

- 新增 `tests/test_mio_route_outcome.py`、`tests/test_mio_outbox.py`、`tests/test_mio_experience_api.py`。
- 文档：本任务 `test` 文档按 AC 逐条建用例矩阵（TC-n ↔ AC-n ↔ FR-n）；`changelog` 文档记录本次变更（implementing 阶段的产出物要求）。
- 全量回归：`python -m pytest -q`；前端 build。
- 交付：按 Step 分提交，commit message 引用 `FR-n`；分支 `task-2f0be5e7`。

---

## 验收映射

| AC | 覆盖步骤 | 验证方式 |
|---|---|---|
| AC-1 | Step 2 / 4 / 9 | ContractJob schema 断言 + claim 响应快照 |
| AC-2 | Step 2 / 4 | 故障注入（CLI 缺失/超时/坏 JSON） |
| AC-3 | Step 4 | 冲突场景：route 命中不改变候选顺序 |
| AC-4 | Step 3 / 5 | 唯一键 + 进程重启后重放 |
| AC-5 | Step 5 / 6 | 两次 retry 各自一条 outbox，payload 的 run 不同 |
| AC-6 | Step 4 / 5 / 7 | `traceId` → `RouteDecision` 反查 |
| AC-7a | Step 2 / 4 | 已有 verified 记录进入 route（对照测试） |
| AC-7b | Step 5 / 6 / 7 | outcome → pending → confirm → 再次 route 命中 |
| AC-7c | — | 显式不做（排序变化不在验收范围） |
| AC-8 | Step 5 / 6 | 写回失败后本地可查、runtime 恢复后补发成功 |
| AC-9 | Step 4 / 5 | project 为空 → 无子进程、无查询记录、outbox=dead |

---

## 回滚方案

| 层 | 回滚动作 | 影响 |
|---|---|---|
| 上游补丁 | 恢复 `mio.js.bak-20261027` | 回到只读 route，闭环退化为单向（不影响 taskhub 功能） |
| 后端 | `MIO_OUTCOME_DISABLED=1` 停投递；feature 开关关闭 claim 注入 | 写回与路由建议同时停止，原有行为完整 |
| 数据 | 两张新表可保留不用（只增表），必要时删除 | 无存量数据丢失 |
| 前端 | 隐藏「经验待确认」段 | 无功能缺失（确认本来就需要人） |

所有回滚均不触碰既有表结构，不做数据迁移，因此可随时执行。

---

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| claim 增加 1.5s 子进程 | 领取变慢 | 预算 1.5s 硬超时；超时即降级；后续可改异步预热 |
| npm 升级覆盖上游补丁 | 闭环静默失效 | Step 9 能力探测告警 + ContractJob 周期检查 |
| 确认了但 route 仍不命中 | 用户困惑「确认无效」 | confirm 响应区分确认失败 / 查询不匹配 / 过滤未通过；文档写明 verified 三条件 |
| 失败来源误确认 | 把失败当成功经验 | 接口层 409 拦截（`outcomeImproved=false`），UI 禁用按钮 |
| `task.project` 为空的历史任务 | 查询污染 | AC-9：不调用、不写查询、outbox 置 dead |
| runtime 无幂等 | 重复 trace | 唯一键 + 只在 taskhub 侧投递，job 串行 |

---

## 交付清单

| 交付物 | 位置 |
|---|---|
| 上游补丁 | `bin/mio.js`（+ 备份文件） |
| 适配层 | `mio_taskhub/mio_runtime.py` |
| 数据模型 | `mio_taskhub/models.py` |
| 读路径 | `mio_taskhub/api/tasks.py` |
| 写路径 | `mio_taskhub/api/runs.py` |
| 后台 job | `mio_taskhub/background.py` |
| 查询端点 | `mio_taskhub/api/mio_runtime.py` |
| 前端 | `web/src/api.js`、`web/src/components/MioRuntimeView.jsx`、`web/src/index.css` |
| 测试 | `tests/test_mio_route_outcome.py`、`tests/test_mio_outbox.py`、`tests/test_mio_experience_api.py` |
| 文档 | 本任务 test / changelog 文档 |
