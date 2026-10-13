# 📋 hub 拆分 supervisor/worker 双进程架构 需求规格

## 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | approved |
| 任务 | 516bd0c7 |
| 上游依据 | 用户裁决（2026-10-05）「咱这软件开发也要专业一点」；run_hub.py 现状单进程弱点实测；历史僵尸/孤儿进程踩坑记录（记忆 mem supervisor/release 相关） |
| 更新日期 | 2026-10-05 20:10 |

---

## 背景与问题

**现状**（packaging/run_hub.py main()，行 432 起）：单个 `mio-taskhub.exe hub` 进程内同时承载系统托盘、uvicorn Server（主循环）、apscheduler 后台任务与后台线程；崩溃容错仅靠「进程内守卫循环」——uvicorn 抛 Python 异常 → sleep 2s → 同进程重建 Server。

**四个已证实的弱点**：

1. **崩溃不隔离**：守卫循环只捕获 Python 层 `BaseException`。原生扩展崩溃（access violation）、GIL 死锁、内存耗尽会直接杀死整个进程——托盘、调度器、服务一起消失，无任何自愈。
2. **换 exe 靠手动杀进程**：v0.5.0 发布与后续 rebuild 多次踩「残留同名 exe 占用导致 mv/rm busy」「单实例锁漏判 → 双托盘/端口冲突/僵尸重启循环」（2026-08-29 实测教训已写入 `_single_hub_instance` 注释）。
3. **更新流程中间态脆弱**：`update/runner.py` 靠「hub 退出 → spawn 新 exe」完成更新，退出路径若有线程卡住（托盘、慢 drain）则更新挂起。
4. **内存无界增长无出路**：长驻进程的慢泄漏只能靠用户手动重启，且重启时托盘窗口一起闪没，观感差。

**为什么现在做**：拆成 1 supervisor + 1 worker 是进程管理的标准形态（nginx/gunicorn master-worker 同构），收益明确（崩溃隔离/防孤儿/干净退出）、改动集中在 `run_hub.py` 与 `run.py` 分派、无 API 行为变更、风险可控。

---

## 目标与非目标

| 类型 | 内容 |
|---|---|
| ✅ 目标 | hub 启动后进程树恒为 supervisor + worker 两进程（MCP 等其他进程模型不在此列） |
| ✅ 目标 | worker 崩溃后服务自动恢复，托盘图标全程不消失 |
| ✅ 目标 | 杀 supervisor 不留孤儿 worker；托盘退出后进程清零、端口释放 |
| ✅ 目标 | 现有单实例互斥、端口回收、面板/更新菜单、更新重启流程行为不变 |
| 🚫 非目标 | 不改任何 HTTP API 行为、不改端口（48620）、不改前端 |
| 🚫 非目标 | 不用 `uvicorn workers=N` 多进程（apscheduler 会跨进程重复执行定时任务） |
| 🚫 非目标 | 不引入第三方 supervisor/进程管理依赖（标准库 subprocess + ctypes 即可） |
| 🚫 非目标 | 不改 MCP server 进程模型（opencode 按 MCP 协议须独立 stdio 进程）、不改 night_runner/idle_worker agent 进程模型 |
| 🚫 非目标 | 不做 Linux/macOS 特化（当前发布目标仅 Windows；代码不写死平台判断以外的 Win-only 散落逻辑） |

---

## 📦 范围

| # | 交付项 | 说明 |
|---|---|---|
| 1 | 进程分派 | `run.py`/`run_hub.py` 支持 `hub --worker` 模式（PyInstaller 单 exe 自 spawn） |
| 2 | supervisor | 单实例锁、端口探测/回收、托盘、拉起+监视 worker、退避重启、退出收敛 |
| 3 | worker | uvicorn + 调度器 + 后台线程；无托盘、不抢单实例锁 |
| 4 | 父子绑定 | Windows Job Object（KILL_ON_JOB_CLOSE）防孤儿 |
| 5 | 优雅退出 | 退出通知通道 + worker drain + supervisor 收尸 |
| 6 | 测试与门控 | 新增回归用例、全量 pytest/npm build、打包实跑、文档链 |

---

## 📋 功能需求（FR）

| FR | 需求描述 | 优先级 | 验收要点 |
|---|---|---|---|
| FR-1 | **双进程结构**：`mio-taskhub.exe hub` 启动为 supervisor（主进程，持命名互斥锁 + 端口探测/回收 + 托盘）并以 `mio-taskhub.exe hub --worker` CreateProcess 拉起 worker；worker 仅承载 uvicorn Server + apscheduler + 后台线程，不创建托盘、不重复执行单实例锁与端口回收逻辑 | P0 | 启动后 `Get-CimInstance` 可见两条 `mio-taskhub.exe`（父=supervisor，子命令行含 `--worker`）；托盘图标恰好 1 个；`http://127.0.0.1:48620/healthz` 200 |
| FR-2 | **崩溃隔离与退避重启**：supervisor 等待 worker 进程句柄；worker 非 0 退出（崩溃/被杀）按 0s→2s→5s→15s 封顶退避自动重启；worker 退出码 0 视为正常结束，supervisor 不再重启（防僵尸循环，沿用守卫循环对 SystemExit(3) 的教训） | P0 | `taskkill /f` 杀 worker 后 15s 内新 worker 拉起、服务恢复 200、托盘不消失；worker 正常退出码 0 时 supervisor 不拉起新 worker |
| FR-3 | **防孤儿（Job Object）**：worker 创建后即归入 Job Object 且设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`；supervisor 意外死亡时 worker 被内核连带终止 | P0 | `taskkill /f` 杀 supervisor 后，秒级内无 `--worker` 残留进程；系统重启/会话结束后无孤儿 |
| FR-4 | **优雅退出通道**：托盘「退出」与更新触发的退出统一走「通知 worker drain（uvicorn `should_exit`）→ 等待 worker 退出（超时兜底强杀）→ supervisor 释放互斥锁后退出」；通知机制为命名事件（`CreateEventW`）或等价进程内可见信号，不引入网络依赖 | P0 | 托盘退出后 ≤5s 进程树清零、48620 端口释放、互斥锁可再次获取（连续启动两次第二次正常起） |
| FR-5 | **既有行为不变**：单实例互斥锁语义、端口探测 `_probe_service` / 回收 `_reclaim_port`、托盘菜单（打开面板/更新/退出）、更新流程（runner 退出后 spawn 新 exe）在新结构下行为等价；重复双击启动仍静默退出不产生第二个托盘 | P0 | 既有相关 pytest 全绿；双击两次仅 1 个 supervisor；被残留进程占端口时仍可回收接管 |
| FR-6 | **回归与打包验证**：新增静态回归用例守结构（分派参数、Job Object 调用、退避序列、worker 不建托盘）；全量 pytest 只增不减（存量 `test_spinning_guard` 4 失败豁免）；`npm run build` 绿；`packaging/build.ps1 -Quick` 实跑后 hub 以双进程结构重启 | P0 | 新增用例绿；pytest 1129+ passed；打包第 4 步 5 项 OK 且重启后可见 supervisor+worker |
| FR-7 | **文档链与追溯**：requirement/spec/api/plan 全 approved 且质量 ≥80；test 文档 FR↔用例追溯闭环；分支 `task-516bd0c7`；push 过 pre-push 门控 | P0 | doc_quality 无 errors、追溯全覆盖、pre-push 通过 |

---

## ⚙️ 非功能需求（NFR）

| NFR | 描述 |
|---|---|
| NFR-1 | 零新第三方依赖：仅标准库 `subprocess`/`ctypes`（kernel32 CreateJobObject/AssignProcessToJobObject/SetInformationJobObject/CreateEventW） |
| NFR-2 | 退出时延：托盘点击退出 → 全进程清零 ≤5s（含 drain 超时兜底） |
| NFR-3 | 崩溃恢复时延：worker 死亡 → 新 worker 就绪 ≤20s（15s 退避封顶 + 启动耗时） |
| NFR-4 | supervisor 不承载业务逻辑：无 uvicorn、无调度器、无数据库连接（进程内崩溃面最小化） |
| NFR-5 | 单 exe 交付形态不变：`mio-taskhub.exe` 仍是唯一入口，`--worker` 为内部参数，不写入使用说明对外文档 |
| NFR-6 | 日志可归因：worker 日志沿用现有 console.log/runtime.log 路径并带 `[worker]` 前缀，supervisor 生命周期事件带 `[supervisor]` 前缀 |

---

## 🔗 依赖与约束

| 项 | 内容 |
|---|---|
| 上游 | PyInstaller 单 exe 多模式分派先例（run.py `hub/mcp/widget/apply-update`）；既有 `_single_hub_instance`/`_reclaim_port`/守卫循环退出码教训 |
| 下游 | update runner 退出-重启流程、build.ps1 一键打包、发布 zip 形态 |
| 约束 | 分支 `task-516bd0c7`；push 前 spec/api/plan 必须 approved |
| 约束 | 验证期间 hub 实例会被 build.ps1 重启；Windows Job Object 语义仅 Win10+ 有效（发布目标满足） |
| 约束 | apscheduler 单实例要求：定时任务只能在 worker 进程内跑一份（禁止多 worker） |

---

## ✅ 验收标准

1. 双进程 + 单托盘，`/healthz` 200（FR-1）
2. 杀 worker 自动退避重启、托盘不掉；退出码 0 不重启（FR-2）
3. 杀 supervisor 秒级无孤儿（FR-3）
4. 托盘退出 ≤5s 进程清零、端口与互斥锁释放（FR-4）
5. 互斥/端口回收/托盘菜单/更新流程行为等价（FR-5）
6. 新增回归用例 + 全量 pytest 只增不减 + npm build 绿 + 打包实跑双进程（FR-6）
7. 文档链 approved、追溯闭环、pre-push 通过（FR-7）

---

## 📎 追溯

| FR | 来源（裁决/缺陷） | 测试用例 |
|---|---|---|
| FR-1 | 用户裁决「专业一点」+ 单进程现状 | `test_supervisor_worker_structure`（静态）+ TC-1 真机进程树 |
| FR-2 | 守卫循环只救 Python 异常 | `test_supervisor_backoff_policy`（退避序列/退出码 0 不重启）+ TC-2 杀 worker |
| FR-3 | 历史孤儿/僵尸进程踩坑 | `test_supervisor_job_object`（静态 Job Object 调用）+ TC-3 杀 supervisor |
| FR-4 | 更新流程退出态脆弱 | `test_worker_no_tray_and_drain_signal` + TC-4 托盘退出清零 |
| FR-5 | 单实例锁/端口回收既有语义 | 既有互斥/端口用例（不回退）+ TC-5 双击互斥/端口回收 |
| FR-6 | 质量守恒 | 新增用例全绿 + 全量 pytest + npm build + TC-6 打包实跑 |
| FR-7 | 仓库文档链门控 | doc_quality/追溯报告 + pre-push 记录 TC-7 |
