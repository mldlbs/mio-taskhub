# 🛠️ 实现计划 · hub supervisor/worker 双进程拆分（516bd0c7）

## 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | approved |
| 任务 | 516bd0c7 |
| 上游 | requirement FR-1~FR-7（approved，100 分）、spec（approved，100 分）、api（approved，100 分）、设计评审 71c2e81c |
| 分支 | `task-516bd0c7`（基线 master） |
| 更新日期 | 2026-10-05 20:22 |

---

## 目标快照

单进程 `run_hub.py` → 1 supervisor + 1 worker；Job Object 防孤儿；0/2/5/15s 退避 + 同码 3 次冷却 60s；命名事件优雅退出（3s 兜底强杀）；零 API/前端/依赖变更。

---

## 实施步骤

### Step 1 分支与基线
- `git checkout master && git checkout -b task-516bd0c7`
- 基线记录：全量 pytest `1129 passed / 1 skipped / 4 deselected`（存量 spinning_guard 4 failed 豁免）、`npm run build` 绿。

### Step 2 分派层（FR-1）
- `packaging/run.py`：`mode=='hub'` 时透传 `--worker` → `run_hub.main(worker=True/False)`。
- `mcp`/`widget`/`apply-update` 分支零改动。

### Step 3 run_hub.py 双分支拆分（FR-1/FR-5）
- `_worker_main(port)`：`_stdio()` + uvicorn Config/Server + 既有守卫循环原样迁移；新增 `_watch_exit_event(port, server)` 守护线程；入口 `_log('[worker] started pid=...')`。
- `main(worker=False)`：`worker=True` 直接进 `_worker_main`（**不碰** `_single_hub_instance`/`_reclaim_port`/`_start_tray`）；否则走既有锁/端口/托盘 → 进入 `_supervisor_loop`。
- 既有 `_probe_service`/`_reclaim_port`/`_single_hub_instance`/`_start_tray` 代码不改逻辑，仅挂到 supervisor 分支。

### Step 4 Job Object + spawn（FR-3）
- 新增 `_create_job()`（CreateJobObjectW → SetInformationJobObject(KILL_ON_JOB_CLOSE)，失败 CloseHandle+warn+返回 None）与 `_assign_job(job, h)`（失败 warn 降级返回 False，不阻断）。
- 新增 `_spawn_worker(job)`：`CreateProcessW([exe, 'hub', '--worker'], CREATE_SUSPENDED)` → assign → `ResumeThread`；返回句柄；**spawn 前必须 CloseHandle 上一 worker 句柄**（评审 R5）。

### Step 5 supervisor 监视循环（FR-2）
- `_supervisor_loop`：`WaitForSingleObject(handle, INFINITE)` → `GetExitCodeProcess` →
  - `expected_exit` 或 `code==0` → 收敛退出（不重启）；
  - 连续 3 次相同非 0 码 → `_log('[supervisor] cooldown 60s ...')` + 托盘 notify + sleep 60 + 计数清零；
  - 否则退避 `[0,2,5,15]s`（封顶 15）→ respawn。
- 所有生命周期日志带 `[supervisor]` 前缀（NFR-6）。

### Step 6 优雅退出通道（FR-4）
- `_exit_event_name(port)` → `Local\mio-taskhub-worker-exit-<port>`；worker 侧 `OpenEventW` 失败仅 warn。
- 退出控制器单入口 `request()`：置 `expected_exit=True` → `SetEvent` → 等 worker ≤3s → 超时 `TerminateProcess` → break loop → finally：停托盘 + `_release_hub_lock`。
- 托盘 `_quit` 与 `set_exit_callback`（更新动作）均改调 `request()`（评审决策 6：更新回调落点=supervisor）。

### Step 7 静态回归用例（FR-6）
- 新增 `tests/test_supervisor_worker.py` 7 用例（见 spec 测试计划：dispatch/spawn_wait/backoff/job_object/exit_event/no_tray_no_lock/no_workers_n）。
- 断言方式：源码文本/AST 断言（与既有 test_memory_merge.py 同风格），不启动真进程。

### Step 8 真机验证（FR-1~FR-5）
- TC-1 启动双进程 + `/healthz` 200 + `/api/v1/tasks` 200；
- TC-2 `taskkill /f` 杀 worker → ≤20s 恢复；
- TC-3 `taskkill /f` 杀 supervisor → 秒级无 `--worker` 残留；
- TC-4 托盘退出 → ≤5s 清零 + 端口/锁释放 + 可立即再启动；
- TC-5 双击两次单托盘；残留占端口可回收；
- 脚本断言用 `Get-CimInstance Win32_Process`（计数 + 命令行含 `--worker`）。

### Step 9 回归与打包（FR-6）
- 全量 pytest 只增不减；`npm run build` 绿（本任务无前端改动，build 应无 diff）。
- `packaging/build.ps1 -Quick` 实跑：第 4 步 5 项 OK → 分发复制 → hub 重启 → TC-1 双进程复核。

### Step 10 文档与交付（FR-7）
- test 文档 `docs/taskhub/test-supervisor-worker.md`（FR↔用例追溯 7/7）→ status passed。
- README/CHANGELOG 回填（若涉及启动行为描述：双击后为 supervisor+worker 双进程）。
- commit（引用 FR-1~FR-7）→ push `task-516bd0c7`（pre-push：spec/api/plan approved + FR-n 核验）→ 登记 gitref → taskhub 提交结果。

---

## 验证命令

```powershell
# 静态回归
.venv\Scripts\python.exe -m pytest tests\test_supervisor_worker.py -q
# 全量（豁免 spinning_guard 4 failed）
.venv\Scripts\python.exe -m pytest tests -q
# 前端
npm run build
# 真机双进程断言
Get-CimInstance Win32_Process -Filter "Name='mio-taskhub.exe'" |
  Select-Object ProcessId,ParentProcessId,CommandLine
Invoke-RestMethod http://127.0.0.1:48620/healthz
# 打包
powershell -File packaging\build.ps1 -Quick
```

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| Job Object 降级路径留孤儿 | warn + `_reclaim_port` 兜底 + 用例覆盖 + TC-3 |
| 启动失败进冷却观感像挂了 | 冷却带托盘 notify + `[supervisor] cooldown` 日志 |
| 改动破坏既有互斥/端口回收 | Step 3 只挂分支不改函数体；既有用例全量跑 |
| 回滚 | 单 commit revert 即回单进程结构（改动集中在 run.py/run_hub.py） |

## 交付物清单

`packaging/run.py`、`packaging/run_hub.py`、`tests/test_supervisor_worker.py`、`docs/taskhub/{requirement,spec,api,plan,test}-supervisor-worker.md`、README/CHANGELOG 回填、commit/push 记录。


## 真机验收结果（2026-10-06，冻结包 dist/mio-taskhub-v3/mio-taskhub.exe）

| TC | 用例 | 结果 | 关键数据 |
|---|---|---|---|
| TC-1 | 冻结态 supervisor/worker 双进程拉起 | PASS | supervisor + `--worker` 子进程成对，48620 服务正常、单托盘 |
| TC-2/5a | 双击两次单实例 | PASS | 第二次静默退出（互斥锁漏判走端口健康探测兜底），单托盘 |
| TC-3 | `taskkill /f` 杀 supervisor | PASS | Job Object 连带终止 worker，秒级无 `--worker` 残留 |
| TC-4 | 托盘「退出」→ 进程树清零 ≤5s + 端口释放 + 可重启 | PASS | 命名事件置位 → worker drain ≤3s → `drain timeout 3s -> terminate worker` → `converge exit (expected_exit=True)` → `supervisor exit`；实测：进程树清零 **3063ms**、端口 **3360ms** 可 bind、重启后 **2922ms** 健康（均 ≤5000ms） |
| TC-5b | 端口被无响应残留占用 → 回收接管 | PASS | 日志 `port 48620 busy, no healthy service -> reclaim` → `reclaim port 48620: kill pid=…` → **7.1s** 恢复服务 + 托盘 + 新 worker 接管 |
| TC-6 | `packaging/build.ps1 -Quick` | PASS | 5 项检查全过（PIL/web/dist/exe），vite 2.16s，产物可直接启动 |

回归验证：
- `pytest`：**1136 passed** / 4 failed / 1 skipped（4 failed 均为 `tests/test_spinning_guard.py` 既有环境失败，与本任务无关，沿用既有豁免）/ 4 deselected。
- `npm run build`（web，于 build.ps1 [1/6] 执行）：✓ built in 2.16s。
- 临时诊断产物（`packaging/run.py` TEMP-DIAGNOSE hook、根目录 `_repro_dump.py`）已全部移除；`git status` 仅剩 `packaging/run.py`、`packaging/run_hub.py`（实现）与新增 `tests/test_supervisor_worker.py`。
