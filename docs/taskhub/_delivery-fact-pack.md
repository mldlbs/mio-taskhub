# 项目事实包 · mio-taskhub（取证日 2026-11-06）

> 用途：交付方案编制的唯一事实底稿。所有成员以此为准，需要更细的码位时自行读源码。
> 取证来源：`README.md`(v0.6.1)、`pyproject.toml`、`AGENTS.md`、`docs/roadmap/01~05`、`docs/taskhub/frozen-scope-20261021.md`、`docs/taskhub/product-decision-20261020.md`、`docs/quality-audit.md`、`docs/taskhub/grab-mode/`、`mio_taskhub/` 目录结构、`web/src/` 目录结构。

---

## 1. 定位与边界

- **本地单用户、零联网、零外部依赖的跨 Agent 任务协调中枢**。人建任务、看进度；多个 AI 编程助手（opencode / claude code / codex / WorkBuddy 等）通过 MCP 或 HTTP 注册上线、自动领活、上报进度、交回结果。
- 三痛点：① 谁在干什么 ② 干到哪了 ③ 干完的产出在哪。
- **辨识度（推断，非已验证创新）**：服务端强制的「文档链门控 + ReadEvidence 内容指纹」把多 Agent 协作变成可追溯事实；门控位于 `submit_result` 必经路径（缺失/指纹不符即 422），而非 Agent 自觉或工具侧建议。对照 spec-kit 的 checklist gate 由 Agent 自执行、mcp-approvals 自述「不在网络路径上、拦不住跳过」。
- **明确非目标**：云端托管/多租户/团队权限、通用多 Agent 框架、与 vibe-kanban 正面拼 diff 评审与 worktree 合并、用 LLM 替代确定性状态机、Evidence→Insight 自喂养闭环、继续扩功能面。
- **2026-10-20/21 产品决策（现行最高约束）**：项目问题不是「发展空间」而是「**过度建设**」。总原则——「会在相当一部分合法完成上触发的检查，只能警告/标注；只在明确的失败或撒谎上触发的检查，才配拦截」。**8 项冻结**：creativity 调度、observer 机会发现、Prometheus/SLO/tracing、memory gateway、ADR 投影、idea 子系统扩张、超出结构校验的文档质量阈值、多入口合并（本轮不做）。**唯一保留投入项 = 窄门禁（拦「假完成」）**。
  - 灰度开关 `MIO_DIFF_GATE=off|warn|block`（默认 `warn`）；配套只读端点 `GET /api/v1/board/diff-gate-stats`（纯统计，不参与流程判定）。
  - 唯一成功判据：**「能指着一例它拦下了一次我本会放过的假完成」**；观察记录 `docs/taskhub/diff-gate-grayscale-log.md`（指标 M1~M5 + 回退条件 + 切 block 前置）。
  - 冻结 = 代码不删、接口不删、文档保留；停投入、停调度、停当价值宣称、移出主链路。已改默认关：`MIO_OBSERVER_AUTOSTART`、`MIO_IDEA_REVIEW_ENABLED`。
  - 未纳入冻结（勿擅自扩大范围）：`MioDigestJob`、`ContractJob` 默认仍开。
  - 复审条件：① 窄门禁频繁误伤合法完成 → 回退为仅标注（**不加严**）；② 作者能演示某冻结项真的省了具体时间 → 解冻；③ 发现运行失败主因是口径失真 → 可靠性判断上修；④ 出现第二个真实用户 → 重估 SLO/tracing。

## 2. 代码与规模

| 维度 | 现状（README v0.6.1） |
|---|---|
| 版本 | `0.6.1`（`pyproject.toml` / `version.py`） |
| 后端 | Python ≥3.10 · FastAPI + SQLModel / SQLite(WAL) · 76 模块 / 33,383 行 |
| 对外能力 | **176 REST 端点** · **56 MCP 工具** · WebSocket 实时广播 · React Web UI |
| 任务文档 | **22 类 kind** · **7 件套链** · **9 类生命周期状态机** · 质量 lint + 状态门控 + 修订指令 |
| 前端 | React 18 + Vite 5 · 49 文件 / 12,532 行 · 10+ 视图 |
| 测试 | **1601 用例**（55 文件 / 8,480 行）· GitHub Actions（windows-latest / py3.12） |
| 部署 | pip 源码 · Docker 多阶段 · Windows 单 EXE 绿色版（免 Python）· Windows 安装包（图形安装 + 正规卸载） |

- **历史口径警告**：2026-10-10 的 `docs/roadmap/02` 曾实测当时的 README 数字全面漂移（0.5.5 / 169 路由 / 47 MCP / 1269 用例 / CI 53 次全红 / 覆盖率 0）。**G0 阶段已修复**：CI 全绿、覆盖率 80.0%、40 份关键文档入库、F1/F2/F4/F5/F6/F7 清零；P2/P3（F8 otel 未生效、F9 night_runner、F10 告警无闭环、F11 限流单桶、F12 异常吞、F13 pre-push fail-open、F16 质量分不可全量回溯、F17 取消原因不落库）登记延后。引用数字须注明口径与日期。
- **依赖钉死**（`pyproject.toml`）：`fastapi==0.141.1` / `starlette==1.6.0` / `uvicorn[standard]==0.52.4` / `sqlmodel==0.0.42` / `SQLAlchemy==2.0.52` / `apscheduler==3.11.3` / `pydantic==2.13.5` / `websockets>=12.0` / `mcp==1.29.1` / `croniter==6.2.4` / `python-multipart>=0.0.9` / `opentelemetry-*` ≥1.25。钉死原因：CI 全新装到 sqlmodel 0.0.48 / SQLAlchemy 2.1.4 后 32 个测试因 naive datetime 校验收紧挂掉。
- dev：`pytest>=8.0` / `httpx>=0.27` / `pytest-cov>=5.0`；`addopts = "-m 'not e2e'"`（默认排除 4 个 e2e）；覆盖率按需跑 `--cov=mio_taskhub --cov-report=term-missing --cov-report=xml`，不进 addopts。

## 3. 领域模型与架构

- **三条正交维度**：
  1. 执行状态机 `queued→claimed→running→completed / retrying / failed / cancelled`（`TaskState.can_transition` 单点校验，非法转移拒绝）；`blocked_failed` 为历史遗留不可达态。
  2. 7 段研发阶段 `brainstorming→design→planning→ready→implementing→review→done`（`api/task_stages.py::STAGE_ARTIFACT_REQUIREMENTS` 门控；`design` 另要求至少一条 discussion）。
  3. 22 类文档 kind + 9 类生命周期状态机（严格向前、不许回退/跳级、终态不可改判）。
- **主要实体**：`Task` / `Run` / `Agent` / `TaskEvent` / `Event`（单调 `seq`，增量订阅）/ `Subtask` / `GitRef` / `HistoryEvent` / `TaskReview` / `Idea` / `IdeaScore` / `Discussion` / `DiscussionMessage` / `TaskTemplate(+Version)` / `ScheduledJob` / `ReadEvidence` / `RatchetBaseline`。文档路径与状态是 Task 上的 JSON 列 `doc_paths` / `doc_statuses`（相对路径，读写经 `_resolve_within_workspace()` 越界校验）。
- **后端核心模块**：`main.py`（装配 / lifespan / CLI）、`models.py`（唯一权威模型 + 枚举）、`db.py`（WAL + QueuePool）、`migrations.py`、`mcp_server.py`（MCP 工具工厂；God File ~643 行）、`middleware.py`（RequestID + 限流，默认 120 req/min，**key 仅 client IP，同机多 Agent 共用一个桶**）、`events.py`（全局事件流 + WS 广播）、`background.py`（守护线程 + `ThreadRegistry` 反向优雅关停；God File ~580 行）、`dependency.py`（依赖满足 = `state==completed` 或 `stage==done`）、`composite.py`、`planner.py`（`detect_cycle` 环检测 + 空闲计划）、`doc_paths.py`（22 kind 唯一事实源）、`doc_chain.py`（7 件套骨架）、`doc_lifecycle.py`、`doc_quality.py`（`score = max(0, 100 - 20*errors - 5*warns)`；`requirement/spec/api` 阈值 **80**）、`ratchet.py`（质量分/用例数只升不降，回退 422）、`read_evidence.py`（内容指纹 sha256）、`diff_gate.py`（**窄门禁，唯一投入项**）、`hierarchy.py` / `decomposition.py` / `next_action.py` / `policy_guard.py` / `mcp_risk.py`（destructive 默认拒）/ `net_guard.py`（SSRF）/ `role_prompts.py` / `payload_layout.py` / `release_assets.py`。
- **分包**：`api/`（30+ 路由模块）、`workflow/`、`scheduling/`（cron_engine / scheduler / night_runner / capacity / gate / auto_triage）、`ideas/`、`ops/`（backup / git_sync / data_fixes）、`observability/`（metrics / alerts / dep_metrics / otel / logging_config / report / insights / remediation / slo_history / task_trace / ws_push）、`update/`（自更新 manifest / runtime / downloader / service / apply）、`installer/`（安装/卸载子系统：install / registry / shortcuts / processes / gui / payload / embedded）、`materials/`。
- **前端** `web/src/`：`App.jsx` + `main.jsx` + `api.js` + `stageDocs.js` + `docLifecycle.js` + `cpm.js`（关键路径）+ `confirm.js` / `promptInput.js` / `noise.js` / `constants.js`；`components/` 40+ 组件：工作流泳道、列表、拓扑 DAG、甘特 CPM、想法、模板、定时、统计、观测台、记忆（观测+网关双 tab 单入口）、Mio 运行时、素材面板、文档面板 `DocPanel.jsx`、评审面板/队列、依赖图、命令面板、任务详情抽屉、嵌入视图 `/#/embed`、连接横幅、错误边界、高对比模式。WebSocket 驱动实时刷新，看板支持拖拽改状态/阶段。
- **四重质量闭环**：
  1. 写时 lint（反馈不阻断）：`doc_quality.check_content`，必备章节 / 表格行数 / 空壳占位 / 分点书写 → `score`。
  2. 状态门控（阻断）：推进 `review`/`approved`/`done` 要求 `errors==0` 且分数 ≥80；`force=true` 可绕过但 emit 事件留痕。
  3. 修订指令反哺：`GET /doc/{kind}/revision` 直接给「逐条问题 + 章节写作指引 + 模板片段」，Agent 拿到就能改。
  4. 配套：`traceability()` 做 `FR-n`↔`TC-n` 追溯；**ReadEvidence 必读**（`claim` 返回 `run_id` + `required_reads` 默认 spec/api/requirement + `required_fr` + 内联预览，预览≠已读；`submit_result` 校验指纹，缺失/变更 422）；**棘轮基线**（`GET /tasks/{id}/ratchet`）；**pre-push 钩子**（校验 spec/api/plan approved + diff 引用 FR-n 真实存在，但 fail-open，路径安装时固化）。
- `api`（接口契约）质量规格全链最严：10 必需 H2 + 11 建议 H2 + `detail_rule`（每个 `### <Method> <Path>` 必须含 请求参数/响应字段/错误码/示例 四个 `####` 子块）；四键 `sections` / `recommended` / `min_table_rows` / `detail_rule`。
- **安全基线**：默认 loopback、单 Bearer token（仅 `/api/*`，Web UI 与 `/docs` 免认证）、`net_guard` SSRF、`mcp_risk` destructive 默认拒、DOMPurify。
- **已知限制**：单用户无多租户与权限体系；SQLite 并发上限（规模增长需迁 Postgres）；`mcp_server.py` / `background.py` God File 残留；文档质量 lint 是**启发式结构校验，判断不了内容是否真的对**；文档 kind 三处同步点（`doc_paths.DOC_KINDS` / `api/task_documents.DOC_PATTERNS` / `web/src/components/DocPanel.jsx` 的 `KIND_META`）缺一致性测试；Windows 优先。
- **运维**：7 个守护线程（heartbeat / scheduler / idea-review / backup / git-sync / night-runner / cron-engine）；SQLite Online Backup API 每小时、保留 31 份；`/healthz` `/readyz` `/metrics` `/dashboard` `/api/v1/alerts` `/api/v1/logs`；SLO 目标（HTTP 可用性 99.9%、任务成功率 99.0%、MCP 99.5%、P50<100ms/P99<2000ms、5xx<0.1%）为**目标值非实测**，且该域已冻结。
- **打包分发**：`packaging/build.ps1`（`npm run build` → PyInstaller 主程序 → PyInstaller 安装器/卸载器 → 拷 setup 脚本/说明/WorkBuddy skill/widget → `scripts/make_zip.py` 确定性 zip → 组装安装包目录）；统一入口 `packaging/run.py` 按 `sys.argv[1]` 分派 hub / mcp / widget；无黑框模式 stdout 为 None 需重定向；zip 不能用 `Compress-Archive`（只读 base_library.zip 报错 + 布局不稳 + 不可复现）；`.ps1` 必须 UTF-8 with BOM；`payload_layout.py` 统一布局契约；排除 torch/pandas/scipy；目标 Windows x64。产物：`dist/mio-taskhub-v3/`、`dist/mio-taskhub-win64.zip`、`dist/mio-taskhub-installer/mio-taskhub-setup.exe`（单文件自解压，装/卸同一个 exe）。用户数据 `%USERPROFILE%\.mio_taskhub`，卸载默认保留。

## 4. 路线与排期基线（`docs/roadmap/05-execution-plan.md`，2026-10-10 制定）

- **五阶段五门**：`G0 证据链修复与审计通过 → G1 实验可运行 → G2 实验结论成立 → G3 产品化完成 → G4 发布准备完成`。**功能增量的唯一开关在 G2**；G0–G2 只做修复、观测脚手架、实验与结论，不新增产品功能。
- **各门判据**：
  - G0：CI 绿（`Run tests` 实际执行）+ 全量 `pytest -q` 0 红 + `coverage.xml` 基线 + `docs/roadmap/01–05` 入库（白名单解绑 `.gitignore:31 docs/`）+ Agent 生命周期埋点（`agent_lifecycle.*` 事件、本地 jsonl 真相源、幂等 ingest、`heartbeat_sent_count`）+ `reaper_decision` n≥30 且三分可判 + `g0_manifest.json` 冻结基线。
  - G1：`04-experiment-design.md` 评审冻结（H1–H8 / S1–S7 / 三态判据 / 样本量）+ 实验 harness 就绪并有单测 + 档位探针双绿（T 422 / C 200）+ 同 seed 二元结局 100% 一致 + P3（破坏性操作审批）/ P4（B 系列 agent 额度审批）/ P5（质量门 env 开关决策）留痕 + 首启样本管道通。
  - G2：M 系列（H1–H6 三态、H7/H8 估计值）+ B 系列（V1，n≥30/组，4 周封顶）+ P 系列（V2，观察性 Fisher，缺失分数敏感性）+ D1 首启成功率与三分根因，各有「支持 / 不支持 / 证据不足」三态结论 + 回写 `01`/`03`。
  - G3：**结论驱动**（每项新增对应 G2 一条结论）+ D1 固化 + `LICENSE`(MIT) 入库 + README 统计由 `scripts/gen_readme_stats.py` 生成且 CI 校验 + 干净 Windows 机 ≥3 台零依赖首跑 + Quickstart ≤5 分钟（计时 n≥5）+ 能力地图无「接线未用」。
  - G4：发布门 checklist 全绿 + 同 commit 可复现构建 + SHA256 + changelog + **口径冻结**（禁用词：「提升质量」「形成闭环」「生产级可靠」「首创/突破」，在结论出来前不得出现）。
- **成本口径**：单人全职 + Agent 辅助、熟悉本仓库、含设计/实现/测试/取证；不含外部评审等待、审批墙钟、分发运营。基线 ≈**14–21 人周（3.5–5 个月）**；D2-b/D3 条件性 +6–12 周。墙钟依赖：reaper 样本 2–4 周、B 系列 4 周封顶、D1 生产样本 2–4 周。分阶段纯工时：G0 3–4 周 / G1 3–4 周 / G2 4–6 周 / G3 3–5 周 / G4 1–2 周。
- **硬纪律**：Star / Trending / fork / 转发数**不得**作为任何 Gate 判据，仅作分发观测（周增 ≥15 持续 2 周 / ≥3 独立来源 / ≥3 外部闭环使用者 / ≥5 外部 Issue-PR）。
- **任务卡字段**（§11 可直接转 `taskhub_create_task`）：`标题 / 前置依赖(depends_on) / 涉及模块(files) / 验收命令 / 完成定义(DoD) / 风险 / 成本 / 上游映射`；**DoD 必须绑定一条可复现命令**，无命令的条目不得创建；条件性任务打 `labels += ["conditional"]`，前置结论未出不得进 `ready`。
- **三个候选技术方向**（`docs/roadmap/03`）：
  - **D1 可判定的执行生命周期**（优先，1–2 周）：自适应失败检测 + 两级证据 + Detect→Classify→Recover→Verify；是 D2/D3 的实验前置，**是入场券不是突破**。
  - **D2 门控效应的可证伪验证 + 协议化**（唯一候选突破）：D2-a A/B 2–3 周，D2-b 协议化 3–6 周（条件性，仅 D2-a 通过且 ≥1 非作者接入才启动）。
  - **D3 可验证完成回执**（3–6 周）：结构化 bundle + 哈希链 + 离线校验器；**依赖真实消费方**（当前 0），概念已不新颖（≥4 个同向公开项目）。
- **G2 实测回写（2026-10-14，现行结论）**：
  - V1「门控+ReadEvidence 改变 Agent 行为」= **证据不足**（S2 单元 T 拦 422 / C 放 200 方向正确，但 n=1 ≪ 30；B 系列 defer，真实 agent 不可用）。
  - V2「质量门+棘轮提升文档质量」= **观测支持、非对照**（`quality_strata.py` 真库观测 api Fisher p=0.037；93 scored / 2 低于 80；观察性关联非 A/B 因果）。
  - V3/E3 根因 = **部分成立**：首启 n=234、成功率 **81.9%**（70–90% 档 → D1 **部分成立**）；reaper n=444；三分根因 真死 27.7% / **链路断 70.9%（项目外因素）** / 观测不足 1.4%；「非误回收」获数据支持。
  - E4 生命周期修复有效 = **部分成立**（81.9%，相对原 ~62% 基线提升显著，但**未达 90%**）。
  - **按 G2 分支的现行裁决**：D1 部分成立且根因落在项目外 → 定位维持「个人/小团队效率工具」；G3 只做 D1 诊断可读化 + 开源前置，**暂缓 D2-b**；V1 证据不足 → **不启动 D2-b、禁止宣称「提升质量」**。
- **三处质量能力硬缺口（2026-10-10 源码实证，回答「质量提升」类问题必查）**：
  1. **无变更识别**：全仓无 `git diff` / `changed_files` 逻辑 → Run 无法绑定「实际改了什么」。（窄门禁 `diff_gate.py` 正是补这一条的唯一投入项）
  2. **无测试执行**：全仓无 pytest runner；`test` 只是一种**文档 kind**，不是运行器。
  3. **评审无身份隔离**：`submit_review` 的 `reviewer` 是自由字符串，同一 Agent 可自己 approve（已加缓解：reviewer 为最近执行者时 approve 报 409，`self_review=true` 可显式放行；approve 结论 ≥8 字符，有 review 文档时豁免）。
  - `submit_result` 成功路径只校验：① Run 所有权 ② 状态可提交 ③ ReadEvidence 版本一致 → 随后自动 `completed→review`；**Agent 自报的 `success: bool` 被直接采信**。读证指纹**只覆盖被读文档，不覆盖代码**。
  - 另注：mio-taskhub **不做代码静态分析、也不跑测试**，它管的是「研发过程质量」。

## 5. 抢单模式（grab mode，任务 `3fa41beb`）

- 文档链已齐：`docs/taskhub/grab-mode/` 下 `requirement / architecture / spec / data-model / api / test / runbook / decision` 全套。
- FR 裁剪为 5 条：`grab_mode` 字段 / 只读大厅 / 复用 claim / 派单让位 / 能力匹配沿用。
- 现状根因：`background.py` 派单先于 claim → 抢不到东西。
- A 批 9 项：A-1 字段迁移 → A-2 `GET /tasks/hall` → A-3 派单让位 → A-4 原子抢单 + `GrabAttempt` → A-5 悬赏爬升 → A-6 释放 → A-7 拒单限额 → A-8 策略 → A-9 统计；**B 批前端另立**。
- 相关代码已存在：`mio_taskhub/api/hall.py`、`api/claim.py`、`scheduling/capacity.py`、`scheduling/gate.py`、`api/board.py`。

## 6. 协作与文档约定（`AGENTS.md`）

- 分支命名 `task-<8位十六进制 id>`；开发前 consult 文档（spec / api，写实现/计划前加 plan）。
- `claim` 返回 `run_id` + `required_reads` + `required_fr` + 内联预览；**内联预览不等于已读**，动手前必须对每个 required kind 调 `taskhub_read_document(task_id, kind, run_id=<run>)` 落 ReadEvidence（含内容指纹）；`submit_result` 校验，缺失 → 422、指纹不符 → 422。`git push --no-verify` 绕不过服务端门控。
- 写代码/测试引用需求文档里的 `FR-n` 编号（可追溯）。
- 文档链门控：进 `design` 要求 `requirement` 已登记且 approved；`requirement`/`spec`/`api` 推进 review/approved 需质量分 ≥80；棘轮基线低于历史最好值 → 422；`force=true` 留痕可绕过。
- 文档书写约定：分点换行、能用表格不用长段落、步骤编号逐行；接口契约事无巨细（每个参数给 位置/类型/必填/默认/约束/示例），禁止「详见代码」「同上」「略」。

## 7. 编制本方案时必须遵守的表达约束

- 用户明确反感「AI 味」文案：不要对仗排比金句、破题式设问、隐喻比喻、抖机灵。用工程汇报口吻：主语明确、陈述句、给字段名/阈值/状态。宁可留白，不用金句填空。
- 极简、纯功能，不堆 emoji / 动效。
- 技术选型必须给出**至少一个被否决的备选方案及理由**；排期以里程碑 + 依赖关系表格呈现并标关键路径；风险清单按「风险 / 概率 / 影响 / 应对」四列；不确定处标【待确认】，不编造事实。
- 涉及成本、工期、人力时给出**假设前提**，不拍脑袋给确定数字。
