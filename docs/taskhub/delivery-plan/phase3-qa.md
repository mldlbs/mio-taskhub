# 测试策略与验收清单（mio-taskhub v0.6.1 交付方案 · Phase 3 / 任务 #8）

> 编制：凌无缺（测试验收工程师）｜取证日 2026-11-06
> 事实底稿：`docs/taskhub/_delivery-fact-pack.md`（唯一事实基线）
> 已完整读取：`frozen-scope-20261021.md`、`diff-gate-grayscale-log.md`、`narrow-gate-implementation-plan-20261020.md`、`d468e633/test.md`、`d468e633/requirement.md`、`acceptance-audit-p4.md`、`docs/roadmap/02-current-state-audit.md`、`docs/roadmap/05-execution-plan.md`、`pyproject.toml`、`delivery-plan/phase1-requirements.md`（AC-01~AC-22）、`phase1-architecture.md`、`phase2-backend.md`、`phase2-frontend.md`、`phase2-data.md`、`build-release-checklist.md`。
> 本次源码复核（只读）：`mio_taskhub/diff_gate.py`、`mio_taskhub/api/runs.py`、`mio_taskhub/api/board.py`、`web/src/api.js`、`web/src/App.jsx`、`web/src/components/TaskDetail.jsx`、`tests/conftest.py`、`tests/test_run_diff_gate.py`、`tests/test_diff_gate_stats.py`、`tests/test_spinning_guard.py`、`tests/test_night_runner.py`、`.github/workflows/test.yml`、`.smoke_ws/gate_smoke.sh`、`README.md`。
> 本次执行的取证命令（不修改任何源码）：`pytest --collect-only -q`（隔离库 `MIO_TEST_DB_PATH`）、`pytest --collect-only`、`ls tests/test_*.py | wc -l`、`grep -c 'ok "' .smoke_ws/gate_smoke.sh`、源码定点 `Read` / `Grep`。**未跑全量测试**（约 7 分钟且已知存在 pre-existing 失败）。
> 口径声明：版本 0.6.1 / 176 REST / 56 MCP / 22 类文档 kind / 7 件套 / 9 类生命周期状态机取事实包 §2（README v0.6.1 口径）。数字引用一律标注口径与取证日。文档门控只称「结构完整性校验」与「服务端强制」。Star / Trending / fork / 转发数不作任何 Gate 判据。
> 表达约束：工程汇报口吻、主语明确、陈述句、给字段名与阈值；不确定处标【待确认】。禁用词清单按事实包 §4 G4 执行，本文不引用被禁表述。冻结 8 项按「代码不删、接口不删、文档保留；停投入、停调度、停当价值宣称、移出主链路」记录，不写成待开发功能。

---

## 主理人补充（2026-11-06 同日并行审计口径对齐 + 2026-11-07 续接取证，19:15 第三次取证后再校）

本册由测试验收工程师独立编制，下表是主理人本轮复核后叠加的事实。**冲突处以主方案 `final-delivery-plan.md` 为准**；本册其余结论、七口径表、QA-T1~QA-T11 任务卡与 18 项【待确认】保持原样。

| # | 本册口径 | 主理人复核 | 处置 |
|---|---|---|---|
| 1 | §0.2 七种用例数口径；`--collect-only` 实测 `1904/1908 collected (4 deselected)` | 与同日 `deliverables/gstack/pre-launch-check-mio-taskhub-2026-11-06.md` 的 QA 全量 **1904 collected / 1901 passed / 1 failed / 2 skipped / 4 deselected，12m28s，0 新增回归** 一致 | 两处互证。README 1609 是脚本正则统计、1904 是 pytest 实际收集，统计方式不同，不可互换或相减 |
| 2 | 口径 C「1235 passed + 4 pre-existing failed」 | 原 4 个 webhook_guard 失败用例**已删除**（`tests/test_spinning_guard.py:6-9` 注释：`CronEngine._should_skip_webhook_generation` 已随生成守卫下线、无迁移目标），基线口径作废 | CI `test.yml:36` 的 `-k "not test_webhook_guard"` 属过期排除项，W-4 清理；`not test_two_agents_race` 逐个复核 |
| 3 | 本册未涉及发布安全 | 同日上线前全检结论为 **No-Go（对外分发）**：3 个安全 P0（未鉴权 `shell=True` RCE / 任意绝对路径读明文 apiKey / 任意文件写）。取证当时 `scheduling/night_runner.py:152` 与 `api/task_documents.py:20-28` 仍在源码中；**2026-11-07 19:07 三个 P0 已随 `2e42305` 提交冻结**（`shell=False`+argv+白名单 / `path_guard.doc_read_error` / `path_guard.workspace_error`），回归用例 21+11 | 主方案 §4.2.1 曾列 S-1~S-4、§5.2 曾列 AC-23~AC-26（2026-11-06 口径）；**2026-11-08 裁决后** S-4 部署动作不做、AC-26 按部署形态条件化。G4 仍把 P0 清零列为硬前置。**测试全绿不构成发布依据**；S-1~S-3 转为「冻结 commit 上复验 AC-23~AC-25」，S-4 四条通道已落地（19:50，工作树未提交）；按 2026-11-08 裁决当前不做默认 auth、#28 降为条件项，冻结 commit 上复验 AC-26 默认形态 |
| 4 | §0.2 QC-02「底稿 13 字段 / 源码 12 键」 | 已逐行复核 `mio_taskhub/api/board.py:371-386`，确为 **12 个顶层键** | QC-02 收口：按 12 键逐键断言、不按总数判定；主方案已统一改为 12 |
| 5 | §1.2 README 统计 | 正文 5 处旧数字清理在工作树已生效但**未提交**。`git archive HEAD` 干净快照实测 `--check` EXIT=0，「干净检出 HEAD 红 CI」已更正为不成立。**19:40 陷阱已修复**：`724601b` 已在干净 worktree 重算并提交顶部表（`33,784 行 / 53 MCP / 12,102 行 / 1635 用例`），工作树 W-5 曾残留更旧的 `33,697 / 56 MCP / 12,935 / 1611`，现已精确对齐；`git diff -- README.md` 为 **5 insertions / 5 deletions**，只剩正文 5 处，顶部表与 HEAD 逐行一致 | README 现可直接提交，不再需要「只重放正文 hunk」。W-10 在冻结 commit 上跑 `--write` + `--check`；当前工作树 `--check` 报 4 处漂移属 5 个未跟踪测试文件与并行写入的移动目标，不是 README 写错。详见主方案 §0.1、§4.4.1 |
| 6 | §1.2 测试隔离 | `tests/test_installer.py::test_gui_install_runs_under_real_mainloop` 曾无目录隔离，同日已实际把本机真实 `TaskHub.exe` 换成 8 字节假 exe；已用合并版安装包恢复（825 文件 / 82MB，合法 x64 PE）。**隔离修复已随 `2e42305` 提交**（真实安装存在即 skip / patch `gui` 命名空间 / 目标断言前置） | W-10 需在冻结 commit 上确认保险丝仍在；全量测试 basetemp 必须放**仓库外**。**W-10 已确认（2026-11-08，`093869b`）**：保险丝随冻结 commit 入库，全量 `1947 passed / 0 failed / EXIT=0` |
| 7 | §0.2 口径 G 与 devops 分册的数字差 | `docs/roadmap/02` 文件实测（`:132`、`:137-144`、`:510-513`）为 **1269 collected / 1260 passed / 4 failed / 1 skipped / 4 deselected**；G0 冻结基线为 1284 passed / 0 failed / cov 80.0%（`04-review-20261014.md:10`、HEAD `ac21dfc0`）；`1372+ passed / failed=0` 唯一出处是 `t43:10`（2026-10-14）。本册口径 G 正确；「1372 passed / 3 skipped 归 roadmap/02」在仓库内无源（`3 skipped` 在 `docs/roadmap/` 全目录无匹配） | 裁定：六份快照分属四个不同文件、不同取证日，按「文件名 + 日期 + 输出行」标注，单一数字不作门禁。主方案 §5.4 与 §7 第 2 项已统一，两份分册不再互相打架 |
| 8 | 本册 AC-26 / 部署鉴权验收项未涉及前端 | 2026-11-07 初轮只读取证只发现 WS：服务端 `main.py:377-385` 在 `auth_token` 非空时校验 `?token=` / Bearer，前端 `App.jsx:247`、`components/EmbedView.jsx:26` 裸连。**19:15 实测把影响面扩大**：`req()`(`api.js:34-47`) 无 `Authorization`，启用 token 后 `/`、`/docs`=200 但 `/api/v1/*`、`/metrics`、`/logs` 全部 **401**，WS 断连，`X-TaskHub-Admin` 头也不足（全局中间件先拦成 401）；另有 5 处绕过 `req()` 的裸 `fetch` 与 `rawFileUrl` 的 `<img src>`/`<a href>`。**19:50 四条通道已落地**：新增 `web/src/auth.js`，`req()`/裸 fetch/WS/图片链接全部接入，`npm run build` 通过，Node 探针 12/12、四条通道真实 HTTP 200、反面 401、`tests/test_frontend_auth_wiring.py` 5 passed | 主方案 §0.3 事实 17 / 19、§4.2.1 S-4、§4.4.1「第四次取证」、§5.2 AC-26、§6 风险清单已全部改写；本册涉及 S-4 / AC-26 的验收项**以主方案为准**，冻结 commit 上仍须复验 AC-26 默认形态。**2026-11-08 裁决**：当前定位不做默认 auth，主方案 §7.2 #28 降为按部署形态条件化（仅决定非环回 / 多机访问时才需定），不再阻塞 G4；AC-26 分默认 / 条件两形态，默认形态只验「无 token 零变化」。`getToken()` 已把读取逻辑收敛到一处，替换策略不影响四条通道 |

---

## 0. 取证说明与口径差异登记

### 0.1 本次取证动作

| 动作 | 命令 / 路径 | 结果 |
|---|---|---|
| 只读收集用例数 | `MIO_TEST_DB_PATH=<隔离库> pytest --collect-only -q` | `1904/1908 tests collected (4 deselected) in 4.57s` |
| 只读收集用例数（复跑） | `MIO_TEST_DB_PATH=<隔离库> pytest --collect-only` | `1904/1908 tests collected (4 deselected) in 3.79s` |
| 测试文件数 | `ls tests/test_*.py \| wc -l` | 179 个 `test_*.py` 文件 |
| e2e 标记 | `grep -rn "pytest.mark.e2e" tests/` | 仅 `tests/test_e2e_real_service.py:29` 一处 `pytestmark`；默认 `addopts = -m 'not e2e'` 排除 4 个用例 |
| 门控冒烟项数 | `grep -c 'ok "' .smoke_ws/gate_smoke.sh` | 11 项 |
| diff-gate-stats 返回键 | 源码 `mio_taskhub/api/board.py:371-386` | 12 个顶层键（见 0.2 QC-02） |
| 窄门禁相关测试资产 | `tests/test_run_diff_gate.py`、`tests/test_diff_gate_stats.py` | 前者覆盖 TC-1~TC-10、纯函数单元、只读自查端点；后者 5 个统计端点用例 |

### 0.2 用例数与字段数口径差异（全部标【待确认】）

| 编号 | 口径 | 数值 | 来源 |
|---|---|---|---|
| 口径 A | README 顶部统计（事实包 §2 记录值） | 1601 用例 / 55 文件 / 8,480 行 | `_delivery-fact-pack.md` §2 |
| 口径 B | 当前工作树 README 顶部实际值 | 1609 用例 / 55 文件 / 8,480 行 | `README.md:14` |
| 口径 C | 全量实跑基线（主理人给定） | 1235 passed + 1 skipped + 4 deselected + 4 pre-existing failed | 任务 #8 已知事实 |
| 口径 D | 本次 `--collect-only`（2026-11-06，隔离库） | 1904/1908 collected（4 deselected） | 本次取证 |
| 口径 E | README 目录树注释 | 1305 个用例 | `README.md:758` |
| 口径 F | pyproject 注释的 T0.1 基线 | 1113 passed | `pyproject.toml:14` |
| 口径 G | 2026-10-10 审计实测 | 1269 collected / 1260 passed / 4 failed / 1 skipped / 4 deselected | `02-current-state-audit.md` §4.1 |

以上七个数字分属不同日期、不同统计对象（README 手工统计 / 历史实跑 / 当前工作树收集数），互不换算。**README 顶部 1601 与实测基线 1235 passed 的差异属【待确认 QC-01】**；本次不给出换算结论，验收断言按「实际执行的命令 + 实际输出行」判定，不按任何单一数字判定。

### 0.3 判据源码优先的 5 处口径（验收断言以源码为准）

| # | 判据 | 源码事实 | 码位 | 文档差异 |
|---|---|---|---|---|
| 1 | 拒绝次数上限 | `MAX_REJECTIONS = 2`；`if run.rejections > MAX_REJECTIONS:`（`>2`，即**第 3 次**才 409 `run.empty_diff_repeated`） | `diff_gate.py:33`、`api/runs.py:480` | 需求 FR-5 / `d468e633/api.md` 写「≥2」，属文档差异 |
| 2 | 采集函数 | `diff_gate.py` **没有独立 `collect()`**；采集由 `snapshot()` + `_numstat()` + `check_diff_gate()` 完成；采集失败返回 `reason=collect_failed` | `diff_gate.py:102/117/147/283` | `narrow-gate-implementation-plan` §2 B4 使用 `collect()` 命名 |
| 3 | `Run.diff_summary.after` 结构 | 只有 `{files, insertions, deletions}` 三字段；`changed` / `reason` / `mode` 平铺在顶层 | `api/runs.py:421-430` | 需求 §4.2 / `d468e633/api.md` 描述为 6 字段完整快照 |
| 4 | 抛 422 前落库 | `_apply_diff_gate` 拦截路径在 `raise HTTPException` 前显式 `db.commit()`；放行路径亦 `db.commit()`；409 路径在 `_handle_failure` 后再次 `db.commit()` | `api/runs.py:451/478/499` | `db.get_session()` 退出只 close 不 commit，不显式 commit 则事件随会话丢弃 |
| 5 | 可见性事件载体 | `emit_event(type="run.result_rejected", entity="task")`；`HistoryEvent(type="rejected")`；`Run.annotations(level="block")`；warn 级 `level="warn"` | `api/runs.py:456-477` | `events.py:43-48` 的 `MESSAGE_TYPES` 不含 `run`；`TaskEvent` 会渲染成 `null·null` |

`api/runs.py:478` 的 `db.commit()` 是 AC-03 的断言对象，不是实现建议。若该 commit 缺失，`Event`、`HistoryEvent`、`Run.annotations`、`Run.rejections`、`Run.diff_summary` 全部随会话回滚，唯一成功判据 M2 无法举证。

---

## 1. 测试策略

### 1.1 分层结构、范围、责任方与自动化目标

| 层级 | 范围 | 责任方 | 自动化目标 |
|---|---|---|---|
| 单元测试 | `diff_gate.py` 纯函数（`differs` / `gate_mode` / `merge_annotations` / `snapshot`）、`doc_quality` / `ratchet` / `read_evidence` / `planner` / `cpm.js` 等 | 后端 / 前端工程师提供，测试验收工程师复核断言 | 已落地，本期不新增运行器；新增断言仅限窄门禁专项 |
| 集成测试（TestClient） | FastAPI `TestClient(app)` + 隔离 SQLite，覆盖 176 REST 端点、`submit_result` 门控链、文档状态门控、事件流 | 后端工程师提供，测试验收工程师定验收命令 | 已落地；窄门禁 `test_run_diff_gate.py` + `test_diff_gate_stats.py` 为现状基线 |
| 门控冒烟（活体） | `.smoke_ws/gate_smoke.sh`：独立 uvicorn 48621 + 空临时库 `gate_smoke.db`，11 项断言，覆盖**阶段推进生命周期门控 LIFECYCLE_GATE**（DESIGN / PLANNING 路径 + `force=true` + `task_stage_gate_forced` 审计事件） | 测试验收工程师 | 现状为手工执行脚本；是否纳入发布 checklist 与 CI 属【待确认 QC-17】 |
| 前端 | `web/src` 49 文件 / 12,532 行、13 个 view 分支；三链路窄门禁渲染 | 前端工程师提供改动，测试验收工程师定验收断言 | **零测试框架**：`web/**/*.{test,spec}.*` 无文件；`package.json` 仅 `dev/build/preview`，无 `test`/`lint`/`typecheck`。本期不引入，见 §3.3 与 QC-04 |
| 端到端（真服务） | `pytest -m e2e`，真 uvicorn + 真 DB + 真 HTTP，4 用例；默认被 `addopts` 排除 | 测试验收工程师 | 已落地，本期不扩 |
| 覆盖率 | `pytest --cov=mio_taskhub --cov-report=term-missing --cov-report=xml`，`source=["mio_taskhub"]`、`branch=false`、`parallel=false` | 测试验收工程师 | G0 本地基线 80.0%（事实包 §2）；**未接入 CI 门禁**，见 1.4 |

### 1.2 测试现状

| 项 | 现状 | 取证 |
|---|---|---|
| 配置 | `testpaths=["tests"]`；`markers = e2e / slow`；`addopts = "-m 'not e2e'"`；dev 依赖 `pytest>=8.0` / `httpx>=0.27` / `pytest-cov>=5.0` | `pyproject.toml:47-67` |
| 测试资产 | 179 个 `test_*.py` 文件；`tests/conftest.py` 读 `MIO_TEST_DB_PATH`，写入 `MIO_TASKHUB_DB`，启动时清理 `_TEST_DB` | `ls tests/test_*.py`、`tests/conftest.py:8-12` |
| 用例数 | 见 §0.2 七种口径，差异【待确认 QC-01】 | 本次 `--collect-only` + 事实包 + README |
| 覆盖率 | G0 修复后本地基线 80.0%；`--cov` 不进 `addopts`，按需执行；`coverage.xml` 按需产出 | 事实包 §2、`pyproject.toml:55-67`、AC-01 |
| CI 接入 | `.github/workflows/test.yml`：`windows-latest` / `py3.12`，两个 job（`test`、`materials-gate`） | 见 1.4 |
| 已知失败与 flaky | 分三类，见 1.3 | 任务 #8 已知事实 + 本次文件复核 |
| 跑测纪律 | 全量跑加 `--basetemp=<独立目录>`；并行跑用 `MIO_TEST_DB_PATH` 隔离；失败名单落盘 `pytest --lf -q -rf > out.txt` | `narrow-gate-implementation-plan` §7、`d468e633/test.md`「测试环境」 |

`--basetemp` 的原因：全量跑结束时 pytest 清理 `pytest-of-admin/garbage-*`，一次性删除项 87 项 > safe-delete 批量删除保护阈值 50，进程在 teardown 被杀，拿不到汇总行。`MIO_TEST_DB_PATH` 隔离的原因：多个 pytest 进程或 pytest 与常驻 hub 争用同一临时 DB 时抛 `PermissionError: [WinError 32]`。

### 1.3 已知失败与 flaky 清单及处置策略

| 类型 | 对象 | 现象 / 根因 | 处置策略 |
|---|---|---|---|
| pre-existing | `tests/test_spinning_guard.py` 的 4 个 `webhook_guard` 用例 | 根因 `CronEngine` 无 `_should_skip_webhook_generation`；全量基线记为 4 failed | 不计入本期回归结论；CI 以 `-k "not test_webhook_guard"` 排除；修复或删除由 G0/T0.2 决定。当前工作树是否仍复现【待确认 QC-03】 |
| pre-existing | `tests/test_run_ownership_security.py::test_tc_s3_owner_can_heartbeat_and_submit` | 后台守护线程向测试 DB 注入跟进任务，`claim` 抢到注入任务 → 读证门控 422（审计 F2） | G0 已按测试隔离处置；验收时单跑该文件确认 8 passed；不计入本期引入 |
| flaky | `tests/test_night_runner.py::test_spawn_and_reap` | 单跑必过；历史根因固定 `sleep(0.3)` 在机器有负载时等不到子进程退出 | 当前源码已改为 10 秒上限轮询 + `_reap_finished()`（`test_night_runner.py:47-59`）；全量出现偶发失败按单跑复核，不判为回归 |
| flaky | `tests/test_mcp_server.py` 个别用例 | 全量跑受跨文件顺序污染偶发失败，单跑稳定通过 | 单跑复核；不误判为回归；不修改用例顺序作为本期工作 |
| flaky（CI 已排除） | `test_two_agents_race` | CI `Run tests` 步骤显式 `-k "not test_two_agents_race"` | 维持排除；`README.md:772` 记该用例已用 QueuePool 修复、稳定性仍在观察 |
| 本期引入 | 无 | 本任务为方案编制，不修改源码、不新增产品功能、不新增测试用例文件 | 上线门禁要求「本期引入失败 = 0」；出现即按 P0/P1 阻断，见 §5 |

### 1.4 CI 接入方式（现状）

| 项 | 内容 |
|---|---|
| 工作流 | `.github/workflows/test.yml`，触发 `push` / `pull_request` 至 `main` / `master` |
| 运行器 | `windows-latest`，`matrix.python-version = ["3.12"]`，`actions/checkout@v4` 带 `fetch-depth: 0`（`test_version_consistency` 校验 tag） |
| 安装步 | `shell: bash`；`pip install -e ".[dev]" 2>/dev/null \|\| pip install -e .`；`pip install pytest`。`shell: bash` 使 `2>/dev/null` 不再被 PowerShell 解析成文件路径（G0 已修复审计 F1 的 `D:\dev\null` 失败） |
| `test` job 测试步 | `python -m pytest tests/ -v --tb=short -k "not test_two_agents_race and not test_webhook_guard"`；`env: MIO_TASKHUB_DB = ${{ runner.temp }}/test_taskhub.db` |
| `materials-gate` job | `python -m pytest tests/test_materials_gate.py tests/test_materials_validate.py tests/test_gate_attestation.py tests/test_release_entry.py -q`（4 个文件存在，已复核） |
| 失败产物 | `if: failure()` 上传 `.pytest_cache/` |
| 未接入项 | 无 `--cov`（覆盖率 80.0% 是本地 G0 基线，不是 CI 门禁）；无 `--basetemp`（runner.temp 每次干净，不触发本地 safe-delete 条件）；无 e2e job；无前端测试 job |
| 口径提示 | CI 绿依赖 `-k` 排除 4 个 `webhook_guard` 与 `test_two_agents_race`，**不等于全量 `pytest -q` 0 红**；G0 判据「全量 0 红」在当前工作树未由本次复核确认，属【待确认 QC-03】 |

### 1.5 测试金字塔与缺口

| 层 | 现状 | 缺口 | 本期处置 | 理由 |
|---|---|---|---|---|
| 单元 | `diff_gate.py` 纯函数、`doc_*` / `ratchet` / `read_evidence` / `planner` 等有单测；`test_run_diff_gate.py` 含 `test_differs_semantics` / `test_merge_annotations_dedupes_by_code` / `test_gate_mode_fallback_to_warn` / `test_snapshot_on_non_git_dir` | 文档 kind 三同步点（`DOC_KINDS` / `DOC_PATTERNS` / `KIND_META`）缺一致性测试；`lib/observatoryAdjacency.js` 自述可单测但无测试 | **不补** | 补一致性测试属工程修复，需另立任务；8 项冻结与「停投入」口径下本期不动 |
| 集成（TestClient） | 门控主链、文档状态门控、事件流、统计端点有集成用例；`test_run_diff_gate.py` 10 个 TC + 补充单元/只读自查；`test_diff_gate_stats.py` 5 用例 | 窄门禁三链路的**前端**自动化断言缺失；`submit_result` 前端无 `api.result()` 调用点 | **本期补**（以查库断言 + 人工界面核验替代自动化断言） | 拦截事件落库（AC-03）可用查库断言完全判定；前端引入框架属【待确认 QC-04】 |
| 门控冒烟（活体） | `.smoke_ws/gate_smoke.sh`：隔离库 `gate_smoke.db` + 48621 端口 + 独立 uvicorn + `trap cleanup EXIT`，11 项 PASS 断言，退出码 = 失败数 | 该脚本覆盖的是**阶段推进生命周期门控**，不是窄门禁 diff gate；未纳入发布 checklist、未接 CI | **待确认** | 是否纳入发布 checklist 与是否需要新增窄门禁活体冒烟属【待确认 QC-17】；窄门禁活体链路已由 §2.2 的验收步骤覆盖 |
| 前端 | 零框架、零测试文件；`npm run build` 可作构建产物绿证据 | 三链路可见性（红色 toast / 动态 tab 红行 / 完成报告红框卡）无自动化断言；`ApiError` 错误码呈现缺口 | **不补**（除非 QC-04 通过） | 用户明确「不引入前端测试框架（除非【待确认】通过）」；三链路用查库 + 留屏/人工观察验收 |
| 端到端 | `pytest -m e2e` 4 用例（真 uvicorn + 真 DB + 真 HTTP），默认 deselect | 未覆盖窄门禁 422/409 全链路；e2e 不在 CI | **不补** | 窄门禁活体验收由 §2.2 隔离实例承担；扩 e2e 属新增投入 |
| 覆盖率 | 本地 80.0% 基线，`coverage.xml` 按需产出 | CI 无覆盖率门禁、无阈值 | **不补** | 冻结 8 项含 Prometheus / SLO / tracing；接覆盖率门禁属新增 CI 投入，本期不做 |
| 性能 / 压测 | 无 | 无 | **不做** | 见 §3.3 与 §8.3 |

### 1.6 回归策略与冒烟集

- 全量回归命令（本地）：`MIO_TEST_DB_PATH=<独立路径> python -m pytest -q --basetemp=<独立目录> --tb=short`；失败名单落盘 `pytest --lf -q -rf > out.txt` 后再读，不在终端判定。
- 定点回归命令：`python -m pytest tests/test_run_diff_gate.py tests/test_diff_gate_stats.py -q --basetemp=<独立目录>`。
- e2e 回归命令：`python -m pytest -q -m e2e`。
- 冒烟集（发布前必跑，顺序固定）：① `pytest --collect-only -q`（收集数落盘）→ ② 定点窄门禁回归 → ③ 全量回归 → ④ `bash .smoke_ws/gate_smoke.sh`（隔离库 + 48621，11 项 PASS=11 / FAIL=0）→ ⑤ `curl -s http://127.0.0.1:48620/api/v1/board/diff-gate-stats`（只读统计可用）。
- 回归判定口径：只统计「本期引入」失败；pre-existing 与 flaky 按 §1.3 清单逐条排除并记录排除依据，不得用排除名单掩盖新增失败。
- 回归不做的事：不重跑 2026-10-10 审计基线、不换算 README 数字、不引入新测试运行器。

### 1.7 测试数据与环境策略

| 项 | 策略 |
|---|---|
| 测试库 | `MIO_TEST_DB_PATH` 指定独立路径；`tests/conftest.py` 写入 `MIO_TASKHUB_DB`；并行进程各用不同路径，否则 `PermissionError` |
| 活体实例 | 独立 uvicorn 端口 48621 + 空临时库，不碰 48620 常驻 hub 与 `~/.mio_taskhub/taskhub.db`；`bash .smoke_ws/gate_smoke.sh` 为既有范式 |
| git 场景 | 临时目录 `git init` + 一次 commit 造仓库，不在真实仓库 `E:/work/code/agent-dev/mio-taskhub` 上跑判定；`d468e633/test.md` 的真实仓库实测记录仅作历史证据，不重复执行 |
| 档位隔离 | 为只验窄门禁，前置 `MIO_READ_GATE=0` 关闭读证门控（或落齐 `required_reads` 的 ReadEvidence），避免读证 422 抢先返回干扰窄门禁判据；`MIO_DIFF_GATE` 按步骤切换 `off` / `warn` / `block` |
| 全量跑 | `--basetemp=<独立目录>` 必加；`--cov` 按需；覆盖率不进 `addopts` |
| 生产数据 | 只读。生产库行数为 2026-10-10 历史快照，非当前值；引用须注日期（事实包 §2、`phase2-data.md` §9 第 11 条） |

---

## 2. 功能测试用例

### 2.1 Must 用户故事覆盖（每个 Must 故事至少一条正向 + 一条异常用例）

优先级 P0 = 唯一投入项窄门禁与门控主链；P1 = 已交付核心能力回归；P2 = 查询/呈现类。用户故事编号取 `phase1-requirements.md` §3（US-1~US-11）与 §2.2（S2-a）。

| 用例ID | 对应用户故事 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|---|
| TC-US1-P | US-1（零改动提交收到 422） | `MIO_DIFF_GATE=block`；隔离库 + 48621；临时 git 仓库 workspace；已落读证或 `MIO_READ_GATE=0`；`run.diff_summary.before` 已采集 | 不做任何改动，`POST /api/v1/runs/{run_id}/result` 带 `{"success":true}` | 422；`detail.code=run.empty_diff`；`files_changed=0`；`rejections=1`；`max_rejections=2`；`Run.rejections` 加 1 | P0 |
| TC-US1-N | US-1 | 同上 | 提交 `success=true` 但 `run.state` 为 `finished` 且已成功交回 | 409 `run.result_already_submitted`，窄门禁不重复计数 | P0 |
| TC-US2-P | US-2（三档开关） | 同一零改动构造 | 分别置 `MIO_DIFF_GATE=off` / `warn` / `block` 各提交一次 | `off`→200 且 `annotations` 空；`warn`→200 且 `annotations` 含 `level=warn`；`block`→422 | P0 |
| TC-US2-N | US-2 | 同上 | 置 `MIO_DIFF_GATE=bogus` 与 `BLOCK` | 非法值按 `warn` 处理；`BLOCK` 按 `block` 处理（大小写不敏感） | P0 |
| TC-US3-P | US-3（只读统计端点） | hub 已启动，库内有 `diff_summary` 记录 | `GET /api/v1/board/diff-gate-stats` | 200；返回 `mode` / `by_reason` / `blocked_run_count` 等字段；M1 / M3 / M5 可算 | P1 |
| TC-US3-N | US-3 | 库内无任何 `run` 记录 | 同上 | 200；`scanned_runs=0`；不因空数据报错；调用前后 `run` 表记录无变化 | P1 |
| TC-US4-P | US-4（显式豁免） | `diff_gate_exempt=true` | 零改动提交 `success=true` | 200；`reason=task_exempt`；`annotations` 写明跳过原因 | P0 |
| TC-US4-N | US-4 | `workspace` 为空 / 非 git 目录 / `task_kind=idea_review` / 同 workspace ≥2 活跃 run | 分别零改动提交 | 四类均 200；`reason` 分别为 `no_workspace` / `collect_failed` / `task_kind_exempt` / `concurrent_run`；无一被拦 | P0 |
| TC-US5-P | US-5（claim 返回必读清单） | 任务有 `doc_paths`；`MIO_READ_GATE` 默认 | `POST /api/v1/tasks/claim?agent=<a>` | 200；响应含 `run_id` / `required_reads` / `required_fr` / `documents` 内联预览 / `read_evidence_required` | P1 |
| TC-US5-N | US-5 | 无可用候选任务 | 同上 | 204；不返回 `run_id`；不落 `Run` | P1 |
| TC-US6-P | US-6（读证指纹） | 已对每个 required kind 调 `taskhub_read_document` | `submit_result` | 200；`read-evidence` 端点 `passed=true` | P1 |
| TC-US6-N | US-6 | 未落 ReadEvidence，或落证后改文档 | 同上 | 422；`detail.missing` / `detail.stale` 非空；不进入窄门禁判定 | P1 |
| TC-US7-P | US-7（被拦后同 run 内改完再交） | `MIO_DIFF_GATE=block`；第 1 次 422 后 | 在工作区创建/修改一个文件（未 commit），再提交 | 200；`diff_summary.changed=true`；`reason=has_changes`；`attempt` 未增 | P0 |
| TC-US7-N | US-7 | 连续 3 次零改动提交 | 第 3 次提交 | 409 `run.empty_diff_repeated`；`rejections=3`；`exit_code=1`；`state=finished`；交既有重试机制 | P0 |
| TC-US8-P | US-8（修订指令） | 文档存在质量问题 | `GET /api/v1/tasks/{id}/doc/{kind}/revision` | 200；返回逐条问题 + 章节写作指引 + 模板片段 | P2 |
| TC-US8-N | US-8 | 文档无质量问题 | 同上 | 200；`revision_prompt=null` | P2 |
| TC-US9-P | US-9（自审 409） | reviewer = 最近执行者 | `POST /api/v1/tasks/{id}/reviews` 带 `decision=approve` | 409 `review.self_approval` | P1 |
| TC-US9-N | US-9 | 同上但带 `self_review=true`；或 approve 结论 <8 字符且无 review 文档 | 同上 | `self_review=true` 显式放行；结论字符不足时 422 | P1 |
| TC-US10-P | US-10（追溯矩阵） | 任务文档含 `FR-n` | `GET /api/v1/tasks/{id}/doc/quality` | 200；`traceability` 含 `fr_total` / `fr_covered` / `fr_uncovered` | P2 |
| TC-US10-N | US-10 | 文档无 `FR-n` 引用 | 同上 | 200；`fr_uncovered` 列出未覆盖项；不报 5xx | P2 |
| TC-US11-P | US-11（三处留痕一致） | 任务经历状态/阶段迁移 | 查 `TaskEvent` / `Event` / `doc_statuses` | 同一 task 三者一致；`Event.seq` 严格单调 | P1 |
| TC-US11-N | US-11 | 非法状态转移 | 调 `advance_stage` / `move_to_stage` 越级 | 被 `can_transition` 拒绝；`doc_statuses` 不写新态 | P1 |
| TC-S2a-P | S2-a（拦截对人可见） | `MIO_DIFF_GATE=block` 命中零改动 | 拦截后查库 + 看界面 | `Event(type=run.result_rejected, entity=task)` 有记录；`HistoryEvent(type=rejected)` 有记录；`Run.annotations` 含 `level=block`；红色 toast / 动态 tab 红行 / 完成报告红框卡齐全 | P0 |
| TC-S2a-N | S2-a | `MIO_DIFF_GATE=warn` 命中零改动 | 同上 | 200；`annotations` 含 `level=warn` 且文案含「不影响放行」；**不产生** `run.result_rejected` 事件、不弹 toast | P0 |

### 2.2 窄门禁专项验收：M2 拆成可执行验收步骤

唯一成功判据 M2（决策 §1 验收第 4 条）=「能指着一例它拦下了一次我本会放过的假完成」。M2 不是模型准确率指标，是**人工回看判定**，用「达标率」口径：样本 = 一个真实使用周期内 `rejections > 0` 的全部 run（M1），达标 = 其中经人工回看确认 ≥1 例「本会放过的假完成」（M2 ≥ 1），且 M3 = 0。样本量下限 = ≥3 个真实 run 完成（灰度 log「切 block 的前置条件」）。本节给出可复现的构造步骤；M2 的人工回看取证在 §7 灰度期执行。

#### 前置（所有步骤共用）

```bash
# Windows Git Bash；独立实例，不碰 48620 常驻 hub 与生产库
export ROOT="E:/work/code/agent-dev/mio-taskhub"
export MIO_TASKHUB_DB="$ROOT/.smoke_ws/qa_diff_gate.db"
export MIO_DIFF_GATE=block
export MIO_READ_GATE=0            # 关闭读证门控，隔离窄门禁判据
export PYTHONPATH="$ROOT"
"./.venv/Scripts/python.exe" -m uvicorn mio_taskhub.main:app \
  --host 127.0.0.1 --port 48621 --log-level warning &

# 临时 git 仓库（不在真实仓库上跑）
WS="$ROOT/.smoke_ws/qa_ws"; mkdir -p "$WS"; cd "$WS"
git init -q; git config user.email qa@t.com; git config user.name qa
echo base > README.md; git add .; git commit -qm init

BASE=http://127.0.0.1:48621/api/v1
TID=$(curl -s --noproxy '*' -X POST "$BASE/tasks" -H 'Content-Type: application/json' \
  -d "{\"title\":\"[qa] 假完成\",\"workspace\":\"$WS\"}" | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
RID=$(curl -s --noproxy '*' -X POST "$BASE/tasks/claim?agent=qa-probe&task_id=$TID" \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
```

#### 步骤与期望响应字段

| 步骤 | 动作 / 请求示例 | 期望响应字段 | 通过条件 |
|---|---|---|---|
| DG-1 构造零改动假完成 | `curl -s -i -X POST "$BASE/runs/$RID/result?agent=qa-probe" -H 'Content-Type: application/json' -d '{"success":true,"result":"已完成染色改造"}'` | HTTP 422；`detail` 为对象 | `detail.code=run.empty_diff`；`detail.message` 含「零改动」；`detail.hint` 含 `diff_gate_exempt`；`detail.files_changed=0`；`detail.rejections=1`；`detail.max_rejections=2`；`detail.message` 不含「数据校验失败」 |
| DG-2 run 状态不变 | 查库 `SELECT state,exit_code,finished_at,attempt,rejections FROM run WHERE id='$RID'` | — | `state` 仍为 `running`/`claimed`；`exit_code IS NULL`；`finished_at IS NULL`；`attempt` 未增；`rejections=1`；`task.state` 未流转 |
| DG-3 三链路可见 + 落库断言（AC-03） | `SELECT type,entity,run_id FROM event WHERE type='run.result_rejected'`；`SELECT type,payload FROM historyevent WHERE type='rejected'`；`SELECT annotations FROM run WHERE id='$RID'` | — | `Event` 命中且 `entity='task'`、`run_id=$RID`；`HistoryEvent` 命中且 `payload` 含「零改动」；`annotations` 含 `{code:'run.empty_diff',level:'block'}`。**三者缺一即判「抛 422 前未 db.commit()」，验收不通过** |
| DG-4 标注 reason | `GET $BASE/tasks/$TID`；`GET $BASE/runs/$RID/diff-evidence` | `runs[].diff_summary` / `annotations` / `rejections` | `diff_summary.reason='empty_diff'`；`diff_summary.mode='block'`；`diff_summary.changed=false`；`diff_summary.after` 只有 `{files:0,insertions:0,deletions:0}`；`changed`/`reason`/`mode` 在顶层不在 `after` 内；`rejections=1` |
| DG-5 第 3 次 409 | 重复 DG-1 请求共 3 次（均零改动） | 前 2 次 422，第 3 次 409 | 前 2 次 `detail.rejections` 依次为 1、2，且 `run.state` 不变、不消耗 `attempt`；第 3 次 `detail.code=run.empty_diff_repeated`、`detail.rejections=3`、`detail.max_rejections=2`；查库 `exit_code=1`、`state=finished`、`rejections=3`。源码判据为 `rejections > 2`（第 3 次），需求 FR-5 写「≥2」是文档差异 |
| DG-6 warn 模式不拦但标 annotations | `MIO_DIFF_GATE=warn` 重启实例；新建 task/run，重复零改动提交 | HTTP 200 | `annotations` 含 `{code:'empty_diff_warn',level:'warn'}`，`message` 含「不影响放行」；`diff_summary.reason='empty_diff_warn'`；`event` 表**无** `run.result_rejected`；`blocked_run_count` 不增加 |
| DG-7 off 模式全量放行 | `MIO_DIFF_GATE=off` 重启实例；新建 task/run，重复零改动提交 | HTTP 200 | `annotations` 为空；`diff_summary.reason='gate_off'`；不采集、不拦、不标注业务流程痕迹 |
| DG-8 各 fail-open 分支不误拦 | 分别构造：`workspace=''`、`diff_gate_exempt=true`、`task_kind=idea_review`、同 workspace ≥2 活跃 run、非 git 目录、`run.diff_summary.before` 缺失 | HTTP 200（全部） | `reason` 依次为 `no_workspace` / `task_exempt` / `task_kind_exempt` / `concurrent_run` / `collect_failed` / `no_before_snapshot`；`annotations` 写明原因；`collect_failed` 的 message 含「不影响放行」；`differs()` 在任一快照 `ok=False` 时返回 `True`（视为有改动，不拦） |
| DG-9 只读自查端点 | `GET $BASE/runs/$RID/diff-evidence` | 200 | 返回 `run_id` / `task_id` / `mode` / `would_block` / `skipped` / `reason` / `files_changed` / `before` / `after` / `changed`；与 `submit_result` 共用 `check_diff_gate`；**不改 run 任何字段**（`rejections=0`、`annotations=[]`、`state=running`）；`mode=warn` 时 `would_block=false` |
| DG-10 只读统计端点 | `GET $BASE/board/diff-gate-stats` | 200 | 返回字段逐键断言（见下）；调用前后 `run` 记录 `diff_summary` / `rejections` / `exit_code` 三元组不变；`blocked_run_count` 为 M1 代理、`suspected_false_positive_count` 为 M3 疑似信号、`by_reason` 为 M5 代理；端点不参与任何流程判定 |
| DG-11 迁移幂等 | `python scripts/verify_diff_gate_migration.py`（生产库副本 + 删列模拟） | 脚本输出 | 新列 `diff_summary` / `annotations` / `rejections` 存在；存量任务 `diff_gate_exempt=1`；二次迁移不报错、数据不变；迁移后新建任务默认 `diff_gate_exempt=0` |
| DG-12 M2 人工回看 | 灰度 log 记录本例上下文 + `SELECT ... FROM run WHERE rejections > 0` | — | 人工回看确认 ≥1 例「本会放过的假完成」；M3=0；M4 ≤4 秒；三条切 block 前置全勾。见 §7 |

DG-1 期望 `detail` 结构（`api/runs.py:511-519`）：

```json
{
  "code": "run.empty_diff",
  "message": "拒绝提交：声明 success=true，但本次 run 期间工作区零改动（0 文件 / 0 行）",
  "hint": "先在工作区实际完成改动（提交或保留改动均可）再 submit_result；若本任务确无代码产出，用 PATCH /tasks/{id} 设置 diff_gate_exempt=true。",
  "files_changed": 0,
  "rejections": 1,
  "max_rejections": 2
}
```

DG-10 `diff-gate-stats` 断言按源码实际键名逐键核对（`api/board.py:371-386`）：`mode` / `scanned_runs` / `judged_runs` / `unjudged_runs` / `by_reason` / `blocked_run_count` / `blocked_runs` / `warned_run_count` / `skipped_run_count` / `suspected_false_positive_count` / `suspected_false_positive` / `note`。交付底稿记为 13 字段，源码实测 12 个顶层键，差值 1 属【待确认 QC-02】；验收不按字段总数判定，按上列键名逐键断言。

#### 前端收尾缺口的验收断言（依赖前端工程师改动）

`web/src/api.js` 现状：`ApiError` 只读 `detail.error`（`:29`），拿不到 `detail.code`；`req()` 的 `msg` 未优先取 `detail.message`（`:43`）；`ERROR_MESSAGES[422]='数据校验失败，请检查必填项'`、`[409]='操作冲突，请刷新后重试'`（`:11`/`:10`）为通用文案。三链路渲染已落地：`App.jsx:269-270` 红色 sticky toast；`TaskDetail.jsx:60` `HIST_TYPE_META.rejected={label:'已拦截',tone:'danger'}`；`TaskDetail.jsx:497-524` `run-report__blocked` 红框卡与 `run-report__warn` 提示行。

| 断言ID | 对象 | 通过条件 |
|---|---|---|
| FE-1 | `ApiError.error_code` | 对 `detail={code:'run.empty_diff'}` 能读出 `run.empty_diff`；兼容原 `detail.error` |
| FE-2 | `req()` 错误消息 | 优先取 `detail.message`，不回落 `JSON.stringify(detail)` 整段 |
| FE-3 | 独立错误码文案 | `run.empty_diff` / `run.empty_diff_repeated` 各有专用文案，`ApiError.userMessage` 不含「数据校验失败」「操作冲突」；带出 `files_changed` / `rejections` / `max_rejections` 与「同 run 内改动后可再交、不消耗 attempt」 |
| FE-4 | `level=warn` 兜底 | 提示行显式含「不影响放行」，不依赖后端 `a.message`；`warn` 场景无 `.toast--danger` |
| FE-5 | 动态 tab 红行 | `rejected` 记录渲染为 danger 标签；是否要求整行红底/红边属【待确认 QC-11】 |

---

## 3. 非功能测试

### 3.1 非功能测试目标与方法

| 类型 | 目标 | 方法 / 工具 | 通过标准 |
|---|---|---|---|
| 性能 | 单次窄门禁判定新增 ≤4 次 git 子进程调用（before 2 次 + after 2 次）；`GIT_TIMEOUT_SEC=5`；端到端增量 P95 ≤300ms（`d468e633/requirement.md` 非功能需求，目标值） | 源码定点复核 `diff_gate.py:29/102/117` + 定点单测计时；**不跑压测/基准** | 判定路径不 spawn 超过 4 次 git；超时分支返回 `collect_failed` 并 200 放行；不设 QPS / P95 实测通过线（见 §3.3） |
| 兼容性 | 存量库可直接启动（迁移幂等、存量任务全置豁免）；`MIO_DIFF_GATE` 未设按 `warn`；干净 Windows 机零依赖首跑 | `scripts/verify_diff_gate_migration.py`；`gate_mode()` 单测；AC-17 / AC-18 干净机首跑 | 迁移可重复执行不报错、数据不变；非法档位按 warn；干净机 ≥3 台成功 |
| 安全 | 默认 loopback；单 Bearer token 仅 `/api/*`；`destructive` 默认拒；文档路径越界 400；只读 git 不写操作；`Snapshot.brief()` 不返回 `error` 绝对路径 | `tests/test_auth.py` / `test_security_defaults.py` / `test_mcp_risk_gate.py` / `test_frontend_xss.py`；非 loopback 启动拒绝检查 | 认证 401 `{"detail":"unauthorized"}`；越界 400；`diff_summary` / 响应体不含本地绝对路径与 diff 全文；窄门禁采集只读 `rev-parse` / `status --porcelain` / `diff --numstat` |
| 可靠性 | `/healthz` 存活、`/readyz` `SELECT 1` 异常 503 且带 `backups`；WAL `busy_timeout=5000`；广播失败静默不冒泡；fail-open 判据 | 定点回归 + §3.2 五项 | 广播异常不反向影响已提交事务；采集失败一律 200 放行；`/readyz` 503 体 `{status:degraded,db:error}` |

### 3.2 回归验收（5 项）

| # | 项 | 现状与已知缺陷 | 验收方法 | 通过标准 |
|---|---|---|---|---|
| R-1 | SQLite 并发写 | WAL + QueuePool（`pool_size=5` / `max_overflow=10`）+ `busy_timeout=5000` + `synchronous=NORMAL`；`atomic_claim()` 条件更新 `WHERE state=QUEUED`，`rowcount!=1` 回滚 | `pytest -m e2e`（4 用例，真 uvicorn + 真 DB）；并发 claim 定点用例；`10×100 并发零重复 / 100% 成功率` 为主理人给定口径，原始报告路径与测量日期属【待确认 QC-15】 | 零重复 run；`atomic_claim` 竞争失败返回 None 不产生第二个 run；不启用多进程写者（触发条件未成立） |
| R-2 | JSON 列读-改-写丢更新（`doc_statuses` / `annotations`） | `set_doc_status`（`task_documents.py:588/660-669`）：`statuses = dict(t.doc_statuses)` 读 → 改 → 整列写回，无 CAS / 无锁 / 无版本字段；两个并发请求各自基于旧副本写回，后写覆盖先写，**两次都返回 200 假成功**。`_apply_diff_gate`（`runs.py:421-430`）、`check_ratchet`、`record_read` 同模式；`merge_annotations` 按 `code` 去重 | 单写者串行路径回归；并发两请求对同一 task 不同 kind 的定点复现（结果作为**已知缺陷证据**留存） | 单用户单写者路径通过；并发丢更新如实登记进「已知限制清单」，**不作为本期回归失败、也不判为通过**；出现真实多写者并发命中时按 P0 上修。本期不修（8 项冻结 + 唯一投入项 = 窄门禁） |
| R-3 | WebSocket 广播 `seq` 单调 | `Event.id` 自增主键 = 单调 `seq`；`after_flush` 收集 + `after_commit` 逐事件广播；增量订阅 `GET /events?after_seq=N` 返回 `id>N` 升序 + `next_seq` | 注入 N 条事件后查 `SELECT id FROM event ORDER BY id`；断连后按 `after_seq` 补齐；广播异常注入 | `seq` 严格单调、无空洞、无重复；增量订阅补齐无遗漏；`_broadcast_after_commit` 逐事件 try/except，异常不冒泡出 `db.commit()`（否则调用方重复提交） |
| R-4 | 备份恢复 | `ops/backup.py` 用 SQLite Online Backup API 每小时 1 份，`keep_hourly=24` + `keep_daily=7` = 31 份；`get_backup_status()` 给 `/readyz` 回显 `{backup_dir,count,latest,total_bytes}`；**源码未实现 restore** | 断言备份份数 ≤31、`latest` 非空、`/readyz` 200/503 语义；恢复演练按人工步骤（停 hub → 处理 `-wal`/`-shm` → 替换 `taskhub.db` → 重启 → `SELECT ... FROM event ORDER BY id` 校验水位） | 备份侧可判定；**恢复流程无脚本、无验收命令，属【待确认 QC-07】**，验收状态记为「未验收 / 运维手册待补」，不得记为通过 |
| R-5 | 干净机首跑 | G3 判据：干净 Windows 机 ≥3 台零依赖首跑；Quickstart ≤5 分钟计时 n≥5；同 commit 可复现构建 + SHA256 | `Get-FileHash dist/mio-taskhub-win64.zip -Algorithm SHA256`；解压双击 + `register→claim→submit` 闭环；`packaging/build.ps1` + `scripts/check_installer_bundle.py` → `[OK]` | ≥3 台成功并记录踩坑；≤5 分钟（n≥5）；两次构建产物 SHA256 可对比（允许差异说明）；changelog 与版本一致。机器来源与取证格式属【待确认 QC-18】 |

### 3.3 明确排除的非功能验证（见 §8.3）

| 排除项 | 排除理由 |
|---|---|
| 性能压测 / 基准（QPS、P95 实测通过线、并发容量曲线） | 用户明确「不做性能压测/基准」；SLO 目标值（HTTP 99.9% / 任务 99.0% / MCP 99.5% / P50<100ms / P99<2000ms / 5xx<0.1%）为目标非实测且该域已冻结 |
| Prometheus / SLO / tracing 告警验证 | 8 项冻结之一（FZ-3）；`observability/` 端点保留但停投入；`/metrics` 文本端点为冻结保留内容，不作现行验收指标 |
| 多租户 / 权限体系测试 | 「本地单用户、零联网、零外部依赖」明确非目标；无角色表、无隔离测试 |
| 前端测试框架引入 | 除非【待确认 QC-04】通过 |
| AI 幻觉率 / Prompt 回归评测 | 门控链路无 LLM 调用，见 §4 |

---

## 4. AI 专项测试（如适用）

**判定：不适用。**

| 依据 | 内容 |
|---|---|
| 门控链路无大模型调用 | `submit_result` 的判定由确定性代码完成：`assert_run_owner` → `assert_run_submittable` → `check_read_gate`（sha256 指纹）→ `_apply_diff_gate`（git 只读子进程）→ `completion_gate.check_submit`。`test` 只是一种文档 kind，不是测试运行器；系统不跑测试、不做代码静态分析（事实包 §4） |
| 完成声明的处理方式 | Agent 自报 `success: bool` 被直接采信，由服务端 `diff_gate.py` 的空 diff 判据兜住，不改造为模型判定 |
| 冻结范围 | `memory gateway` / observer 机会发现 / creativity 调度已冻结或改默认关；不新增评测集、不接 Prompt 回归 |
| 唯一带主观判定的指标 | M2「本会放过的假完成」由人工回看判定，不是模型输出评测。口径：样本 = 观察周期内 `rejections > 0` 的全部 run（M1），达标 = 人工确认 M2 ≥ 1 例且 M3 = 0，样本量下限 ≥3 个真实 run。**用达标率与样本量口径，不写「通过/不通过」** |
| 若未来引入 LLM 判定 | 需先补超时、重试上限、降级到结构完整性校验、单次/单任务成本上限四项后才可进主链路（`phase1-architecture.md` §5）；届时另立评测集，本期不设计 |

因此不输出准确率 / 幻觉率 / 合规拦截率指标，不设计 Prompt 回归集与流式中断恢复用例，不设成本波动监控。

---

## 5. 缺陷分级与门禁

### 5.1 缺陷分级（修复时限绑定，前提：单人全职 + Agent 辅助、熟悉本仓库）

| 级别 | 定义 | 修复时限 | 放行影响 |
|---|---|---|---|
| P0 | 数据丢失 / 状态不可信 / 不可恢复；窄门禁误拦合法完成导致任务推不动；拦截事件未落库导致 M2 无法举证；拦截改变 `run.state` 或消耗 `attempt`；采集失败产生 422 | 发现即停发布，≤1 人日 | 阻断放行；先 `MIO_DIFF_GATE=off` 回退（不改代码、不需迁移），再定位修复 |
| P1 | 核心链路功能错误：状态机非法转移、ReadEvidence 指纹门控失效、三链路缺一、`MAX_REJECTIONS` 阈值错误、`diff-evidence` / `diff-gate-stats` 非只读 | ≤3 人日 | 阻断放行；修复后重跑 §1.6 冒烟集 |
| P2 | 非核心缺陷：前端错误码呈现、warn 文案兜底、动态 tab 红行样式、只读统计字段缺失、文档行号/口径不一致 | ≤5 人日 | 有条件放行：登记进「已知限制清单」并经复核；不阻断但需在发布 checklist 留痕 |
| P3 | 文档 / 注释 / 命名差异（如 `collect()` 命名、需求「≥2」与源码「>2」、`after` 字段结构、README 数字漂移） | 下一文档修订窗口 | 不阻断放行；不由代码改动收口 |

分类纪律：pre-existing 与 flaky 按 §1.3 单独登记，不套用上表时限；「本期引入」失败一律按 P0/P1 处理。缺陷分级必须绑定修复时限，未绑定时限的分级不进入放行判定。

### 5.2 上线放行门禁（Go / No-Go）

**Go（全部满足）**

1. §6.2 中「当前可验收」的 AC-01~AC-11 全部有证据且通过；AC-23~AC-30 中归属 G4 的项全部勾选。
2. 本期引入失败 = 0；pre-existing 4 例与 flaky 清单逐条登记并注明排除依据（§1.3）。
3. 无未关闭的 P0 / P1 缺陷。
4. `MIO_DIFF_GATE` 当前档位与灰度 log 记录一致；切 `block` 的三条前置全勾（≥3 真实 run、M3=0、M2≥1），否则维持 `warn`。
5. 对外材料通过口径冻结检查：事实包 §4 G4 禁用词清单不出现；文档门控只称「结构完整性校验」与「服务端强制」；V1 证据不足不写成质量结论，D2-b 暂缓不写成待发布功能。
6. 发布 checklist 全绿：CI `success`、`LICENSE`(MIT) 入库、`gen_readme_stats.py --check` 通过、同 commit 可复现构建 + SHA256、changelog、已知限制清单与文档同步。
   **W-10 实测（2026-11-08，`093869b`）**：上条为放行条件，当前**未满足**——`release_checklist.py --ci --json` 8 项 = 7「是」+ ⑤「部分」，`all_green=false` / EXIT=1；确定阻塞为 ⑤ 外部 3 台干净机取证未补齐与 W-9 同 commit 可复现构建未关闭。CI `success`、LICENSE、`--check` 三项已满足，同 commit 构建证据仍基于 v0.6.0 / `d916a20`。
   **2026-11-09 范围裁决**：作者确认无外部取证条件，当前不做对外分发，上条放行条件整体转条件形态；`all_green=false` 保持原值，G4 记为「不适用（本机自用）」而非通过。见主方案 §0.3 裁决段。
7. 冒烟集（§1.6）五项全部执行且证据落盘。

**No-Go（任一命中）**

- 出现任一未关闭 P0 / P1；出现任一「本期引入」失败。
- M3 > 0 且未执行 `MIO_DIFF_GATE=off` / `warn` 回退。
- 对外材料出现禁用词，或把「未成立的结论」写成已验证结论。
- AC-03 查库断言未命中（拦截事件随会话回滚）。
- 依赖回滚代码或数据迁移才能放行（回退有效性失效）。

---

## 6. 验收清单

### 6.1 Gate 判据表（G0–G4）

来源：`docs/roadmap/05-execution-plan.md` §2–§6 与事实包 §4。**Star / Trending / fork / 转发数不得作为任何 Gate 的进入条件、退出条件或证据**，仅作分发观测（周增 ≥15 持续 2 周 / ≥3 独立来源 / ≥3 外部闭环使用者 / ≥5 外部 Issue-PR）。

| Gate | 进入条件 | 退出条件 | 证据 | 否决项 | 当前结论状态 |
|---|---|---|---|---|---|
| G0 证据链修复与审计通过 | 冻结基线 `g0_manifest.json` 可用 | CI 绿且 `Run tests` 实际执行；全量 `pytest -q` 0 红（例外列明）；`coverage.xml` 基线；`docs/roadmap/01–05` 入库；Agent 生命周期埋点落库 + `reaper_decision` n≥30 且三分可判 | `gh run list` / `gh run view --log`；pytest 输出；`coverage.xml`；`git check-ignore`；`g0_manifest.json` | CI 或测试不能做到 0 红（连续 2 轮修复仍红）；埋点阻塞业务；reaper 连续 4 周凑不满 n≥30；出现确证 P0 | 事实包 §2 记 G0 已修复（CI 全绿、覆盖率 80.0%、40 份关键文档入库、F1/F2/F4/F5/F6/F7 清零）；当前工作树 CI 绿依赖 `-k` 排除，全量 0 红未由本次复核确认【待确认 QC-03】 |
| G1 实验可运行 | G0 通过 | `04-experiment-design.md` 评审冻结（H1–H8 / S1–S7 / 三态判据 / 样本量）；harness 有单测且绿；档位探针双绿（T 422 / C 200）；同 seed 二元结局 100% 一致；P3/P4 通过或明确 defer；P5 决策留痕 | `run_m.py --help` / `summarize.py --help` / `pytest tests/test_experiment_harness.py`；探针输出；审批单据 | 同 seed 二跑不一致；档位探针无法双绿；发现必须新增产品功能才能继续实验 | 实验 harness 相关测试文件存在（`test_experiment_harness.py` 等）；本方案不重判 G1 结论 |
| G2 实验结论成立 | G1 通过 | M 系列 H1–H6 各得三态、H7/H8 报估计值；D1 有三分根因与三态结论；V1 / V2 得三态或明确「证据不足」；`report.md` 数字可回溯 raw jsonl；`01` / `03` 已回写并留 diff | `experiments/runs/<dir>/summary/report.md`；回写 diff；三态结论记录 | 任一主假设「不支持」且触及架构假设；V1 无法达 n≥30/组仍宣称效应；D1 首启 n≥100 后仍 <70% | **V1 = 证据不足**（n=1 ≪ 30，B 系列 defer，真实 agent 不可用）；**V2 = 观测支持、非对照**（`quality_strata.py` 真库 api Fisher p=0.037，观察性关联非 A/B 因果）；**D1 部分成立**（首启 n=234、81.9%，落在 70–90% 档，未达 90%；reaper n=444，真死 27.7% / 链路断 70.9% / 观测不足 1.4%）；**E4 部分成立**（相对原约 62% 基线有变化但未达 90%）；**链路断 70.9% 为主因且落在项目外**；按 G2 分支裁决：**暂缓 D2-b**、定位维持「个人/小团队效率工具」、G3 只做 D1 诊断可读化 + 开源前置 |
| G3 产品化完成 | G2 结论已出 | 本阶段每一项新增/固化都能对应 G2 一条结论；D1 固化或明确「根因在项目外→仅诊断」；`LICENSE`(MIT) 入库 + README 由脚本生成且 CI 校验一致；干净 Windows 机 ≥3 台 + Quickstart ≤5 分钟（n≥5）；能力地图无「接线未用」 | `git ls-files LICENSE`；`gen_readme_stats.py --check`；干净机计时记录；能力地图 | D2-b 6 个月 0 外部接入；D3 产出 3 个月 0 消费；零依赖首跑在 ≥3 台中失败率高；出现确证 P0 | 按现行裁决只保留 D1 诊断可读化 + 开源前置；D2-b / D3 为条件性、未触发不做；交付形态【待确认 QC-09】 |
| G4 发布准备完成 | G3 通过 | 发布门 checklist 全绿；同 commit 可复现构建 + SHA256 + changelog；**口径冻结**（禁用词在结论出来前不得出现） | CI run；`git ls-files LICENSE`；`Get-FileHash`；计时记录；changelog；能力地图 | 任一发布门项不达标；实验结论为「不支持/证据不足」却被要求作效应/首创宣称 | **当前不成立**：① M2 唯一成功判据未成立（灰度 log 起止为 `____-__-__`、M1~M5 全部「待观察」、2026-10-21 记「尚未产生真实拦截」），状态口径为「代码已落地、灰度观察未成立」；② **W-10 冻结基线 `093869b` 实测发布门 `all_green=false`（8 项 = 7「是」+ ⑤「部分」）**，确定阻塞为外部 3 台干净机取证与 W-9 同 commit 可复现构建；**2026-11-09 裁决**：当前不做对外分发，G4 记为「不适用（本机自用）」，`all_green=false` 保持，判据原文不删改 |

**明确不成立的结论（不得写成已验证）**：V1 门控效应；V2 的 A/B 因果；D1「成立」（仅部分成立）；E4「达标」（未达 90%）；D2-b 可启动；M2 已举证；SLO 达成率（目标值非实测且该域已冻结）；门控位置唯一性（`docs/roadmap/03` §2.4 检索为推断档，「检索未发现 ≠ 不存在」，V1 证据不足，不得写成已验证结论）。

### 6.2 AC-01–AC-22 可勾选验收表

每条判据均为可判定条件（字段 / 状态码 / 计数 / 命令输出），不写「功能正常」类表述。归属阶段：`G0` = 基线；`当前` = 已交付能力可立即复现；`灰度期` = 需先满足前置；`G3` / `G4` = 发布前置；`条件性` = FR-C 触发前不验收。

| 勾 | 编号 | 判据 | 验证方式 | 通过条件 | 归属阶段 |
|---|---|---|---|---|---|
| [ ] | AC-01 | 全量测试与基线 | `pytest -q`（`addopts=-m 'not e2e'`）；`pytest --cov=mio_taskhub --cov-report=term-missing --cov-report=xml -q`；全量加 `--basetemp=<独立目录>` | 输出行含 `0` 个本期引入 failed；4 个 e2e 被 deselect；`coverage.xml` 产出；4 个 pre-existing 与 flaky 按 §1.3 登记排除；CI `success` 且 `Run tests` 实际执行 | G0 |
| [ ] | AC-02 | 窄门禁单测层 | `pytest tests/test_run_diff_gate.py tests/test_diff_gate_stats.py -q --basetemp=<独立目录>` | 全绿；`MIO_DIFF_GATE=block` 下「success + 零改动」→ 422 且 `detail.code=run.empty_diff`；10 个 TC 与 5 个统计用例均有结果 | 当前 |
| [ ] | AC-03 | 拦截事件确实落库 | 查库 `SELECT * FROM event WHERE type='run.result_rejected' AND entity='task'`；`SELECT * FROM historyevent WHERE type='rejected'`；`SELECT annotations,rejections,diff_summary FROM run WHERE id=<rid>` | 三处均命中且 `run_id` 一致；证明 `api/runs.py:478` 的 `db.commit()` 已执行；仅看界面不算通过 | 当前 |
| [ ] | AC-04 | 只读统计端点可用 | `curl -s http://127.0.0.1:48620/api/v1/board/diff-gate-stats \| python -m json.tool` | 200；按 §2.2 DG-10 的键名逐键存在；端点不参与任何流程判定；调用前后 `run` 三元组不变 | 当前 |
| [ ] | AC-05 | 三档开关语义 | 分别以 `MIO_DIFF_GATE=off/warn/block` 启动隔离实例，构造零改动 `success=true` run | `off`→200 且 `annotations` 空；`warn`→200 且 `annotations` 含 `level=warn`；`block`→422 `run.empty_diff` | 当前 |
| [ ] | AC-06 | 误伤边界（豁免 / fail-open） | 构造：非 git 目录、`diff_gate_exempt=true`、`task_kind=idea_review`、同 workspace ≥2 活跃 run、`workspace` 空、`before` 缺失 | 六类均 200；`run.diff_summary.reason` 分别为 `collect_failed` / `task_exempt` / `task_kind_exempt` / `concurrent_run` / `no_workspace` / `no_before_snapshot`；无一 422 | 当前 |
| [ ] | AC-07 | 拦截可见性 | 被拦后查 `Event` / `HistoryEvent(type=rejected)` / `Run.annotations(level=block)`，并观察动态 tab 与 toast | 三链路齐全；`level=warn` 只出现在完成报告提示行、不弹 toast、文案含「不影响放行」；红行口径【待确认 QC-11】 | 当前 |
| [ ] | AC-08 | ReadEvidence 指纹门控 | 构造未落 ReadEvidence 或改文档后再 `submit_result` | 缺失 / 指纹不符 → 422 且 `detail.missing` / `detail.stale` 非空；落齐 → 200 | 当前 |
| [ ] | AC-09 | 结构完整性校验口径 | `GET /api/v1/tasks/{id}/doc/quality`；`GET /api/v1/tasks/{id}/doc/{kind}/revision` | 返回 `errors` / `warns` / `score` 与 `traceability`；`score=max(0,100-20*errors-5*warns)`；对外文案只称「结构完整性校验」，不含内容质量宣称 | 当前 |
| [ ] | AC-10 | 状态机 / 阶段 / 追溯一致性 | `GET /api/v1/tasks/{id}/doc/statuses`；`GET /api/v1/tasks/graph`（`has_cycle`/`cycle_path`）；`GET /api/v1/stages/requirements`（`lifecycle_gate`） | 非法转移被 `can_transition` 拒绝；`Event.seq` 严格单调、`after_seq` 增量订阅可补齐 | 当前 |
| [ ] | AC-11 | 版本与规模口径一致 | 读 `mio_taskhub/version.py` / `pyproject.toml`；`python scripts/gen_readme_stats.py --check` | 版本号 `0.6.1`；CI 校验 README 统计一致；不引用 README 手工数字作基线；数字注口径与取证日；口径差异见 QC-01 | 当前 / G3 |
| [ ] | AC-12 | 唯一成功判据 M2 ≥ 1 | 灰度 log M2 人工回看记录 + `SELECT ... FROM run WHERE rejections > 0` | 前置：① 一个真实使用周期且起止已填；② ≥3 个真实 run 完成；③ 已切 `MIO_DIFF_GATE=block`。产出 ≥1 例「本会放过的假完成」被 422 拦下的上下文记录 | 灰度期 |
| [ ] | AC-13 | 零误伤 M3 = 0 | 灰度 log M3 + 全量回看被拦 run | 合法完成误拒数 = 0；`suspected_false_positive_count` 人工复核后为 0；否则按 AC-16 回退 | 灰度期 |
| [ ] | AC-14 | 切 block 前置（FR-9） | 灰度 log「切 block 的前置条件」三勾选 | ≥3 真实 run、M3=0、M2≥1 三条全勾才切 `block`；缺一维持 `warn` | 灰度期 |
| [ ] | AC-15 | M4 拦截可见性 4 秒内 | 每次拦截的动态 tab 红行 + 红色 toast 计时记录 | ≤4 秒；`EmbedView` 是否计入口径【待确认 QC-12】 | 灰度期 |
| [ ] | AC-16 | 误伤回退有效性 | `MIO_DIFF_GATE=off`（或 `warn`）+ 复现原用例 | 不改代码、不回滚迁移，拦截消失且 `annotations` 不再含 `level=block` | 灰度期 |
| [ ] | AC-17 | G3：干净 Windows 机零依赖首跑 ≥3 台 | `Get-FileHash mio-taskhub-win64.zip -Algorithm SHA256`；解压双击 `TaskHub.exe` + `setup-agent.bat`；3 台踩坑记录 | ≥3 台成功完成 `register→claim→submit` 闭环；机器来源与取证格式【待确认 QC-18】 | G3 |
| [ ] | AC-18 | G3：Quickstart ≤5 分钟 | 陌生用户 `register→claim→submit` 计时记录 n≥5 | 计时样本 ≥5 且 ≤5 分钟 | G3 |
| [ ] | AC-19 | G3：LICENSE(MIT) 与 README 单一事实源 | `git ls-files LICENSE`；CI job 校验 `gen_readme_stats.py` 输出一致 | LICENSE 入库；CI 绿；README 手工数字不再作为基线 | G3 |
| [ ] | AC-20 | G4：同 commit 可复现构建 | `powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1` 同 commit 跑两次，比对产物 SHA256 + changelog | 两次 SHA256 一致（或有差异说明）；changelog 齐；`scripts/check_installer_bundle.py` 返回 `[OK]` | G4 |
| [ ] | AC-21 | V1 门控效应结论（FR-C01） | A/B 报告（当前 n=1、B 系列 defer、真实 agent 不可用） | 前置未满足前**不验收**，也不得对外宣称门控效应；V1 维持「证据不足」 | 条件性 |
| [ ] | AC-22 | D2-b 协议化 / SLO 解冻 / 冻结项解冻 | 复审记录（FR-C02 / C04 / C05 触发条件） | 未触发即不验收；8 项冻结不解冻；D2-b 暂缓不写成待开发功能 | 条件性 |

### 6.3 补齐的发布前置与灰度观察验收项（AC-23–AC-30）

| 勾 | 编号 | 判据 | 验证方式 | 通过条件 | 归属阶段 |
|---|---|---|---|---|---|
| [ ] | AC-23 | 口径冻结 | 对外材料全文扫描事实包 §4 G4 禁用词清单 | 禁用词 0 命中；文档门控只称「结构完整性校验」与「服务端强制」；未成立结论不写成已验证 | G4 |
| [ ] | AC-24 | 已知限制清单与文档同步 | 核对能力地图与已知限制清单 | 无「接线未用」条目；JSON 列 RMW 丢更新、备份 restore 缺口、前端零测试框架、pre-existing 4 例与 flaky 清单逐条登记 | G4 |
| [ ] | AC-25 | changelog / 版本号 / tag | `git tag -l`；读 `CHANGELOG.md` 与 `version.py` | 版本号单调递增；tag 指向 G3 后提交；changelog 含本周期改动（含实验结论摘要且不含禁用词） | G4 |
| [ ] | AC-26 | 前端鉴权联动按部署形态可用（主方案 §5.2） | 默认形态：不启用 `--auth`，token 为空；`pytest tests/test_frontend_auth_wiring.py -q` 5 passed；四条通道行为与改造前一致；`npm run build` 通过。条件形态（非环回 / 多机访问）不在当前 G4 判据内 | 默认形态判据全绿并附证据链接；同 commit 可复现构建 + SHA256 齐。**W-10 冻结 commit `093869b` 上默认形态已复验通过**：`test_frontend_auth_wiring.py` 5 passed、`test_auth.py` 9 passed、`npm run build` ✓ 2.84s；同 commit 可复现构建仍缺（W-9 未关闭） | G4 |
| [ ] | AC-27 | 灰度 M1~M5 记录 | 读 `docs/taskhub/diff-gate-grayscale-log.md` | 起止日期已填（不再为 `____-__-__`）；M1~M5 每格有值或明确「待观察」及原因；观察期事件记录表有条目 | 灰度期 |
| [ ] | AC-28 | 切 block 三前置全勾 | 灰度 log「切 block 的前置条件」 | ≥3 真实 run、M3=0、M2≥1 三条全勾；当前档位与记录一致【待确认 QC-05】 | 灰度期 |
| [ ] | AC-29 | 误伤回退演练 | 触发 M3>0 场景后置 `MIO_DIFF_GATE=off` / `warn` 并复现 | 拦截消失；不需回滚代码、不需迁移、不需改数据；回退方向固定为仅标注/关闭，**不加严** | 灰度期 |
| [ ] | AC-30 | 回归与非功能 5 项证据齐 | §3.2 的 R-1~R-5 逐项证据文件 | R-1/R-2/R-3 有输出与结论；R-4 恢复缺口标注为未验收并列入已知限制；R-5 干净机与 Quickstart 证据齐 | G3 / G4 |

### 6.4 分类可勾选总表

- [ ] 功能验收：AC-01~AC-11、AC-23~AC-30 中归属「当前 / G4」的项全部勾选；AC-03 查库断言命中。
- [ ] 性能：不做压测/基准；仅核对窄门禁 ≤4 次 git 调用与 `GIT_TIMEOUT_SEC=5` 超时 fail-open 分支。
- [ ] 安全：认证 401 / 越界 400 / destructive 默认拒 / SSRF 拒绝 / 响应体不含绝对路径与 diff 全文，逐项有结论。
- [ ] 兼容性：迁移幂等 + 存量豁免；非法 `MIO_DIFF_GATE` 按 warn；干净 Windows 机 ≥3 台。
- [ ] 文档与交付物：`phase3-qa.md` 落盘；AC-01~AC-30 每条有证据位；已知限制清单、changelog、LICENSE、README 统计脚本一致。
- [ ] 运维就绪：`/healthz` 200、`/readyz` 200（含 `backups.count` / `latest`）；7 守护线程 `ThreadRegistry.stop_all()` 反向关停；备份份数 ≤31；恢复缺口已登记。

---

## 7. 灰度期观测与回滚判定

观察载体：`docs/taskhub/diff-gate-grayscale-log.md`（指标 M1~M5 + 回退条件 + 切 block 前置）。当前记录：2026-10-21 窄门禁落地、默认 `warn`、备注「尚未产生真实拦截」，M1~M5 全部「待观察」。

| 观测项 | 正常范围 | 回滚触发条件 | 回退动作 |
|---|---|---|---|
| M1 零改动提交被拦次数（`blocked_run_count`） | `block` 档下 ≥1 | 一个真实周期跑满仍为 0 | 不属回滚；不切 `block`、维持 `warn`，继续观察 |
| M2 本会放过的假完成（人工回看） | ≥1 例确认 | 0 例 | 唯一成功判据未成立；禁止对外宣称门控效应；口径冻结，维持 `warn` |
| M3 合法完成被误拒次数 | = 0 | > 0 | 立即 `MIO_DIFF_GATE=off` 全量放行，或 `warn` 只标注不拦；**不加严** |
| M4 拦截可见性 | 动态 tab 红行 + 红色 toast ≤4 秒 | 超时或三链路缺一 | 按 P1 处理；先回退 `warn`，修复后重跑 AC-07 |
| M5 跳过原因分布（`by_reason`） | `collect_failed` 不占多数 | 大量 `collect_failed` | 排查 git 采集与环境，不加严判据、不改「只在明确假完成上拦截」总原则 |
| `suspected_false_positive_count` | = 0（人工复核后） | > 0 且人工确认误拒 | 同 M3 回退 |
| `MIO_DIFF_GATE` 当前档位 | 默认 `warn` | 三条前置未全勾即被切 `block` | 回退 `warn` |
| 回退有效性（AC-16 / AC-29） | 改 env 后拦截消失 | 需改代码或迁移才能放行 | 判 P0，阻断发布 |
| CI / 全量回归 | 本期引入失败 = 0 | 出现本期引入失败 | 按 §5.1 P0/P1 阻断 |

**切 `block` 前置（FR-9，三条全勾才切）**：① 至少 3 个真实 run 完成；② M3 = 0；③ M2 ≥ 1 例确认拦到过假完成。当前三条均未满足，维持 `warn`。回退操作不需回滚代码或数据：`MIO_DIFF_GATE=off` 完全关闭（不采集、不拦、不标注），`warn` 只标注不拦。

---

## 8. 风险与待确认

### 8.1 风险清单

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| 口径失真：README 数字、用例数、字段数、阈值文档差异（QC-01/02） | 高 | 验收断言按错数字写，判据不可判定 | 全部数字注口径与取证日；断言按实际命令输出与源码键名写；不换算 |
| 拦截事件未落库（`db.commit()` 缺失） | 中 | M2 无法举证 | AC-03 查库断言为硬门；缺任一表即不通过 |
| 窄门禁误伤合法完成 | 中 | 任务推不动 | `off`/`warn` 立即回退且不加严；M3=0 才允许切 `block` |
| JSON 列 RMW 丢更新被误判为回归或误判为通过 | 中 | 已知缺陷被掩盖 | §3.2 R-2 明确「不作为本期回归失败、也不判为通过」，登记进已知限制清单 |
| 前端三链路无自动化断言 | 高 | AC-07 / AC-15 靠人工观察，证据强度低 | 查库断言兜底 + 留屏/人工核验；是否补框架【待确认 QC-04】 |
| 全量跑被 safe-delete 保护杀掉 teardown | 中 | 拿不到汇总行 | `--basetemp=<独立目录>` 必加；失败名单落盘再读 |
| 并行跑 `PermissionError` | 中 | 回归中断 | `MIO_TEST_DB_PATH` 按进程隔离 |
| `test_spinning_guard` 4 例当前是否仍复现不明 | 中 | CI 绿与全量 0 红口径混淆 | 【待确认 QC-03】；发布前重跑一次全量并落盘汇总行 |
| 灰度观察周期样本自限（外部使用者 0） | 高 | M2 无法举证，唯一成功判据判不了 | 一个真实周期 + ≥3 真实 run；不达标维持 `warn`，不强行切 `block` |
| 冻结项被擅自重启 / 范围扩大 | 中 | 违背「做减法」现行最高约束 | 8 项冻结清单 + 复审条件 4 条；`MioDigestJob` / `ContractJob` 默认仍开不擅自纳入冻结 |
| 备份恢复无脚本 | 中 | 灾难恢复无验收依据 | 列为已知限制；运维手册待补【待确认 QC-07】 |

### 8.2 【待确认】清单（共 18 项，逐条给缺口与取证方向）

| 编号 | 待确认项 | 缺口 | 取证方向 |
|---|---|---|---|
| QC-01 | 用例数口径 | README 顶部 1601（事实包）vs 当前工作树 1609 vs 全量基线 1235 passed vs `--collect-only` 1904/1908 collected；另 README 目录树 1305、pyproject 注释 1113 | `scripts/gen_readme_stats.py --check`；固定 commit SHA 与取证日后重跑 `pytest --collect-only -q` 与全量，落盘汇总行 |
| QC-02 | `diff-gate-stats` 字段数 | 交付底稿记 13 字段，源码 `api/board.py:371-386` 实测 12 个顶层键 | 与后端工程师确认第 13 键名或更正底稿；验收按 12 键逐键断言 |
| QC-03 | 4 个 pre-existing 失败当前是否复现 | `tests/test_spinning_guard.py:6-9` 文件头记「4 例已删除」；CI 仍 `-k "not test_webhook_guard"`；本次未跑全量 | 发布前 `pytest -q --basetemp=<独立目录>` 一次并落盘汇总行；确认 G0「全量 0 红」判据 |
| QC-04 | 前端测试框架与三链路自动化断言 | 是否引入 vitest / @testing-library / Playwright；是否纳入 G3/G4 发布 checklist；验收证据形式（留屏 vs 自动断言） | 主理人裁决；`web/package.json` scripts 与 `devDependencies` |
| QC-05 | 灰度观察周期起止与当前档位 | `diff-gate-grayscale-log.md` 起止仍 `____-__-__`、M1~M5 待观察 | 灰度 log 记录表 + 当前 `MIO_DIFF_GATE` 实际值；是否已有真实拦截 |
| QC-06 | 代码类 `task_kind` 完整清单 | `TaskKind` 实测 3 值（`normal` / `change_tracking` / `idea_review`），`EXEMPT_TASK_KINDS` 豁免后 2 值；扩 kind 时同步责任未定 | `mio_taskhub/models.py` `TaskKind`、`diff_gate.py:41` |
| QC-07 | 备份恢复流程 | `ops/backup.py` 无 restore 实现；WAL 伴生文件处理、恢复后 `Event.seq` 水位校验命令未定义 | `ops/backup.py`；运维手册待补；恢复演练证据 |
| QC-08 | G3/G4 具体日历排期 | 决策 §6 明确「不排日历，具体排期由作者定」 | 主理人裁决；本方案只给工时区间（附录 A） |
| QC-09 | D1 诊断可读化交付形态 | 字段名 / 入口 / 呈现位置未定 | 事实包 §4 G3 判据；`phase1-architecture.md` §6 决策 4 |
| QC-10 | 窄门禁独立错误码 UI 承载边界 | Web UI 无 `api.result()` 调用点；是仅落 `api.js`/`ApiError` 层还是需新增提交入口 | `phase2-frontend.md` §5.3 / §9 第 2 条 |
| QC-11 | 动态 tab「红行」验收口径 | 现状 `chip--danger` 红色标签，`HistRow` 根节点无 danger 修饰类 | AC-07 是否要求整行红底/红边；`phase2-frontend.md` §9 第 6 条 |
| QC-12 | `EmbedView` 是否计入 M4 | `/#/embed` 独立 WS、`onmessage` 只调 `load()`、不弹 toast、不渲染完整 `TaskDetail` | `phase2-frontend.md` §9 第 11 条 |
| QC-13 | WS 认证连通性 | `App.jsx:247` / `EmbedView` 未带 `?token=` 或 Bearer；`--auth` 时能否连上 `/ws`（失败 `close(4401)`） | `main.py::websocket_endpoint`；前端 token 注入点 |
| QC-14 | 统一错误体是否收敛单一 schema | 4 类门控 422 `detail` 结构各异；401 / 429 亦不同 | `phase2-backend.md` §9；本期不动（8 项冻结含停投入） |
| QC-15 | claim P50/P95 与并发零重复原始证据 | 主理人给定 30.5ms / 32.5ms 与 10×100 并发零重复，原始报告路径与测量日期未逐条列出 | `docs/roadmap/02-current-state-audit.md`；并发实验报告 |
| QC-16 | P1-1 / P1-2 完成度 | 区分 `cancelled`/`failed`、删 `self_review` / 禁空 reviewer 的当前状态未给 | 决策 §4 验收项；`api/reviews.py` / `submit_review` 语义 |
| QC-17 | 门控冒烟是否纳入发布 checklist / CI | `.smoke_ws/gate_smoke.sh` 11 项覆盖阶段推进 LIFECYCLE_GATE，非窄门禁 diff gate；未接 CI | 主理人裁决；是否新增窄门禁活体冒烟脚本 |
| QC-18 | 干净机 ≥3 台与 Quickstart n≥5 取证格式 | 机器来源、计时记录格式、踩坑记录模板未定 | G3 判据；`build-release-checklist.md`；`phase1-requirements.md` AC-17/AC-18 |

### 8.3 明确「不做」清单

| # | 不做项 | 口径 |
|---|---|---|
| 1 | 不引入前端测试框架（vitest / Playwright / @testing-library） | **除非【待确认 QC-04】通过**；本期三链路用查库断言 + 留屏/人工观察验收，前端零测试框架为现状 |
| 2 | 不做性能压测 / 基准 | 不设 QPS 目标、不测 P95 实测通过线、不跑并发容量曲线；SLO 目标值为目标非实测且该域已冻结 |
| 3 | 不做 Prometheus / SLO 告警验证 | 8 项冻结之一（FZ-3）；`observability/` 端点保留、停投入、不作现行验收指标 |
| 4 | 不做多租户 / 团队权限 / RBAC 测试 | 「本地单用户、零联网、零外部依赖」明确非目标；无角色表、无隔离测试 |
| 5 | 不做 AI 幻觉率 / Prompt 回归 / 流式中断 / 成本波动评测 | 门控链路无 LLM 调用，AI 专项不适用（§4） |
| 6 | 不跑全量测试作为本方案编制动作 | 已知 pre-existing 失败；如需取证只用只读文件与 `--collect-only` |
| 7 | 不新增虚构功能、不重设计测试运行器 | 唯一投入项为窄门禁 `MIO_DIFF_GATE=off\|warn\|block`（代码已落地、灰度观察未成立）；冻结 8 项不写成待开发功能 |
| 8 | 不修 JSON 列 RMW 丢更新、不补文档 kind 三同步点一致性测试、不接覆盖率 CI 门禁 | 触发条件（多写者并发 / 第二个真实用户 / 新增 CI 投入）当前未成立，登记延后 |

---

## 附录 A · 测试侧任务卡与依赖

**人力假设**：单人全职 + Agent 辅助、熟悉本仓库，含设计 / 实现 / 测试 / 取证；不含外部评审等待、审批墙钟、分发运营。工期给**区间（人日）**，不给日历日期。灰度观察取证与干净机协调为**墙钟依赖**，不计入纯工时。纯工时合计约 **6.5–12 人日**。任务卡字段沿用 `docs/roadmap/05-execution-plan.md` §11 模板（标题 / 前置依赖 / 涉及模块 / 验收命令 / DoD / 风险 / 成本 / 上游映射）；每条 DoD 绑定一条可复现命令。

| # | 标题 | 前置依赖 | 涉及模块 | 验收命令 | 完成定义（DoD） | 风险 | 成本 | 上游映射 |
|---|---|---|---|---|---|---|---|---|
| QA-T1 | 全量基线复跑与口径收口 | 无 | `pyproject.toml`、`tests/`、`README.md`、`.github/workflows/test.yml` | `pytest --collect-only -q`；`pytest -q --basetemp=<独立目录> --tb=short` | 基线文件落盘，含 commit SHA、取证日、收集数、汇总行、4 个 pre-existing 与 flaky 排除依据；QC-01 / QC-03 有结论或明确未决 | safe-delete 保护杀 teardown；`PermissionError` | 0.5–1 人日 | F1/F2/F4/F5、G0、AC-01 |
| QA-T2 | 窄门禁专项验收脚本 | QA-T1 | `.smoke_ws/`、`mio_taskhub/api/runs.py`、`diff_gate.py` | `bash .smoke_ws/qa_diff_gate.sh`（按 §2.2 前置与 DG-1~DG-11 实现，隔离库 + 48621） | 脚本退出码 = 失败数；逐项打印 PASS/FAIL；DG-1~DG-11 每步有落盘的响应体或查库结果 | 读证 422 抢先返回；真实仓库被误当 workspace | 1–2 人日 | FR-B01~B07、TC-1~TC-10、AC-02/05/06/08 |
| QA-T3 | 拦截事件落库断言 | QA-T2 | `Event` / `HistoryEvent` / `Run.annotations` | `SELECT type,entity,run_id FROM event WHERE type='run.result_rejected'` 及配套两条查库 | 三处均命中且 `run_id` 一致；证明 `db.commit()` 时序；AC-03 勾选 | 未显式 commit 导致事件回滚 | 0.5 人日 | 坑位①、AC-03、M2 |
| QA-T4 | 前端收尾验收 | QA-T2；依赖前端工程师的 `api.js` / `App.jsx` / `TaskDetail.jsx` 改动 | `web/src/api.js`、`ErrorBar.jsx`、`TaskDetail.jsx` | FE-1~FE-5 断言（§2.2）；`cd web && npm run build` | `detail.code` 可读、`detail.message` 优先、两个 code 不回落通用 422/409 文案；`level=warn` 显式「不影响放行」且无 toast；构建绿 | 前端改动未交付则本卡阻塞；QC-10/11 未决 | 0.5–1 人日 | F1~F7、FR-B05、AC-07 |
| QA-T5 | 门控冒烟纳入发布清单 | QA-T1；依赖 devops 工程师 CI/发布方案（任务 #7） | `.smoke_ws/gate_smoke.sh`、发布 checklist | `bash .smoke_ws/gate_smoke.sh` | 11 项 PASS=11 / FAIL=0、退出码 0、证据落盘；是否入 CI 由 QC-17 裁决并留痕 | 脚本覆盖的是阶段门控而非窄门禁，易被误记为窄门禁覆盖 | 0.5 人日 | LIFECYCLE_GATE、G4 发布门 |
| QA-T6 | 回归与非功能验收 | QA-T1；依赖 devops 环境 | `db.py`、`events.py`、`api/task_documents.py`、`ops/backup.py`、`packaging/` | §3.2 R-1~R-5 各自命令 | 每项有证据文件；R-2 丢更新登记进已知限制清单；R-4 恢复缺口明确标「未验收」 | 把已知缺陷误判为回归失败或误判为通过 | 1–2 人日 | F11/F13/F16/F17、AC-30 |
| QA-T7 | AC 清单冻结与 Gate 判据对齐 | QA-T1~T6 | `phase1-requirements.md`、`phase3-qa.md`、`05-execution-plan.md` | 逐条核对 AC-01~AC-30 的证据位；Star/Trending 全文检索 | AC-01~AC-30 每条可判定、有证据位、有归属阶段；Gate 表无 Star/Trending；未成立结论清单已列 | 判据写成「功能正常」类不可判定表述 | 0.5–1 人日 | AC-01~AC-22、G0–G4 |
| QA-T8 | 灰度观察取证 | 窄门禁上线；作者定观察周期起止 | `diff-gate-grayscale-log.md`、`api/board/diff-gate-stats` | `curl -s .../board/diff-gate-stats \| python -m json.tool`；`SELECT ... FROM run WHERE rejections > 0` | 起止已填；M1~M5 有值；M2 人工回看记录 ≥1 例；M3=0；M4 ≤4 秒；切 block 三前置状态明确 | 样本自限、外部使用者 0；墙钟依赖不可压缩 | **墙钟依赖，不计纯工时** | M1~M5、AC-12~AC-16、AC-27~AC-29 |
| QA-T9 | 缺陷分级与放行门禁落地 | QA-T7 | `phase3-qa.md` §5、发布 checklist | Go/No-Go 逐条勾选并留痕 | 无未关闭 P0/P1；本期引入失败=0；口径冻结检查通过；判定记录落盘 | 用排除名单掩盖新增失败 | 0.5 人日 | G4 发布门、Go/No-Go |
| QA-T10 | 发布前置与干净机验收 | QA-T7；依赖 devops 可复现构建与干净机 ≥3 台 | `packaging/build.ps1`、`scripts/check_installer_bundle.py`、`LICENSE`、`CHANGELOG.md` | `Get-FileHash dist/mio-taskhub-win64.zip -Algorithm SHA256`；`gen_readme_stats.py --check` | AC-17~AC-20、AC-25/26 证据齐；两次构建 SHA256 可对比；changelog 齐 | 干净机不可控、杀软/端口占用；QC-18 未决 | 1–2 人日 | G3/G4、M3/M4 |
| QA-T11 | 文档与交付物验收 | QA-T7 | `phase3-qa.md`、已知限制清单、能力地图 | 逐项核对 §6.4 六类可勾选总表 | 六类全部勾选；已知限制清单含 JSON RMW、restore 缺口、前端零测试框架、pre-existing 与 flaky；禁用词 0 命中 | 对外材料出现禁用词或未成立结论 | 0.5 人日 | G4 口径冻结、AC-23/24 |

**关键路径**：QA-T1 → QA-T2 → QA-T3 → QA-T4（前端改动）→ QA-T7 → QA-T9 → QA-T10。QA-T5 / QA-T6 / QA-T11 可与 QA-T4 并行；QA-T8 为墙钟依赖，不进关键路径但决定 M2 能否成立。
**跨成员依赖**：QA-T4 依赖前端体验工程师（任务 #5）的 `api.js` 收尾改动；QA-T5 / QA-T10 依赖部署运维工程师（任务 #7）的 CI 与可复现构建方案；§0.3 的五处源码判据需后端服务工程师（任务 #6）确认码位口径。

---

（本章交付完毕。未修改任何源码，未跑全量测试，未新增虚构功能，未把冻结项写成待开发功能。所有数字标注口径与取证日；不确定处共 18 项，逐条标【待确认】并给取证方向。）
