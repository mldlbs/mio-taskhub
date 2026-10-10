# 📐 hub supervisor/worker 双进程拆分 技术规格（supervisor-worker）

## 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | approved |
| 任务 | 516bd0c7 |
| 上游需求 | docs/taskhub/requirement-supervisor-worker.md v1.0（FR-1~FR-7） |
| 上游依据 | 设计评审 discussion 71c2e81c（技术/运维/红队/产品 四视角，6 决策 5 风险） |
| 更新日期 | 2026-10-05 20:18 |

---

## 概述

`run_hub.main()` 从「单进程内托盘+uvicorn+调度器+守卫循环」重构为 **1 supervisor + 1 worker** 双进程：supervisor 负责生命周期（锁/端口/托盘/子进程管理），worker 承载业务（uvicorn + apscheduler）。零新依赖、零 API 变更、单 exe 交付形态不变。

---

## 模块职责与边界

| 模块 | 职责（做什么） | 边界（不做什么） |
|---|---|---|
| `run.py`（分派层） | 解析 `hub [--worker]` 参数，把 `--worker` 透传给 `run_hub.main` | 不做进程管理 |
| `run_hub.py` supervisor 分支 | 单实例锁、端口探测/回收、托盘（打开面板/更新/退出）、CreateProcess 拉起 worker、Job Object 绑定、等待+退避重启、退出收敛（drain→收尸→释放锁） | 不跑 uvicorn、不连数据库、不起调度器（NFR-4） |
| `run_hub.py` worker 分支 | uvicorn Server 守卫循环（保留现有异常重启语义）、apscheduler、后台线程、命名事件监听 drain | 不创建托盘、不创建单实例锁、不跑端口回收（运维静态用例守住） |
| Job Object 封装 `_job_object.py`（同文件内函数组） | CreateJobObjectW + SetInformationJobObject(KILL_ON_JOB_CLOSE) + AssignProcessToJobObject；失败降级返回 False | 不重试、不抛异常阻断启动（降级=留痕+句柄等待） |
| 退出事件 `_exit_event(port)` | `Local\mio-taskhub-worker-exit-<port>` 命名事件的创建/等待/置位 | 不用 HTTP 退出端点（评审决策） |
| `mio_taskhub/update/runner.py` | 退出回调注册点不变，但语义落点=supervisor 收到后走优雅退出路径 | 不直接杀 worker |
| 既有 `_single_hub_instance`/`_probe_service`/`_reclaim_port` | 原样保留在 supervisor 分支 | 不进 worker |

---

## 架构设计

```
mio-taskhub.exe hub                      ← supervisor（父进程）
  ├─ CreateMutexW  单实例锁（既有）
  ├─ _probe_service / _reclaim_port      端口探测与残留回收（既有）
  ├─ _start_tray                         托盘（既有，退出/更新动作改走新收敛路径）
  ├─ _create_job()  CreateJobObjectW + KILL_ON_JOB_CLOSE
  ├─ CreateProcessW(exe, "hub --worker", CREATE_SUSPENDED)
  │     └─ AssignProcessToJobObject → ResumeThread
  │        （Assign 失败 → 仍 ResumeThread，runtime.log warn "[supervisor] job object degraded"）
  ├─ 等待循环: WaitForSingleObject(worker) → exit_code
  │     ├─ expected_exit / exit_code==0 → 收敛退出，不重启
  │     ├─ 连续 3 次相同非 0 码 → 冷却 60s（log + 托盘 notify）后重置计数
  │     └─ 否则退避 [0, 2, 5, 15]s（封顶）重启（先 CloseHandle 再 spawn，防句柄泄漏）
  └─ 退出路径: SetEvent(exit_event) → 等 worker ≤3s → TerminateProcess 兜底 → CloseHandle(job)
                → _release_hub_lock → 自身退出

mio-taskhub.exe hub --worker             ← worker（子进程）
  ├─ （不碰互斥锁/端口回收/托盘）
  ├─ 守护线程: WaitForSingleObject(exit_event) → server.should_exit = True
  ├─ uvicorn Server.run() 守卫循环（既有异常语义保留）
  └─ apscheduler + 后台线程（既有）
```

**关键设计决策（承接评审 6 决议）：**

| 决策 | 选择 | 理由 |
|---|---|---|
| 进程数 | 1 supervisor + 1 worker，**禁止 uvicorn workers=N** | apscheduler 跨进程会重复执行定时任务 |
| 防孤儿 | Job Object KILL_ON_JOB_CLOSE，Assign 失败降级留痕 | 内核级父死子杀；降级不阻断启动，`_reclaim_port` 兜底 |
| 退出通知 | 命名事件 `Local\mio-taskhub-worker-exit-<port>` | supervisor 不反向依赖业务 app、无网络假设；端口后缀防多实例互扰 |
| 重启策略 | 退避 [0,2,5,15]s 封顶 + 连续 3 次同非 0 码冷却 60s + 退出码 0/expected_exit 不重启 | 防端口占用型僵尸循环（2026-08-29 SystemExit(3) 教训） |
| drain 兜底 | 置位事件后等 worker ≤3s，超时 TerminateProcess | 慢请求卡死不阻塞退出（NFR-2 ≤5s） |
| 更新回调 | `set_exit_callback` 注册在 supervisor，动作=优雅退出路径 | 更新「先退干净再 spawn」顺序保持不变 |

---

## 详细设计

### 1. 分派与入口（FR-1）

`run.py main()`：`mode == 'hub'` 且 `'--worker' in sys.argv[1:]` → `run_hub.main(worker=True)`；否则 `run_hub.main(worker=False)`。`mcp`/`widget`/`apply-update` 分派不动。

### 2. supervisor 主流程（FR-1/FR-3/FR-5）

```python
def main(worker: bool = False):
    if worker:
        return _worker_main()          # 不碰锁/端口/托盘
    lock = _single_hub_instance()      # 既有
    ...端口探测/回收（既有）...
    tray = _start_tray(url, exit_controller)   # 退出动作改为 exit_controller.request()
    job = _create_job()                       # 失败 → None + warn
    _supervisor_loop(exe, port, job)          # spawn/等待/退避/冷却
```

- `_spawn_worker(job)`：`CreateProcessW([exe, 'hub', '--worker'], CREATE_SUSPENDED)` → Assign → `ResumeThread`；返回 `(handle, pid)`。**每次重启前必关上一个 handle**（评审风险 R5）。
- `_supervisor_loop`：`WaitForSingleObject(handle, INFINITE)` → `GetExitCodeProcess` → 分支判定（expected_exit 标志由退出控制器置位）。
- 退避计数器：`streak`（连续相同非 0 码次数）与 `last_code`；`streak >= 3` → 冷却 60s + `icon.notify`，然后 `streak=0`。
- 托盘「退出」与更新回调统一进 `exit_controller.request()`：`SetEvent` → 等 worker ≤3s → 兜底 `TerminateProcess` → break loop → finally 释放锁/停托盘。

### 3. worker 主流程（FR-1/FR-4）

```python
def _worker_main():
    _stdio()                             # 既有日志重绑
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    threading.Thread(target=_watch_exit_event, args=(port, server), daemon=True).start()
    while True:                          # 既有守卫循环原样保留（异常重启/SystemExit 退出）
        ...
```

- `_watch_exit_event`：`OpenEventW(SYNCHRONIZE, name)` → `WaitForSingleObject` → `server.should_exit = True`（打开失败 → runtime.log warn，退出仍可由兜底强杀收敛）。
- worker 内**不得**出现 `_single_hub_instance`/`_start_tray`/`_reclaim_port` 调用（静态用例断言源码不含）。

### 4. Job Object 封装（FR-3）

`_create_job()`：`CreateJobObjectW(None, None)` → `SetInformationJobObject(JobObjectExtendedLimitInformation, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)`；任一步失败 `CloseHandle` + `[supervisor] job object degraded` warn + 返回 None。`_assign(job, handle)` 失败同样 warn 但**不阻断**（worker 继续跑，靠句柄等待 + `_reclaim_port` 兜底）。

### 5. 日志归因（NFR-6）

- supervisor 生命周期事件：`_log('[supervisor] spawn worker pid=...')` / `restart backoff=2s code=1` / `cooldown after 3 identical failures` / `job object degraded`。
- worker 既有日志不动（console.log 已有路径），入口处 `_log('[worker] started pid=...')`。

### 6. 与既有代码的差异清单

| 文件 | 改动 |
|---|---|
| `packaging/run.py` | `hub` 分派识别 `--worker` 并透传 |
| `packaging/run_hub.py` | main 拆双分支；新增 `_create_job`/`_assign`/`_spawn_worker`/`_supervisor_loop`/`_watch_exit_event`/`_exit_event_name`/exit_controller；托盘 `_quit` 与 `set_exit_callback` 改走 exit_controller；worker 分支保留原 uvicorn 守卫循环 |
| `mio_taskhub/update/runner.py` | 不改代码（回调语义经 set_exit_callback 注册点自动落到 supervisor）；仅验证顺序 |
| `packaging/build.ps1` | 不改（重启后验收断言双进程在测试脚本做） |

---

## 接口设计

**无新增/修改 HTTP 端点。** 既有 API、端口（48620）、`/healthz`、`/api/v1/*` 全部不变（FR-5/NFR-5）。进程内部信号（命名事件）不属于 HTTP 契约，固化在本 spec §2/§3，不进 api 文档接口清单（api 文档仅声明「零端点变更」及进程结构对调用方透明）。

---

## 前端设计

不适用（零前端改动）。托盘菜单文案、更新提示文案不变。

---

## 测试计划

**新增 `tests/test_supervisor_worker.py`（静态回归，7 用例）：**

| 用例 | 覆盖 | 断言要点 |
|---|---|---|
| `test_dispatch_passes_worker_flag` | FR-1 | run.py 源码含 `--worker` 分派且透传给 run_hub.main |
| `test_supervisor_spawn_and_wait` | FR-1/FR-2 | run_hub 含 CreateProcessW(… '--worker' …) 与 WaitForSingleObject/GetExitCodeProcess |
| `test_backoff_policy` | FR-2 | 退避序列字面 [0,2,5,15]、冷却阈值 3、冷却时长 60、退出码 0 不重启分支存在 |
| `test_job_object_usage` | FR-3 | 含 CreateJobObjectW/KILL_ON_JOB_CLOSE/AssignProcessToJobObject，Assign 失败有 warn 降级分支 |
| `test_exit_event_name` | FR-4 | 事件名模板含 `mio-taskhub-worker-exit-` 与 `<port>` 后缀；drain 兜底超时 3s 存在 |
| `test_worker_no_tray_no_lock` | FR-1/FR-5 | `_worker_main` 函数体（按行切片）不含 `_single_hub_instance`/`_start_tray`/`_reclaim_port` |
| `test_no_uvicorn_workers_n` | 非目标 | run_hub 无 `workers=` 参数传给 uvicorn（防 apscheduler 重复） |

**真机验证（TC）：**

| 编号 | 步骤 | 预期 |
|---|---|---|
| TC-1 | 启动 hub | 两条 mio-taskhub.exe（子命令行含 `--worker`）、托盘 1 个、`/healthz` 200 |
| TC-2 | `taskkill /f` 杀 worker | ≤20s 新 worker 拉起、服务恢复 200、托盘不消失 |
| TC-3 | `taskkill /f` 杀 supervisor | 秒级无 `--worker` 残留（Job Object）；降级分支则记录 warn 且重启后 `_reclaim_port` 收回 |
| TC-4 | 托盘「退出」 | ≤5s 进程树清零、48620 释放、可立即再次启动（互斥锁释放） |
| TC-5 | 双击启动两次 / 端口被残留占用 | 第二次静默退出单托盘；残留被回收接管 |
| TC-6 | `build.ps1 -Quick` 实跑 | 校验 5 项 OK、重启后 TC-1 双进程结构成立 |

**回归：** 全量 `pytest -q` 只增不减（存量 `test_spinning_guard` 4 失败豁免）；`npm run build` 绿（FR-6）。

---

## 性能与容量

| 项 | 结论 |
|---|---|
| 启动开销 | 多一次 CreateProcess ≈ 100-300ms，用户无感 |
| 内存 | supervisor 极轻（无业务 import），worker ≈ 现单进程内存，总量略增 10-20MB |
| 退出/恢复时延 | 退出 ≤5s（NFR-2）；崩溃恢复 ≤20s（NFR-3，15s 封顶退避+启动） |

---

## 风险与对策

| 风险（评审 R1-R5） | 对策 |
|---|---|
| Job Object Assign 失败降级留孤儿 | warn 日志 + `_reclaim_port` 兜底 + `test_job_object_usage` 覆盖降级分支 + TC-3 |
| 端口占用型僵尸循环 | 连续 3 次同非 0 码 → 冷却 60s（`test_backoff_policy`） |
| 命名事件提前置位假退出 | 事件名带端口后缀；假退出走正常退避重启，可恢复 |
| drain 卡死退出不返回 | 3s 超时 TerminateProcess 兜底（`test_exit_event_name`） |
| 句柄泄漏 WaitFor 失效 | 重启前 CloseHandle（`test_supervisor_spawn_and_wait`） |
| 托盘线程先于收敛结束 | 托盘 stop 放 finally，退出控制器单入口 |

---

## 验收标准（映射 FR）

| FR | 验证方式 |
|---|---|
| FR-1 | `test_dispatch_passes_worker_flag` + `test_supervisor_spawn_and_wait` + `test_worker_no_tray_no_lock` + TC-1 |
| FR-2 | `test_backoff_policy` + TC-2 |
| FR-3 | `test_job_object_usage` + TC-3 |
| FR-4 | `test_exit_event_name` + TC-4 |
| FR-5 | `test_worker_no_tray_no_lock` + 既有互斥/端口用例不回退 + TC-5 |
| FR-6 | 新增 7 用例绿 + 全量 pytest + npm build + TC-6 |
| FR-7 | doc_quality / 追溯 / pre-push（TC-7） |

---

## 📎 追溯

| FR | 本规格章节 | 测试用例 |
|---|---|---|
| FR-1 | 架构设计、详细设计 1/2/3 | dispatch/spawn/no_tray 三用例、TC-1 |
| FR-2 | 详细设计 2、决策-重启策略 | `test_backoff_policy`、TC-2 |
| FR-3 | 详细设计 4 | `test_job_object_usage`、TC-3 |
| FR-4 | 详细设计 2/3、决策-退出通知 | `test_exit_event_name`、TC-4 |
| FR-5 | 模块职责边界、详细设计 3 | `test_worker_no_tray_no_lock`、TC-5 |
| FR-6 | 测试计划-回归 | 全量 pytest + npm build + TC-6 |
| FR-7 | （本文档链） | doc_quality + 追溯 + pre-push（TC-7） |
