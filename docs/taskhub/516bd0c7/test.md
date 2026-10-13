# 测试与验收：hub supervisor/worker 双进程（task 516bd0c7）

## 文档信息

| 项 | 值 |
|---|---|
| 关联任务 | 516bd0c7（hub 改造 supervisor/worker 双进程架构） |
| 关联需求 | `docs/taskhub/requirement-supervisor-worker.md`（FR-1~FR-7，approved） |
| 关联设计 | `docs/taskhub/spec-supervisor-worker.md`（approved） |
| 测试对象 | `packaging/run.py`、`packaging/run_hub.py`、冻结包 `dist/mio-taskhub-v3/mio-taskhub.exe` |
| 日期 | 2026-10-06 |
| 状态 | planned（待推进 passed） |

## 测试范围

- **单元/静态层**：`tests/test_supervisor_worker.py`（8 用例）——不依赖运行态，直接静态断言 `run.py`/`run_hub.py` 的分派、spawn、退避、Job、事件命名与 worker 分支禁用面。
- **集成/真机 E2E 层**：冻结包 `dist/mio-taskhub-v3/mio-taskhub.exe` 上执行 TC-1~TC-6——双进程拉起、单实例、杀父防孤儿、托盘退出时序、端口残留回收、构建脚本。
- **全量回归层**：`pytest` 全量（既有 1136 用例集）+ `npm run build`（web 前端产物）。
- **不测范围**：非本任务的业务 API 功能；`tests/test_spinning_guard.py` 4 个既有环境失败（豁免集，与本任务无交集）；Linux/macOS 平台行为（本任务仅 Windows 路径）。

## 验收标准

| FR | 验收标准 | 覆盖用例 |
|---|---|---|
| FR-1 | supervisor/worker 双进程分派：`--worker` 走 `_worker_main`，不碰托盘/单实例/端口回收 | TC-1、UT-1、UT-2、UT-6 |
| FR-2 | supervisor spawn + 句柄等待 + 退避/冷却重启；expected_exit / code=0 收敛不重启 | TC-1、UT-2、UT-3 |
| FR-3 | Job Object KILL_ON_JOB_CLOSE 防孤儿，失败降级留痕不阻断 | TC-3、UT-4 |
| FR-4 | 命名退出事件 drain（≤3s）+ TerminateProcess 兜底，无 HTTP 退出端点 | TC-4、UT-5 |
| FR-5 | 既有行为不变：单实例互斥/端口探测回收/托盘菜单/双击静默退出 | TC-2/5a、TC-5b、UT-6 |
| FR-6 | 不采用 uvicorn `--workers` 多进程模型，守卫循环单 worker 结构不变 | UT-7 |
| FR-7 | 文档与追溯：requirement/spec/api/plan 全部 approved（质量分 ≥80）、test 文档 FR↔用例追溯闭环、分支 `task-516bd0c7` pre-push 通过 | 本文档（追溯表 7/7）+ 提交/推送记录 |

## 用例清单

| TC | 覆盖 FR | 前置 | 步骤 | 预期 | 实测结果（2026-10-06） |
|---|---|---|---|---|---|
| TC-1 | FR-1/FR-2 | 干净环境，48620 空闲 | 双击 `mio-taskhub.exe` | supervisor 拉起 `--worker` 子进程，48620 服务健康、单托盘 | **PASS**：supervisor+worker 成对，服务 200 |
| TC-2/5a | FR-5 | hub 已运行 | 再次双击 exe | 第二次静默退出，仅 1 个托盘，服务不受影响 | **PASS**：锁 + 端口健康探测双兜底 |
| TC-3 | FR-3 | hub 运行中（supervisor+worker） | `taskkill /f <supervisor pid>` | 秒级内无 `--worker` 残留（Job 连带杀） | **PASS**：worker 随即消失，无残留 |
| TC-4 | FR-4 | hub 运行中 | 托盘右键 → 「退出」 | 进程树清零 ≤5s、48620 可重新 bind、可立即重启且健康 | **PASS**：清零 **3063ms**、bind **3360ms**、重启 **2922ms**；日志链 `exit event signaled` → `drain timeout 3s -> terminate worker` → `converge exit (expected_exit=True)` → `supervisor exit` |
| TC-5b | FR-5 | hub 已停止，无响应残留进程占住 48620 | 启动 exe | 日志 `port busy, no healthy service -> reclaim`，杀残留，接管恢复服务 | **PASS**：reclaim + kill 残留，**7.1s** 恢复服务与托盘 |
| TC-6 | FR-1~FR-5 | 干净树 | `& ".\packaging\build.ps1" -Quick` | 5 项检查全过，产物可直接启动 | **PASS**：vite 2.16s，exe 启动即健康 |

## 单元测试用例与 FR 追溯

| UT | 用例 | 覆盖 FR | 验证点 | 结果 |
|---|---|---|---|---|
| UT-1 | `test_dispatch_passes_worker_flag` | FR-1 | run.py hub 分支将 `--worker` 透传 `run_hub.main(worker=...)` | PASS |
| UT-2 | `test_supervisor_spawn_and_wait` | FR-1/FR-2 | CreateProcessW spawn `hub --worker` + 句柄等待非轮询 | PASS |
| UT-3 | `test_backoff_policy` | FR-2 | 退避 [0,2,5,15] 顶格 + 同码 3 次 → 60s 冷却；收敛不重启 | PASS |
| UT-4 | `test_job_object_usage` | FR-3 | Job KILL_ON_JOB_CLOSE；Assign/SetInformation 失败降级 warn | PASS |
| UT-5 | `test_exit_event_name` | FR-4 | 事件名端口后缀；drain 3s → TerminateProcess 兜底 | PASS |
| UT-6 | `test_worker_no_tray_no_lock` | FR-1/FR-5 | worker 分支不含托盘/单实例锁/端口回收（静态断言） | PASS |
| UT-7 | `test_no_uvicorn_workers_n` | FR-6 | 不使用 uvicorn `--workers` 模型 | PASS |

## 全量回归

- `pytest`：**1136 passed** / 4 failed / 1 skipped / 4 deselected；4 failed 全部为 `tests/test_spinning_guard.py` 既有环境失败（既有豁免），**无新增失败**。
- `npm run build`：✓ built in 2.16s（272 modules，build.ps1 [1/6]）。
- 临时诊断产物（run.py TEMP-DIAGNOSE hook、`_repro_dump.py`）已移除。
