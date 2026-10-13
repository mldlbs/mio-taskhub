# 02 · 项目现状与证据审计报告（mio-taskhub）

| 项 | 值 |
|---|---|
| 版本 | v0.1（首次成文） |
| 日期 | 2026-10-10 |
| 状态 | 事实已复核；结论待评审 |
| 作者 | opencode（只读审计，未改任何代码） |
| 依据 | 本仓库源码 + 本地/远端运行数据 + 本轮实跑命令输出 |
| 上游 | `docs/roadmap/01-positioning.md`（定位与口径纠正）、`docs/quality-audit.md`（质量门控探针） |
| 约束 | 全文判断按 `01` §0 的三档标注；每条关键结论附可复现命令 |

---

## 0. 阅读约定（证据分档）

沿用 `01-positioning.md` §0：

| 档位 | 含义 | 本报告中的写法 |
|---|---|---|
| **事实** | 代码、数据库、命令输出或公开来源**直接**证明 | 给出路径 / 行号 / 命令 / 原始输出片段 |
| **推断** | 由实现或数据推断，*无直接证据* | 「（推断）」+ 推断依据 |
| **待验证** | 目前缺证据，是主要假设 | 「（待验证）」+ 验证方法 |

**本报告只做四件事**：分级现状、核对数据来源、给出失效模式与证据缺口、给出复现命令。不改代码、不修问题、不给实现方案（方案属 roadmap 后续文档）。

---

## 1. 审计方法与复现口径

### 1.1 方法

- **只读**：源码、文档、CI、生产 DB、日志均只读；本轮唯一写操作是新建本文件。
- **实跑**：测试套件、e2e、OpenAPI 内省、探针脚本均在本机实跑，不引用二手数字。
- **口径统一**：所有测试结论标注命令与 marker；`pyproject.toml` 的 `addopts = "-m 'not e2e'"` 是默认口径差异的根源。

### 1.2 环境（事实）

| 项 | 值 | 取证命令 |
|---|---|---|
| Python | 3.12.14（`.venv`） | `.\.venv\Scripts\python.exe -V` |
| pytest | 9.1.1 | `.\.venv\Scripts\python.exe -m pytest --version` |
| fastapi / sqlmodel / httpx | 0.141.1 / 0.0.42 / 0.28.1 | `pip list` |
| pytest-cov | **未安装** | `pip list | findstr cov` |
| 服务 | `http://127.0.0.1:48620/api/v1/status` → **200**；python 进程 8 个 | `Invoke-WebRequest .../api/v1/status` |
| 生产 DB | `C:\Users\admin\.mio_taskhub\taskhub.db`，5.39 MB | 见 §6 |
| 运行日志 | `C:\Users\admin\.mio_taskhub\console.log`，36 MB | 见 §6.3 |

### 1.3 关键口径（务必先读，否则结论对不上）

1. **默认测试不含 e2e**：`addopts = "-m 'not e2e'"`，所以 `pytest -q` 会 `deselected 4`。
2. **测试 DB 必须隔离**：直接跑会与并发 pytest / 正在运行的服务争用同一临时 DB，报
   `PermissionError: [WinError 32] ... mimo_taskhub_test.db`。本轮统一加：
   ```powershell
   $env:MIO_TEST_DB_PATH = "C:\Users\admin\AppData\Local\Temp\opencode\audit_x.db"
   ```
3. **生产数据只读**：用 `sqlite3.connect("file:...db?mode=ro", uri=True)`，不连生产写路径。
4. **README 数字不作基线**：`01-positioning.md` §1.3 已判定 README 口径漂移（C1/C2/C3），本报告一律以**代码派生指标**为准（见 §7）。

---

## 2. 仓库与工作区基线（事实）

| 项 | 值 | 取证命令 |
|---|---|---|
| 仓库形态 | **嵌套独立 git 仓库**（父仓 `E:\work\code\agent-dev` 不跟踪 `mio-taskhub/`） | `git rev-parse --show-toplevel` |
| 提交总数 | **12** | `git rev-list --count HEAD` |
| 最早提交 | `f9ba0ad chore: squash 452 commits into single root snapshot` | `git log --oneline` |
| 当前分支 / HEAD | `task-516bd0c7` / `1e7d00e feat(2026-10-08): 价值发现实验入口…` | `git status -sb` |
| 主干 | `main` = `9fdf371`（v0.5.3）；HEAD 领先 main **10 个提交** | `git rev-parse main` |
| 版本号 | **0.5.5** | `mio_taskhub/version.py` → `__version__ = "0.5.5"` |
| 工作区 | **24 项未提交**：17 个修改 + 7 个未跟踪 | `git status --porcelain` |
| 改动规模 | 17 文件 **+521 / −46** | `git diff --stat` |
| 未跟踪新文件 | `api/scheduler_control.py`、`api/task_lessons.py`、`scripts/audit_quality_gaps.py`、`tests/test_review_gates.py`、`tests/test_scheduler_control.py`、`tests/test_task_lessons.py`、`LICENSE` | 同上 |

**关键事实（推断风险高，故单列）**：

- **历史证据已丢失**：根提交是 squash 快照，12 个提交之前的 452 条历史不可审计（事实：`git log --oneline` 只有 12 条）。
- **`.gitignore` 第 31 行是 `docs/`** → 文档**默认不入库**：
  - 已跟踪 docs：**98**（`git ls-files docs | measure`）
  - 未跟踪且被忽略的 docs：**36**（`git ls-files --others --ignored --exclude-standard docs/`），含 `docs/quality-audit.md`、`docs/quality-plan-review-20261010.md`、`docs/roadmap/01-positioning.md`、`docs/taskhub/grab-mode/*`、`docs/taskhub/system-assessment-20261002-round6.md` 等。
  - **影响（事实）**：本报告（`docs/roadmap/02-*.md`）写完即处于"被忽略"状态，不产生版本记录；依赖这批文档的 `01-positioning`/`quality-audit` 结论也**没有可追溯的 commit 证据**。
  - **复现**：`git check-ignore -v docs/roadmap/02-current-state-audit.md`

---

## 3. 现状分级总表（已实现 / 已测试 / 真实运行 / 未验证）

**分档定义**

| 档 | 含义 | 判定依据 |
|---|---|---|
| **A 已实现** | 代码存在、可导入、接口可调用 | 源码路径 + 内省/请求成功 |
| **B 已测试** | 有自动化测试覆盖且**本轮实跑通过** | 本轮 `pytest` 输出 |
| **C 真实运行** | 在生产/真实进程中有可观测痕迹 | 生产 DB 行、console.log、在线服务 |
| **D 未验证** | 实现存在，但**无测试或无运行证据** | 既不在本轮测试通过集，也无运行痕迹 |

| 能力域 | 档 | 证据（简） | 详细 |
|---|---|---|---|
| HTTP API（REST） | **A+B+C** | OpenAPI **151 paths / 170 operations**；e2e 真 HTTP 通过；生产 `event` 2988 条 | §4.2, §6.2 |
| MCP 工具 | **A+B** | `mcp_server.py` `@_tool` **47**；`tests/test_mcp_server.py` 通过 | §7 |
| 任务/Run 状态机与迁移 | **A+B+C** | `workflow/state_machine.py` + 生产 383 task / 284 run | §6.1 |
| Read Evidence 门控（文档 consult） | **A+B+C** | 生产 `readevidence` **96** 行；本轮探针**可稳定触发 422** | §4.4 |
| 文档质量分 / 生命周期门控 | **A+B+C** | `doc_quality.py` 分数公式 + 阈值 80；`scripts/audit_quality_gaps.py` 实跑复现"缺3/通11/绕4"；生产 **103 次状态流转 / approved 56 次 / force 0 次**；**遗留 1 处 45 分越界待判**（§8.4） | §8.1, §8.4 |
| 棘轮基线（质量只升不降） | **A+B+C** | `ratchet.py` 实现 + 生产 `ratchetbaseline` **48** 行 | §8.2 |
| Read Evidence 服务端门控 | **A+B+C** | `api/runs.py` + 96 行生产证据 | §4.4 |
| 定时任务（scheduledjob / cron） | **A+C** | 3 个 job 生产在库；`scheduledjobexecution` ok 109 / error 5 / skipped 4 | §6.1 |
| 后台守护线程（lifespan） | **A+C（负面）** | 生产 console.log 有各 worker 启停痕迹；同时是**测试非封闭**根因 | §4.4, §6.3 |
| 可观测（日志 / 指标 / 告警 / SLO 快照） | **A+C** | `slosnapshot` 2016 行、`alertaudit` 59 行、`HighHttpErrorRate` 告警 331 次 | §6.3 |
| 可观测日志 **结构化字段完整性** | **D（缺陷）** | `middleware.py:77` 传入的 `path`/`duration_ms` 被 JSON formatter 丢弃 | §9-F3 |
| Web UI（React） | **A / D** | `web/src` 有改动（`api.js`、`MaterialPanelView.jsx` 在本次未提交改动中）；**全仓库无任何前端测试文件**（`web/` 下除 `node_modules` 外无 `*.test.*`/`*.spec.*`） | §10-G2 |
| 自更新 / supervisor-worker 双进程 | **A+（提交声明）** | `b3d54fc`/`4bdd0ca` 提交信息带 FR 编号；**本轮未验证** | §10-G1 |
| 值发现 / 创意链（idea→任务） | **A+B+C** | 单测 9 个 `tests/test_idea*.py` + `test_value_discovery.py`；生产 `idea` 52 / `insight` 122；**无 e2e** | §6.1, §10-G2 |
| 端到端（真服务） | **B** | `pytest -m e2e` → **4 passed, 12.53s** | §4.2 |
| CI 自动化验证 | **D（失效）** | CI workflow **53 次全失败**，测试从未在 CI 执行 | §5 |
| 覆盖率 | **D（缺失）** | 无 pytest-cov，**仓库无任何覆盖率数据** | §4.5 |

> **一句话现状（事实汇总）**：核心执行链（API + 状态机 + 门控 + 调度 + 可观测）**本地实现齐、本地测试齐、生产有真实使用痕迹**；但**质量保证链的两端是断的**——上游 CI 从未真正跑过测试（§5），下游覆盖率从未被测量（§4.5），且支撑门控结论的 36 份关键文档不入库（§2）。

---

## 4. 测试证据（本轮实跑）

### 4.1 全量结果（事实）

```powershell
$env:MIO_TEST_DB_PATH = "C:\Users\admin\AppData\Local\Temp\opencode\audit_full_suite.db"
.\.venv\Scripts\python.exe -m pytest -q
```

```
4 failed, 1260 passed, 1 skipped, 4 deselected, 2 warnings in 393.28s (0:06:33)
```

| 指标 | 值 |
|---|---|
| 收集总数 | **1269**（`pytest --collect-only -q` → `1265/1269 tests collected (4 deselected) in 2.06s`） |
| 通过 | 1260 |
| 失败 | **4**（全部来自 `tests/test_spinning_guard.py`） |
| 跳过 | 1 |
| 默认排除（e2e） | 4 |
| 耗时 | 393s（~6.6 min） |

**质量基线（事实）**：本地非 e2e 用例通过率 **1260/1264 = 98.1%**（分母不含 4 个 e2e 与 1 个 skip）。**注意这是"本地实跑"，不是"CI 通过"，两者不可互换引用。**

### 4.2 e2e（真 uvicorn + 真 DB + 真 HTTP，事实）

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m e2e
```
```
4 passed, 1265 deselected, 1 warning in 12.53s
```

与 `docs/taskhub/reliability-verification-report.md` 的声明一致（该报告声明 e2e 4/4、20-agent claim 成功率 100%、claim P50/P95 = 30/32ms）。**分档说明**：e2e 4 用例属 **B（本轮复测通过）**；报告内的 20-agent 压测与 P50/P95 属**报告内数据，本轮未复跑** → 归 **D（待验证）**，复现方法：按该报告 §2 用例重跑并留存输出。

### 4.3 失效用例：`tests/test_spinning_guard.py`（事实，HEAD 即红）

4 个用例全部失败于同一根因：

```
tests\test_spinning_guard.py:167: AttributeError:
'CronEngine' object has no attribute '_should_skip_webhook_generation'.
Did you mean: '_should_skip_task_creation'?
```

- 失败用例：`test_webhook_guard_skips_when_unconsumed_ideas_pile_up`、`test_webhook_guard_allows_below_threshold`、`test_webhook_guard_ignores_non_generation_webhook`、`test_webhook_guard_counts_only_unconsumed_new`。
- **分档：D（测试资产失效）**——被测方法已不存在（guard 逻辑迁走/改名），用例未随实现更新。
- **推断（推断）**：webhook 价值闸门已由 `CronEngine` 内联判断迁出（实现侧现存在 `_should_skip_task_creation`），旧用例指向的历史方法名成为死引用。
- **验证方法**：`git log -S "_should_skip_webhook_generation" -- mio_taskhub` （因历史被 squash，只能看到现有 12 个提交内的记录）。
- **复现**：`.\.venv\Scripts\python.exe -m pytest -q tests/test_spinning_guard.py`

### 4.4 间歇性失败：claim 竞态导致 Read Evidence 422（事实 + 探针稳定复现）

**现象**：`tests/test_run_ownership_security.py::test_tc_s3_owner_can_heartbeat_and_submit` 在全量跑中曾报 **422**；单跑该文件 **8 passed in 1.36s**；本轮全量跑也未复现 → **flaky（间歇性）**。

**根因探针（本轮 2026-10-10 14:27 实跑，稳定复现 422）**：

```powershell
$env:MIO_TEST_DB_PATH = "...\opencode\audit_race.db"
.\.venv\Scripts\python.exe "C:\Users\admin\AppData\Local\Temp\opencode\audit_claim_race.py"
```

关键日志（原始输出）：

```
insight remediator created follow-up task 003bcb4f for metric=taskhub_task_success_rate
CREATE 200  {... "state": "queued" ...}
CLAIMED: 003bcb4f | required_reads: ['spec', 'requirement']
hb 200
RESULT 422 {"detail": {"message": "Read Evidence 门控...", "missing": ["spec", "requirement"]}}
```

**因果链（事实，逐环有码位）**：

1. `TestClient(app)` 触发 lifespan，启动**一批守护线程**（`mio_taskhub/main.py:50-161`：`backup` / `slo-snapshot` / `dep-persist` / `insights-eval` / `remediation-eval`；`background.start_background_jobs()`（`background.py:593-614`）再注册 `heartbeat` / `scheduler` / `idea-review` / `mio-digest` / `mio-contract`，另有 `git_sync`、`cron_engine` 等），**≥10 个注册线程**。
2. `insights-eval` / remediation 线程在**测试 DB**内创建带 `doc_paths` 的跟进任务（`mio_taskhub/observability/insight_remediator.py` `_create_followup`）。
3. 测试调用**不带 `task_id` 的 claim**，按优先级**抢到了这个注入任务**（`mio_taskhub/api/claim.py`）。
4. 该任务 `required_reads = ['spec','requirement']`，测试从未读过 → `submit_result` 被服务端 Read Evidence 门控拦下 **422**（`mio_taskhub/api/runs.py` submit 分支；生产同源逻辑见 `mio_taskhub/read_evidence.py`）。

**分档**：**D（测试非封闭 / 测试不可靠）**。这是"实现正确但测试体系有洞"的典型样本——门控本身工作正常，坏在**测试进程与后台线程共享同一 DB**。

**复现/规避**：
- 复现：跑探针（上方命令）。
- 单测恒绿：`pytest -q tests/test_run_ownership_security.py`（本轮 8 passed）。
- 验证是否为同一根因：看失败输出里 `CLAIMED:` 的 task_id 是否**不是**测试自己创建的那个。

### 4.5 覆盖率：**缺失（事实）**

- `pytest-cov` 未安装；`pyproject.toml` 无 `--cov` 配置；仓库内无 `coverage.xml`/`.coverage`/HTML 报告。
- **分档：D**。**当前无法回答"核心链路覆盖多少行"**，任何基于覆盖率的承诺都属待验证。
- 验证方法：`pip install pytest-cov && .\.venv\Scripts\python.exe -m pytest -q --cov=mio_taskhub --cov-report=term-missing`

---

## 5. CI 证据：测试从未在 CI 执行（事实，最高优先级缺口）

### 5.1 结果统计

```powershell
gh run list --limit 100 --json conclusion,workflowName
```

| workflow | 次数 | conclusion |
|---|---|---|
| **CI**（`.github/workflows/test.yml`） | **53** | **failure × 53（100%）** |
| Dependency Graph | 1 | success × 1（`event=dynamic`，与测试无关） |

- 时间跨度：2026-08-29 → 2026-10-05（最近一次 CI 是 `2026-10-05T06:51`，run `37274529093`）。
- 当前分支 `task-516bd0c7` **不触发 CI**（workflow 仅监听 `main`/`master`/`pull_request`）。

### 5.2 根因（事实，原始日志）

`.github/workflows/test.yml` 安装步：

```yaml
run: |
  python -m pip install --upgrade pip
  pip install -e ".[dev]" 2>/dev/null || pip install -e .
  pip install pytest
```

`gh run view 37274529093 --log` 关键行：

```
pip install -e ".[dev]" 2>/dev/null || pip install -e .
  | Could not find a part of the path 'D:\dev\null'.
##[error]Process completed with exit code 1.
```

- Windows runner 的 `run:` 默认走 PowerShell；`2>/dev/null` 被解析为**重定向到文件 `/dev/null`**（而非丢弃流），路径解析失败 → 该步 exit 1 → **workflow 在"Run tests"之前就终止**。
- **结论（事实）**：`.github/workflows/test.yml` 的 **Install dependencies 步 53 次全红**，**"Run tests" 步一次都没执行过**。
- **影响（事实）**：README §「CI」宣称的"GitHub Actions / windows-latest / py3.12 全绿"不可引用；**代码从未在干净环境被自动验证**。
- **修复成本（推断）**：删掉 `2>/dev/null`（或改 `shell: bash`）即可让安装步过；是否真能全绿，**待验证**——本地 4 个失效用例 + 6.6min 时长 + 可能的网络依赖都会成为下一道关。

---

## 6. 真实运行证据（生产 DB + 日志 + 在线服务）

> 取证方式：`sqlite3.connect("file:C:\Users\admin\.mio_taskhub\taskhub.db?mode=ro", uri=True)`；日志用 JSON 逐行解析。**全部只读。**

### 6.1 数据库事实（2026-10-10）

| 表 | 行数 | 说明 |
|---|---|---|
| `task` | **383** | `CANCELLED 186 / COMPLETED 126 / QUEUED 66 / FAILED 5` |
| `task.stage` | 383 | `CANCELLED 134 / DONE 80 / READY 58 / REVIEW 49 / IMPLEMENTING 32 / BRAINSTORMING 25 / PLANNING 4 / DESIGN 1` |
| `run` | **284** | 字段为 `state`（非 `status`），含 `last_heartbeat`、`exit_code` |
| `readevidence` | **96** | 门控真实被走过（`document_kind` 分布见下） |
| `ratchetbaseline` | **48** | 棘轮基线已落库 |
| `event` | 2988 | Top：`heartbeat` 712 / `task_claimed` 227 / `task_created` 212 / **`task_doc_written` 194** / `task_result` 152 / **`task_cancelled` 145** / `agent_registered` 132 / `agent_offline` 130 / `task_stage` 114 / `scheduled_job_executed` 112 / **`task_doc_status` 103** / `task_updated` 99 / `reaper_decision` 85 / `task_history_added` 70 / `task_moved` 65 |
| `taskevent` | 992 | 任务级事件 |
| `slosnapshot` | **2016** | SLO 快照在持续落库 |
| `alertaudit` | 59 | 告警审计在写 |
| `insight` | 122 | 洞察在积累 |
| `idea` / `discussion` | 52 / 60 | 想法与讨论在使用 |
| `agent` | 19 | 注册过 19 个 agent |
| `outboxevent` | **0** | outbox 有表、**从未有数据**（待验证：是"未启用"还是"被清理"） |
| `scheduledjob` | 3 | **`enabled=0` 全部停用**：`a9cbd255`(OK, 6 次)、`50646f04`(OK, 98 次)、`1271eac5`(**ERROR**, 14 次，`last_error: 502 creativity...返回空包`) |
| `scheduledjobexecution` | 118 | `ok 109 / error 5 / skipped 4` |

**readevidence 96 行按 kind（事实）**：`spec 42 / requirement 38 / api 14 / plan 2`（同口径查询在 §附录 A）。

**运行层结论（事实）**：
- 服务**长期在线、真实被 agent 使用**（284 个 run、96 条读证据、2988 事件）。
- **失败集中在 CANCELLED（186/383=48.6%）**；FAILED 仅 5。→ **事实**：`event` 中 `task_cancelled` **145** 条，与 186 个已取消任务**差 41**（事件缺失或早期无事件）；且抽查 3 条 `task_cancelled` 的 `payload` **全为 `null`** → **取消原因没有落在 event 里**，追因只能查 `historyevent`/`taskevent`（→ §10-G9）。
- **3 个定时任务全部 `enabled=0`** → 当前**没有任何活跃定时任务**；其中每日灵感生成 job 上次为 `ERROR`（502）。→ **D（调度能力存在但处于停摆）**。

### 6.2 服务在线事实

- `GET http://127.0.0.1:48620/api/v1/status` → **200**。
- 本机 python 进程 8 个（含 supervisor/worker 形态）。
- **推断**：当前由 supervisor-worker 双进程架构托管（`4bdd0ca`），具体进程清单与守护关系**待验证**（`.\.venv\Scripts\python.exe -m ...` 无统一 ps 清单；可用 `mio_list_processes` 或 `Get-CimInstance Win32_Process` 取父子 PID 复核）。

### 6.3 运行日志事实（`console.log`，36 MB）

**总量（事实）**：`ERROR 1758`、`WARNING 419`（按 `"level"` 字段计数）。

**按 logger+msg 分组 Top（含首末时间，事实）**：

| 次数 | logger | msg（截断） | 时间跨度 |
|---|---|---|---|
| 683 | `mio_taskhub.observability.remediation` | `Failed to evaluate stalled tasks`（`AttributeError: 'str' object has no attribute 'tzinfo'`） | 2026-09-15 → 09-17 |
| 595 | `mio_taskhub.middleware` | `request_exception: 'INBOX' is not among the defined enum values` | 2026-10-04 16:10 → 19:50 |
| 331 | `mio_taskhub.observability.alerts` | `Active alerts: ['HighHttpErrorRate']` | 2026-09-15 → 2026-10-07 |
| 400 | `opentelemetry...instrumentor` | `DependencyConflict: requested sqlalchemy<2.1.0 / httpx>=0.18.0` | 2026-09-11 → **2026-10-10（仍在发）** |
| 50 | `night_runner` | `spawn idle-worker-opencode precheck failed: 脚本不存在` | 2026-09-30 → **2026-10-10（仍在发）** |
| 27 | `mio_taskhub.middleware` | `request_exception: name 'TaskEvent' is not defined` | 2026-09-08 → 09-10 |
| 27 | `asyncio` | `Exception in callback _ProactorBaseWritePipeTransport...` | 2026-09-17 → **2026-10-09（仍在发）** |
| 8+5+4 | `mio_taskhub.background` | `状态机拒绝…未发生流转: (queued,ready) -> (queued,…)` | 2026-09-27 |
| 7 | `mio_taskhub.middleware` | `request_error`（5xx） | 2026-09-26 → **2026-10-08** |
| 4 | `mio_taskhub.observability.task_trace` | `Failed to get trace summaries: Session.exec() takes 2 positional...` | 2026-09-16 → 09-17 |

**分档结论**：
- **已修复且有运行证据（A+C）**：`tzinfo` 崩溃 683 次止于 2026-09-17，此后 23 天无复发。日志 `exception` 字段定位到历史缺陷 `mio_taskhub\observability\remediation.py:25`；现实现已把时间比较下沉 SQL（`observability/remediation.py:37-52`，docstring 自述"历史缺陷，导致本函数每 2 分钟报一次 AttributeError 且永远返回空"），日志点在 `:69`。→ 这是**"运行证据驱动修复"的正样本**，可作为后续问题的处理模板。
- **已修复（A+B）**：`TaskEvent is not defined` 止于 09-10。
- **仍在发生（C-负面，需进 backlog）**：
  - otel `DependencyConflict` **每天重启都发**（09-11 → 10-10）→ **D**：otel 自动插桩实际未生效。
  - `night_runner spawn idle-worker precheck failed`（脚本不存在）→ **D**：夜间 idle-worker 拉起长期失败。
  - asyncio Proactor write-pipe 异常 → **D**（Windows 事件循环已知噪音 + 潜在真实句柄泄漏）。
  - `HighHttpErrorRate` 告警 331 次 → **D**：告警在响但**无人处理**（未见处置记录）。
- **单次爆发型**：`INBOX` 枚举不匹配 595 次集中在 2026-10-04 三小时内（→ **推断**：某枚举值/数据迁移缺陷触发的一次性风暴；**待验证**：查该时段前后的代码提交与 `idea`/`inbox` 数据）。

---

## 7. 口径核对（README / 报告 vs 实测）

| # | 来源口径 | 实测值（命令） | 判定 |
|---|---|---|---|
| C1 | README：版本 **v0.3.0** | `version.py` = **0.5.5** | **README 落后 2 个 minor** |
| C2 | README：**119** REST 端点 | OpenAPI **151 paths / 170 operations**（`GET /openapi.json` 内省）；`api/*.py` 中 `@router.` 装饰器 **161** | **README 偏低** |
| C3 | README：**40** MCP 工具 | `mcp_server.py` `@_tool` 装饰器 **47** | **README 偏低** |
| C4 | README：**641** 测试用例 / 55 文件 | 收集 **1269**（`pytest --collect-only -q`） | **README 严重偏低**（约 1/2） |
| C5 | README：**13,749 行** | `mio_taskhub` 108 个 `.py`（`01` E4 实测约 19,475 行） | **README 偏低** |
| C6 | README：CI 全绿 | CI workflow **53/53 failure** | **README 与事实相反** |
| C7 | README：无 LICENSE 文件 | 仓库根 `LICENSE` 为**未跟踪新文件**（`git status --porcelain` 显示 `?? LICENSE`） | **法律状态仍未入库**（`01` C3 的续） |
| C8 | `quality-audit.md`：缺3 / 通11 / 绕4 | `scripts/audit_quality_gaps.py` 本轮实跑**复现一致** | **报告可信**（可复现） |
| C9 | `reliability-verification-report.md`：e2e 4/4 | 本轮 `pytest -m e2e` → **4 passed** | **复现一致** |
| C10 | `SLO-SLI.md`：可用性 99.9% / 99.0% / 99.5% | 无达成率统计代码输出；`slosnapshot` 2016 行**是快照，非达成率** | **目标值，非实测**（分档 D） |

**结论（事实）**：**README 的数字全部不可作为基线**（与 `01-positioning` C1–C3 结论一致，本报告补充 C4–C7）；**质量审计与可靠性报告的数字可复现**（C8/C9）；**SLO 是目标不是实测**（C10）。

---

## 8. 门控机制现状（代码事实）

### 8.1 文档质量分（`mio_taskhub/doc_quality.py`）

- 分数公式（L12 注释）：`score = 100 - 20*errors - 5*warns`，下限 0。
- **硬阈值（L87-89）**：`requirement / spec / api = 80`（推进到 `review`/`approved`/`done` 需分数达标）。
- 扣分项（L251-313）：缺必需章节（`error`）、章节内残留模板注释（`error`）、表格数据行不足（`error`）、缺推荐章节（`warn`）、TODO 占位残留（`error`）、缺 FR↔测试块（`error`）。
- **缺陷样本（`quality-audit.md` P0-1/P0-2/P0-4，本轮 `audit_quality_gaps.py` 复现）**：3 处仍"缺"、11 处"通"、4 处"绕"（`force` 可绕过且**留痕**）。
- **分数落库位置（事实）**：分数不存独立表，而是随 `task_doc_status` 事件写进 `event.payload.quality_score`（`api/task_documents.py` `set_doc_status` 分支）→ 生产 103 条中 **26 条无分**（早期未记分），分布 100×71 / 95×2 / 90×2 / 45×2（明细见 §8.4）。

### 8.2 棘轮基线（`mio_taskhub/ratchet.py`）

- 开关：`MIO_RATCHET_DISABLED` 非空则关闭（L30-32）；默认**开启**。
- 规则：`current >= baseline` 放行并**抬升基线**；`current < baseline` **阻断（422）**，除非 `force=true` 留痕（L13-14、L69-90）。
- 生产证据：`ratchetbaseline` **48 行**（基线确在记录）。

### 8.3 Read Evidence（服务端强制）

- `readevidence` **96 行**（`spec 42 / requirement 38 / api 14 / plan 2`）→ 读证留痕**在生产真实发生**。
- 422 拦截**可稳定复现**（§4.4 探针）→ 门控**有效**。

### 8.4 文档生命周期：生产侧真实走通情况（本轮新取证）

> 存储位置更正：文档状态**不是**独立表，而是 `task.doc_statuses` JSON 列（`models.py:157`，`kind -> {state, at, note}`），流转事件写入 `event` 表 `type='task_doc_status'`（`api/task_documents.py:571-654`）。→ 这纠正了"生产 DB 无生命周期痕迹"的初步判断。

| 事实 | 值 | 查询 |
|---|---|---|
| 有 `doc_statuses` 的任务 | **31 / 383** | `select id, doc_statuses from task` |
| `task_doc_status` 事件 | **103**（时间跨度 2026-09-20 03:36 → 2026-10-10 05:33） | `select payload from event where type='task_doc_status'` |
| 其中 → approved | requirement **17** / spec **16** / api **12** / plan **11** | 按 `kind,to` 聚合 |
| 其中 → review | spec **18** / api **14** | 同上 |
| `task_doc_written` / `task_doc_scaffolded` 事件 | **194 / 9** | `select type,count(*) from event group by 1` |
| **`forced` 字段** | **103/103 全为 `null`** → **生产从未用 force 绕过** | payload 解析 |
| `quality_score` 分布 | 100×71、95×2、90×2、**45×2**、缺失×26 | 同上 |
| **分数 <80 却进入 review/approved** | **2 条，同一任务**：`6a630afa` 的 `api` 文档，**45 分**，2026-09-23 02:57:25 → review、02:57:32 → approved（相隔 7 秒） | 同上 |

- **判断（事实）**：文档生命周期状态机**在生产侧确实走通**（103 次流转、approved 共 56 次），`force` 绕过**0 次**。
- **判断（推断 + 待验证）**：现行代码对这 2 条会**直接 422**——`api/task_documents.py:613-628` 在 `state ∈ GATED_TARGETS`（`doc_quality.py:74 = review/approved/done`）时算分，`score < MIN_SCORE['api']=80`（`doc_quality.py:86-90`）即抛 422，除非 `force`（而该 2 条 `forced=null`）。因此**当时运行的一定是未含此门控的构建**；与 `AGENTS.md` 所述「2026-09-23 收紧门控」**同日**（事件时刻 02:57），很可能是收紧生效前的边界样本。**但因提交历史被 squash（§2），无法确定门控代码的合入时刻** → 无法最终判定为"门控被绕过"还是"当时尚无门控"。**验证方法**：读任务 `6a630afa` 当日 `event` 序列与 `doc_statuses` 备注，或比对部署构建时间。
- **判断（事实，缺口）**：**26/103 次流转没有 `quality_score`**（早期版本未记分）→ 质量分**不是全量可回溯**；另 `historyevent` 中无 `force`/`bypass` 类型记录（留痕载体是 `event.payload.forced`，不是 `historyevent.type`）。

### 8.5 门控闭环的真实状态

| 门 | 实现 | 生产走过 | 结论 |
|---|---|---|---|
| 质量分 ≥80 才能 approved | ✅ | ✅ 103 次流转**均记 `quality_score`**（26 次早期无分），**仅 2 条 45 分越过（边界日，见 §8.4）** | **通，有 1 处边界待判** |
| 棘轮只升不降 | ✅ | ✅（48 行基线） | **通** |
| Read Evidence 必读 | ✅ | ✅（96 行 + 422 可复现） | **通** |
| `force` 绕过留痕 | ✅ | ✅（`event.payload.forced` 全 103 条为 `null` → **生产 0 次绕过**；留痕字段存在） | **通（且从未被用）** |
| pre-push FR 编号校验 | ✅（`scripts/install_hooks.py`） | **✅ 钩子已安装**：`.git/hooks/pre-push` 933 B，根路径已固化为 `E:/work/code/agent-dev/mio-taskhub` | **通，但 fail-open**（见下注） |

> **pre-push 门控的两条硬事实**（读 `.git/hooks/pre-push`）：
> 1. **fail-open**：找不到 `python` 或 `scripts/git-hooks/pre_push_runner.py` 时直接 `exit 0` **放行**（钩子原文：`echo "[doc-gate] 未找到...跳过文档门控（放行）"; exit 0`）→ 门控**不是强制的**，环境问题会静默降级为无门控。
> 2. **路径在安装时固化**：`MIO_ROOT="${MIO_TASKHUB_ROOT:-E:/work/code/agent-dev/mio-taskhub}"`（`install_hooks.py:57` 用 `PRE_PUSH_TEMPLATE.replace("@MIO_ROOT@", ...)` 生成）→ 换目录/换机器须重装（`python mio-taskhub/scripts/install_hooks.py`），可被 `MIO_TASKHUB_ROOT` 环境变量覆盖。

---

## 9. 失效模式与技术债清单（含复现命令）

| 编号 | 问题 | 档 | 证据（文件:行 / 命令） | 影响 | 复现 |
|---|---|---|---|---|---|
| **F1** | CI 53/53 全红，**测试从未在 CI 执行** | D | `.github/workflows/test.yml` 安装步 `2>/dev/null`；`gh run view 37274529093 --log` → `Could not find a part of the path 'D:\dev\null'` + `exit code 1` | 干净环境零验证 | `gh run list --limit 100 --json conclusion,workflowName` |
| **F2** | 测试非封闭：lifespan 后台线程写测试 DB → claim 抢任务 → **422 flaky** | D | 探针日志 §4.4；`main.py` lifespan、`insight_remediator._create_followup`、`api/claim.py`、`api/runs.py` submit 门控 | 测试结果不可信/不可复现 | `python audit_claim_race.py`（§4.4） |
| **F3** | 结构化日志丢字段：`path`/`duration_ms`/`status` 不入 JSON | D | `middleware.py:77` 传 `extra={...path, duration_ms}`；`observability/logging_config.py:62-64,105,134-136` 只取 `request_id/trace_id/span_id` | 7 条 `request_error`（5xx）**无法定位到路由** | 抓一条 `request_error` 日志看 JSON 键 |
| **F4** | `tests/test_spinning_guard.py` 4 用例死引用已删方法 | D | `AttributeError ... _should_skip_webhook_generation`（`test_spinning_guard.py:167`） | 4 个恒红用例 | `pytest -q tests/test_spinning_guard.py` |
| **F5** | 无覆盖率数据 | D | `pytest-cov` 未装、无 `--cov` 配置 | 无法量化测试盲区 | `pip show pytest-cov` |
| **F6** | 关键文档 36 份被 `.gitignore:31 docs/` 忽略、不入库 | 事实 | `git ls-files --others --ignored --exclude-standard docs/` → 36 | 结论无版本可追溯 | `git check-ignore -v docs/quality-audit.md` |
| **F7** | 3 个定时任务全部 `enabled=0`，其一 `ERROR` | C | `scheduledjob` 表：`last_status=ERROR, last_error=502 creativity 返回空包` | 调度能力停摆 | §6.1 只读 SQL |
| **F8** | otel `DependencyConflict` 每天复发（09-11→10-10，400 次） | C-负 | `console.log` otel instrumentor | **otel 插桩实际未生效** | `audit_log_stats.py` |
| **F9** | `night_runner spawn idle-worker precheck failed: 脚本不存在`（50 次，仍在发） | C-负 | `console.log` `night_runner` | 夜间 worker 拉起失败 | 同上 |
| **F10** | `HighHttpErrorRate` 告警 331 次，**无处置闭环** | C-负 | `console.log` `observability.alerts` + `alertaudit` 59 行 | 告警疲劳 | 同上 |
| **F11** | 全局限流 **key 只用 client IP**：`key = f"{client_ip}"`，默认 120 req/min → 同机全部 agent 共用一个桶 | 事实 | `mio_taskhub/middleware.py:87,106,112,118-124`（`_DEFAULT_RATE_LIMIT=120`；429 返回 `rate_limited`）；可用 `MIO_TASKHUB_RATE_LIMIT` 调 | 并发 agent 场景互相挤兑 → 误 429 | 并发压同一 IP 触发 429 |
| **F12** | 异常吞掉：**94 处** `except → pass/continue/return None`（44 多行 + 50 单行），`bare except` **0**；Top：`observability/metrics.py` 20、`main.py` 8 | 事实 | 见 §附录 A 统计脚本 | 失败被静默（如指标丢失） | 附录 A 脚本 |
| **F13** | pre-push 门控 **fail-open**：缺 python/runner 即静默放行；根路径在安装时固化 | 事实 | `.git/hooks/pre-push`（`exit 0` 放行分支 + `MIO_ROOT="${MIO_TASKHUB_ROOT:-E:/work/code/agent-dev/mio-taskhub}"`）；`scripts/install_hooks.py:57` | 环境异常时门控**静默失效**，无告警 | 删掉 `.venv` 的 python 别名或改 runner 路径后 `git push` |
| **F14** | `outboxevent` 表 0 行 | D | §6.1 | outbox 机制可能从未启用 | 查启用开关 |
| **F15** | Windows asyncio Proactor write-pipe 异常 27 次（仍在发） | C-负 | `console.log` `asyncio` | 潜在句柄/关闭竞态 | 同 F8 |
| **F16** | 质量分**不可全量回溯**：103 次流转中 **26 次无 `quality_score`**；另 2 次 45 分越界（边界日） | 事实 | `event.payload` 解析（§8.4 / 附录 A.11） | 历史门控强度无法审计 | 附录 A.11 |
| **F17** | `task_cancelled` 事件 `payload` 全为 `null`，**取消原因不落库**；186 任务 vs 145 事件差 41 | 事实 | §6.1 / 附录 A.12 | 取消型失败无法归因 | 附录 A.12 |

**处理优先级（推断，供 roadmap 用）**：P0 = F1、F2、F5（验证链断）；P1 = F4、F6、F7（资产失效/不可追溯）；P2 = F3、F8、F9、F10、F16、F17（可观测与归因闭环）；P3 = F11–F15（技术债）。

---

## 10. 证据缺口清单（缺什么 → 怎么补）

| # | 缺口 | 当前状态 | 补证方法（最小成本） |
|---|---|---|---|
| G1 | supervisor/worker、自更新、打包发布链**无本轮验证** | 仅提交声明 | 跑一次 `build.ps1` + 观察 supervisor 起停 + `Get-CimInstance Win32_Process` 记父子 PID |
| G2 | Web UI **零自动化测试** | `web/src` 有改动、无测试 | 引入 Playwright 冒烟（或先手测留屏录证据） |
| G3 | **覆盖率**全无 | §4.5 | `pip install pytest-cov` + `--cov=mio_taskhub --cov-report=term-missing` |
| G4 | **CI 绿**从未达成 | §5 | 修安装步后跑一次，记录 `Run tests` 是否通过、耗时、flaky 项 |
| G5 | ~~文档生命周期在生产侧是否走过~~ → **已核（事实）**：103 次流转 / approved 56 / `task_doc_written` 194 | ✅ 已核（§8.4） | `select payload from event where type='task_doc_status'` |
| G6 | ~~`force` 绕过是否留痕~~ → **已核（事实）**：`event.payload.forced` **103/103 为 null**，生产 0 次绕过；留痕字段存在 | ✅ 已核（§8.4） | 同上，解析 `forced` 字段 |
| G6b | **45 分 `api` 为何能 approved（`6a630afa`，2026-09-23 02:57）** | 推断为门控收紧前的边界样本，**不可证（历史已 squash）** | 读该任务 `doc_statuses` + 同日 `event` 序列 |
| G7 | ~~pre-push 钩子是否已安装~~ → **已核（事实）**：`.git/hooks/pre-push` 存在（933 B），但 **fail-open**（见 §8.5 注） | ✅ 已核 | `Get-Content .git\hooks\pre-push` |
| G8 | reliability 报告的 20-agent 压测 / P50 P95 | 报告内数据 | 按 `docs/taskhub/reliability-verification-report.md` §2 重跑并留存 |
| G9 | CANCELLED 186 条的**取消原因分布** | **部分已核（事实）**：`event.task_cancelled` 145 条且 `payload` 全为 `null` → **原因不在 event**；186 vs 145 差 41 待解释 | §6.1 / 附录 A.12 → 读 `historyevent`/`taskevent` 载荷 |
| G10 | `INBOX` 枚举风暴（10-04，595 次）成因 | §6.3 推断 | 对照 10-04 前后提交与 inbox 数据 |
| G11 | 12 条提交之外的 452 条历史 | **不可补**（已 squash） | 记为永久证据损失 |

---

## 11. 结论

1. **实现侧（A）**：核心能力齐备且可内省——OpenAPI 151/170、MCP 47 工具、39 个模型、状态机 + 三重门控（质量分/棘轮/读证）均有实现。
1b. **门控不是纸面功夫（C，正面）**：文档生命周期在生产走过 **103 次流转（approved 56）**、读证 **96 条**、棘轮基线 **48 条**、**`force` 绕过 0 次**；唯一越界是 2026-09-23 的 45 分 `api` 转 approved（边界日，待判）。→ 门控体系的**实际执行强度**是本项目最扎实的部分。
2. **测试侧（B）**：本地 **1260 passed / 4 failed / 393s**，e2e **4 passed**；通过率 98.1%，但**存在 1 组死引用用例（F4）+ 1 个可复现根因的 flaky（F2）**。
3. **运行侧（C）**：生产**真实在用**（383 task / 284 run / 96 读证 / 2016 SLO 快照 / 服务 200），且**运行日志能驱动修复**（683 次 tzinfo 崩溃已止血）；但同时暴露**停摆的调度（F7）、失效的 otel（F8）、无闭环的告警（F10）**。
4. **验证链两端断裂（D，最关键）**：**上游 CI 53 次全红、测试一次都没跑过（F1）**，**下游覆盖率从未测量（F5）**，中间**36 份支撑结论的文档不入库（F6）**——三者叠加，导致"绿色"主要来自本地单机实跑，缺乏可复现的第三方证据。
5. **口径必须以实测为准**：README 的 6 项数字全部偏低或相反（§7 C1–C7）；质量审计与可靠性报告的数字**可复现**（C8/C9）；SLO 为**目标值非实测**（C10）。

---

## 附录 A · 复现命令全集

> 环境：`E:\work\code\agent-dev\mio-taskhub`，PowerShell，`.venv`。

### A.1 仓库基线

```powershell
git rev-list --count HEAD                      # 12
git log --oneline -12
git status -sb ; git status --porcelain | measure   # 24
git diff --stat                                # 17 files, +521 -46
git rev-parse --show-toplevel                  # 嵌套仓库根
```

### A.2 文档入库缺口

```powershell
git ls-files docs | measure                                     # 98 已跟踪
git ls-files --others --ignored --exclude-standard docs/        # 36 被忽略
git check-ignore -v docs/roadmap/02-current-state-audit.md      # .gitignore:31:docs/
Select-String -Path .gitignore -Pattern docs                    # 31: docs/
```

### A.3 测试（务必先隔离 DB）

```powershell
$env:MIO_TEST_DB_PATH = "C:\Users\admin\AppData\Local\Temp\opencode\audit_full_suite.db"
.\.venv\Scripts\python.exe -m pytest -q                    # 4 failed, 1260 passed, 1 skipped, 4 deselected, 393s
.\.venv\Scripts\python.exe -m pytest --collect-only -q     # 1265/1269 collected (4 deselected)
.\.venv\Scripts\python.exe -m pytest -q -m e2e             # 4 passed, 1265 deselected, 12.53s
.\.venv\Scripts\python.exe -m pytest -q tests/test_spinning_guard.py          # 4 failed (F4)
.\.venv\Scripts\python.exe -m pytest -q tests/test_run_ownership_security.py  # 8 passed (F2 单跑恒绿)
.\.venv\Scripts\python.exe "C:\Users\admin\AppData\Local\Temp\opencode\audit_claim_race.py"   # 422 复现 (F2)
```

### A.4 覆盖率（当前缺失，装完即可得）

```powershell
pip install pytest-cov
.\.venv\Scripts\python.exe -m pytest -q --cov=mio_taskhub --cov-report=term-missing
```

### A.5 代码派生指标（口径基线）

```powershell
# OpenAPI
.\.venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from mio_taskhub.main import app; s=TestClient(app).get('/openapi.json').json(); print(len(s['paths']), sum(1 for p,m in s['paths'].items() for k in m if k in ('get','post','put','delete','patch')))"
# MCP 工具
Select-String -Path mio_taskhub\mcp_server.py -Pattern '^@_tool' | measure   # 47
# 路由装饰器 / 模型类
Select-String -Path mio_taskhub\api\*.py -Pattern '@router\.(get|post|put|delete|patch)' | measure  # 161
Select-String -Path mio_taskhub\models.py -Pattern '^class ' | measure       # 39
```

### A.6 异常吞统计（F12）

```powershell
.\.venv\Scripts\python.exe "C:\Users\admin\AppData\Local\Temp\opencode\audit_swallow.py"
# multiline swallow: 44, inline swallow: 50, sum: 94, bare except: 0
# top: observability/metrics.py 20, main.py 8, api/task_documents.py 5
```

### A.7 运行日志分组（F3/F8/F9/F10）

```powershell
.\.venv\Scripts\python.exe "C:\Users\admin\AppData\Local\Temp\opencode\audit_log_stats.py"
# ERROR: 1758  WARNING: 419  + 分组 Top15（含首末时间）
```

### A.8 生产 DB 只读统计（§6.1）

```powershell
.\.venv\Scripts\python.exe "C:\Users\admin\AppData\Local\Temp\opencode\audit_db.py"
.\.venv\Scripts\python.exe "C:\Users\admin\AppData\Local\Temp\opencode\audit_db2.py"
.\.venv\Scripts\python.exe "C:\Users\admin\AppData\Local\Temp\opencode\audit_db3.py"
# 只读：file:...taskhub.db?mode=ro
```

### A.9 CI（F1）

```powershell
gh run list --limit 100 --json conclusion,workflowName     # CI: 53 failure / Dependency Graph: 1 success
gh run view 37274529093 --log | Select-String "##\[error\]|null"   # 安装步 D:\dev\null 失败
Get-Content .github\workflows\test.yml                     # 2>/dev/null 陷阱 + 仅监听 main/master
```

### A.10 服务在线

```powershell
(Invoke-WebRequest -Uri "http://127.0.0.1:48620/api/v1/status" -UseBasicParsing -TimeoutSec 5).StatusCode  # 200
```

### A.11 文档生命周期 / 门控生产取证（§8.4）

```sql
-- 用只读连接打开生产库：sqlite3.connect("file:...taskhub.db?mode=ro", uri=True)
-- 1) 有 doc_statuses 的任务数
select count(*) from task where doc_statuses is not null and doc_statuses not in ('{}','','null');   -- 31/383
-- 2) 流转事件
select payload from event where type='task_doc_status';            -- 103 行
-- 3) 文档写入/取消事件
select type, count(*) from event group by 1 order by 2 desc limit 15;
--    task_doc_written 194 / task_doc_status 103 / task_doc_scaffolded 9 / task_cancelled 145
-- 4) 解析 payload 的 quality_score / forced：解析脚本 %TEMP%\audit_gate.py、audit_gate2.py
--    → approved: requirement 17 / spec 16 / api 12 / plan 11 = 56
--    → forced 103/103 = null（0 次绕过）
--    → 仅 task 6a630afa 的 api 文档 45 分进入 review/approved（2026-09-23 02:57）
```

### A.12 取消事件 payload 抽查（§6.1）

```sql
select payload from event where type='task_cancelled' limit 3;   -- 全部 null → 无取消原因
```

---

## 附录 B · 本报告引用的一手证据索引

| 代号 | 证据 | 位置 |
|---|---|---|
| E1 | 全量测试输出 | §4.1 / 附录 A.3 |
| E2 | e2e 输出 | §4.2 |
| E3 | 422 竞态探针日志 | §4.4 |
| E4 | CI 失败日志 | §5.2 / `gh run view 37274529093` |
| E5 | 生产 DB 统计 | §6.1 / 附录 A.8 |
| E6 | console.log 分组 | §6.3 / 附录 A.7 |
| E7 | OpenAPI / MCP 内省 | §7 / 附录 A.5 |
| E8 | `.gitignore` 忽略 docs | §2 / 附录 A.2 |
| E9 | `doc_quality.py` / `ratchet.py` 源码行号 | §8.1 / §8.2 |
| E10 | `quality-audit.md` + `audit_quality_gaps.py` 实跑 | §7 C8 |
| E11 | `reliability-verification-report.md` + e2e 复跑 | §4.2 / §7 C9 |
| E12 | `SLO-SLI.md` 目标值 | §7 C10 |
| E13 | 文档生命周期生产取证（103 流转 / 45 分越界 / force=0） | §8.4 / 附录 A.11 |
| E14 | `event` 类型分布 + `task_cancelled` payload 抽查 | §6.1 / 附录 A.11-A.12 |
| E15 | pre-push 钩子原文（fail-open + 路径固化） | §8.5 注 / `.git/hooks/pre-push` |
