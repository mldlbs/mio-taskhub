# mio-taskhub《项目交付方案》· 需求拆解与验收标准（Phase 1 / 任务 #1）

> 编制：许可研（场景产品策略师）｜取证日 2026-11-06
> 事实底稿：`docs/taskhub/_delivery-fact-pack.md`（唯一底稿）；补充取证 `README.md`(v0.6.1)、`docs/roadmap/01-positioning.md`、`docs/roadmap/03-technical-gap.md`、`docs/taskhub/frozen-scope-20261021.md`、`docs/taskhub/product-decision-20261020.md`、`docs/taskhub/narrow-gate-implementation-plan-20261020.md`、`docs/taskhub/diff-gate-grayscale-log.md`。
> 数字口径：版本 0.6.1 / 176 REST / 56 MCP / 22 类文档 kind / 1601 用例，均取事实包 §2（README v0.6.1 口径）；`docs/roadmap/01` 的 0.5.5 / 169 路由 / 47 MCP / ~1213 用例为 2026-10-10 审计旧基线，不作为本方案口径。
> 决策基线：冻结决策 2026-10-20/21；G2 实测回写 2026-10-14。
> 口径声明：本段遵守事实包 §4 G4 禁用词清单（「提升质量」「形成闭环」「生产级可靠」「首创/突破」），此处仅作纪律声明，不作为项目主张。文档质量 lint 对外只称「结构完整性校验」。当前定位为「个人/小团队效率工具」，排期按「剩余交付」计（G0 已过、G2 已有部分结论、G3 只做 D1 诊断可读化 + 开源前置、暂缓 D2-b）。
> 本节不含技术选型、工期日历与人力估算，那部分由系统架构师与主理人负责。

---

## 1. 一句话项目定义与成功标准

**定义**：mio-taskhub（v0.6.1）是一个本地单用户、零联网、零外部依赖的跨 Agent 任务协调中枢，它把多个编码 Agent 的任务归属、进度、产出收敛成本地 SQLite 中带状态机与文档链门控的可追溯事实。

**当前唯一成功判据**（决策 §1 验收第 4 条 / 事实包 §1，逐字）：能指着一例「它拦下了一次我本会放过的假完成」。

把它转成可判定条件，落点为 `docs/taskhub/diff-gate-grayscale-log.md` 的 M2：

| 判定项 | 可判定条件 | 取证 |
|---|---|---|
| 成功判据（唯一） | M2 ≥ 1 例：人工回看确认存在一次「声明 `success=true`、本次 run 工作区零改动、且本会被放过」的提交，被服务端拦下 | 灰度 log M2 记录 + `run` 表 `rejections > 0` 的 run 上下文 |
| 不误伤底线（非成功判据，是切档前置） | M3 = 0：合法完成被误拒次数为 0（误拒定义：后来证明真做了但没留下 diff） | 灰度 log M3 + `SELECT ... FROM run WHERE diff_summary IS NOT NULL` |
| 生效前置 | `MIO_DIFF_GATE=block` 且至少 3 个真实 run 完成 | 环境变量 + 灰度 log「切 block 的前置条件」三勾选 |
| 拦截可举证 | 每次拦截在「动态」tab 有红行 + 红色 toast（M4，4 秒内可见） | `Event(type=run.result_rejected, entity=task)` 查库 + 界面 |

现状：灰度 log 记录 2026-10-21 窄门禁落地、默认 `warn`、备注「尚未产生真实拦截」，M1~M5 全部「待观察」。因此该成功判据当前**尚未成立**，属 §7.2「需先满足前置条件才可验收」。

---

## 2. 目标用户与核心场景

### 2.1 用户分层

| 层 | 画像 | 依据 / 档位 |
|---|---|---|
| 核心 | 同时驱动 ≥2 个编码 Agent（opencode / claude code / codex / WorkBuddy 等）的个人开发者 / 小团队；本地、离线、零云依赖；在意研发过程可追溯（文档链 / 证据）。现网 = 1 人 | 事实包 §1、`01-positioning` §2.2（当前即为该形态的单用户）；定位维持「个人/小团队效率工具」 |
| 次要 | 需要强文档链 + 质量门 + 可追溯的合规 / 审计场景团队 | `01-positioning` §2.2，推断；当前无外部使用者证据 |
| 明确非目标 | ① 只想要「并行跑 Agent 看 diff 合并」的 vibe 工作流用户；② 要云端多租户 / 团队协作的 SaaS 用户；③ 要通用多 Agent 框架的算法团队 | 事实包 §1 明确非目标、`01-positioning` §2.2 与 §6.1 N1/N2 |

### 2.2 核心场景 S1–S4（按优先级，含完成条件）

| 编号 | 场景（谁 / 什么情况 / 做什么 / 得到什么） | 完成条件（可判定） | 现状 |
|---|---|---|---|
| S1 | 人把活派出去并看见：建任务 → Agent 注册 / 领取 → 看板 / 拓扑 / 甘特看进度 | 任务落 `queued`，Agent 状态 online，`claim` 返回 `run_id`；`GET /api/v1/tasks/graph` 返回 `has_cycle` / `cycle_path`；Web 视图渲染 state 与 stage | 已交付 |
| S2 | 强制「先读文档再动手」：`claim` 返回必读清单 → `read_document` 落 ReadEvidence → `submit_result` 指纹校验 | `required_reads` 未落或指纹不符 → 422；落齐 → 200 且 `completed→review` | 已交付；S2 单元 T 拦 422 / C 放 200（G2 T2.1，n=1，方向正确但样本不足） |
| S3 | 研发过程留痕可追溯：7 段阶段 + 文档生命周期 + 事件流三者一致 | 同一 task 上 `doc_statuses` 生命周期态、`TaskEvent` 状态 / 阶段迁移、`Event.seq` 单调可增量订阅，三者一致可查 | 已交付 |
| S4 | 想法到任务的发酵：想法 → 讨论 → 拆解 → ADR | 想法 `formed` → `breakdown_idea` 拆出任务，子任务 `ref` 互指依赖 | 能力存在但已冻结：`MIO_IDEA_REVIEW_ENABLED` 默认关（2026-10-21）；本轮不新增调度 |

补充一条与当前投入直接相关的场景，作为 S2 的收口，不单独占优先级位：

| 编号 | 场景 | 完成条件 | 现状 |
|---|---|---|---|
| S2-a | 人要求「完成」有硬证据：Agent 声称成功但本次零改动时，服务端拦下并让人看见 | `MIO_DIFF_GATE=block` 下 `success=true` + `changed==False` → 422 `run.empty_diff`；拦截三链路可见；M3=0 | 进行中（唯一投入项，详见 FR-B） |

---

## 3. 用户故事（人 / Agent / 评审方）

格式：作为 <角色>，我要 <动作>，以便 <可验证结果>。结果均绑定字段 / 状态码 / 可查询记录。

**人（作者 / 维护者）**

| # | 用户故事 |
|---|---|
| US-1 | 作为项目作者，我要在 `submit_result` 声称成功但本次 run 工作区零改动时收到 422，以便不把没有改动的执行记成完成。可验证：响应 `code=run.empty_diff`、`files_changed=0`，`Run.rejections` 加 1。 |
| US-2 | 作为项目作者，我要用 `MIO_DIFF_GATE` 的 off / warn / block 三档控制门禁，以便误伤时不用改代码、不用回滚迁移就能回退。可验证：改环境变量后 `block` 命中放行、`warn` 只写 `annotations` 不拦。 |
| US-3 | 作为项目作者，我要查只读统计端点，以便判断门禁是否真拦到过假完成、跳过原因分布是否正常。可验证：`curl -s http://127.0.0.1:48620/api/v1/board/diff-gate-stats` 返回统计，M1 / M5 可算。 |
| US-4 | 作为项目作者，我要对无代码产出的任务设 `diff_gate_exempt=true`，以便文档 / 评审 / 决策类任务不因无 diff 被误拦。可验证：置位后 `success=true` + 零改动 → 200。 |

**Agent（编码助手）**

| # | 用户故事 |
|---|---|
| US-5 | 作为编码 Agent，我要 `claim` 时拿到 `run_id` + `required_reads` + `required_fr` + 内联预览，以便按清单读文档（预览≠已读）。可验证：`claim` 响应含上述字段。 |
| US-6 | 作为编码 Agent，我要在落 ReadEvidence（含内容指纹）后提交结果，以便指纹缺失或变更时收到 422 而不是静默通过。可验证：`submit_result` 指纹校验。 |
| US-7 | 作为编码 Agent，我要在被拦后于同一 run 内实际改动再提交，以便不消耗 `attempt` 且能放行。可验证：`rejections < MAX_REJECTIONS(2)` 时 422 不消耗 attempt；改动后提交 → 200。 |
| US-8 | 作为编码 Agent，我要从 `GET /doc/{kind}/revision` 拿到逐条问题 + 章节写作指引 + 模板片段，以便把 `errors` 改到 0 再过状态门。可验证：revision 端点返回；`doc_quality` 分数 `= max(0, 100-20*errors-5*warns)`。 |

**评审方（人 / 审查者）**

| # | 用户故事 |
|---|---|
| US-9 | 作为评审方，我要 `submit_review` 在 reviewer 为最近执行者时返回 409，以便避免同一 Agent 自审自批。可验证：同一 agent 自审恒 409；`self_review=true` 才显式放行；approve 结论 ≥8 字符。 |
| US-10 | 作为评审方，我要 `traceability()` 输出 FR-n ↔ TC-n 覆盖矩阵，以便确认每条需求都有对应用例。可验证：`GET /api/v1/tasks/{id}/doc/quality` 含追溯矩阵。 |
| US-11 | 作为评审方，我要 7 段阶段 + `doc_statuses` + `Event.seq` 一致的留痕，以便确认「按什么契约、做完没」。可验证：查 `TaskEvent` / `Event` / `doc_statuses` 三者对同一 task 一致。 |

---

## 4. 需求拆解（FR 编号）

### 4.0 分组口径

本项目是 v0.6.1 已落地项目，需求结构不是「待建功能清单」，而是三层：

- **FR-Axx**：已交付能力的需求映射（现状证据指向真实模块 / 端点 / 字段），状态 = 已交付。
- **FR-Bxx**：当前唯一投入项「窄门禁」（拦假完成），状态 = 进行中。
- **FR-Cxx**：G2 结论驱动的条件性需求，逐条标触发条件与「未触发即不做」，状态 = 条件性。
- **发布前置的归属**：G3 / G4 的 LICENSE(MIT)、`scripts/gen_readme_stats.py` CI 校验、干净 Windows 机 ≥3 台零依赖首跑、Quickstart ≤5 分钟计时 n≥5、同 commit 可复现构建 + SHA256、changelog，属发布 checklist 的工程修复与诊断可读化交付，**不是新增产品功能需求**，不计入 FR-A/B/C，统一在 §7 验收标准中以可复现命令列出。此区分与「唯一保留投入项 = 窄门禁」不冲突。

### 4.1 FR-Axx 已交付能力的需求映射

| 编号 | 需求（能力） | 现状证据（模块 / 端点 / 字段） | 状态 |
|---|---|---|---|
| FR-A01 | 任务建管与执行状态机 | `models.py::TaskState.can_transition` 单点校验；8 态 `queued→claimed→running→completed/retrying/failed/cancelled`，非法转移拒绝；`blocked_failed` 为历史不可达态，由 `ops/data_fixes.py` 迁回 | 已交付 |
| FR-A02 | 7 段研发阶段门控 | `api/task_stages.py::STAGE_ARTIFACT_REQUIREMENTS`；`design` 另要求 ≥1 discussion；`GET /api/v1/stages/requirements` 返回 `document_kinds` 与 `lifecycle_gate` | 已交付 |
| FR-A03 | 文档体系：22 kind / 7 件套 / 9 类生命周期 | `doc_paths.py::DOC_KINDS`（唯一事实源）；`doc_chain.py`（`requirement→architecture→spec→data-model→api→test→runbook`）；`doc_lifecycle.py`；`Task.doc_paths` / `Task.doc_statuses` 为 JSON 列，读写经 `_resolve_within_workspace()` 越界校验 | 已交付 |
| FR-A04 | 结构完整性校验（写时 lint） | `doc_quality.check_content`，必备章节 / 表格行数 / 空壳占位 / 分点书写；`score = max(0, 100-20*errors-5*warns)`；`requirement/spec/api` 阈值 80。对外只宣称「结构完整性校验」，判断不了内容是否真的对 | 已交付 |
| FR-A05 | 状态门控 + 修订指令 + 质量棘轮 | 推进 `review`/`approved`/`done` 要求 `errors==0` 且分数 ≥80，`force=true` 绕过并 emit 事件留痕；`GET /api/v1/tasks/{id}/doc/{kind}/revision` 返回可执行修订指令；`ratchet.py` 质量分 / 用例数只升不降，回退 422；`GET /tasks/{id}/ratchet` | 已交付 |
| FR-A06 | ReadEvidence 内容指纹门控 | `read_evidence.py`（sha256）；`claim` 返回 `run_id` + `required_reads`（默认 spec/api/requirement）+ `required_fr` + 内联预览（预览≠已读）；`submit_result` 校验指纹，缺失 / 变更 → 422 | 已交付 |
| FR-A07 | FR↔TC 追溯 + pre-push 钩子 | `traceability()` 校验 `FR-n` ↔ `TC-n`；pre-push 钩子校验 spec/api/plan approved + diff 引用 FR-n 真实存在，**fail-open**，路径安装时固化 | 已交付 |
| FR-A08 | 多 Agent 注册 / 心跳 / 领取 / 派发 | `mcp_server.py`（56 MCP 工具）、176 REST；`Agent.last_heartbeat` 180s 无心跳转 offline；`background.py::scheduler` 每 30s 扫描；`scheduling/capacity.py`、`scheduling/gate.py`、`api/hall.py`、`api/claim.py`（抢单模式，任务 `3fa41beb`） | 已交付 |
| FR-A09 | 依赖编排与环检测 | `dependency.py`（满足 = `state==completed` 或 `stage==done`）；`planner.detect_cycle`；`GET /api/v1/tasks/graph` 返回 `has_cycle` / `cycle_path` | 已交付 |
| FR-A10 | 事件流与实时广播 | `events.py`（`Event.seq` 单调、增量订阅）+ WebSocket 广播；`TaskEvent`；`web/src/` React 18 + Vite 5、10+ 视图、`cpm.js` 关键路径 | 已交付 |
| FR-A11 | 评审记录与自审缓解 | `TaskReview` / `submit_review`；reviewer 为最近执行者时 approve → 409，`self_review=true` 显式放行，approve 结论 ≥8 字符（有 review 文档时豁免） | 已交付 |
| FR-A12 | 运维 / 备份 / 健康探针 | 7 个守护线程由 `ThreadRegistry` 反向优雅关停；SQLite Online Backup API 每小时、保留 31 份；`/healthz`、`/readyz`、`/metrics` | 已交付 |
| FR-A13 | 打包分发 | `packaging/build.ps1`、`packaging/run.py`（按 `sys.argv[1]` 分派 hub/mcp/widget）、`payload_layout.py`、`scripts/make_zip.py` 确定性 zip；`mio-taskhub-setup.exe` 装 / 卸同一文件 | 已交付 |
| FR-A14 | 安全基线 | 默认 loopback、单 Bearer token（仅 `/api/*`，Web UI 与 `/docs` 免认证）、`net_guard`(SSRF)、`mcp_risk` destructive 默认拒、DOMPurify | 已交付 |

### 4.2 FR-Bxx 窄门禁（当前唯一投入项）

| 编号 | 需求 | 语义与阈值 | 现状证据 | 状态 |
|---|---|---|---|---|
| FR-B01 | 服务端自行采集改动证据 | `Run` 落 `diff_summary`（改动文件数 / 增删行数 / 涉及路径列表），**不存全文 diff**；`create_run` 拍 before、`submit_result` 拍 after 双快照；`changed = head_after≠head_before OR porcelain_after≠porcelain_before OR numstat_insertions+deletions>0` | `product-decision-20261020.md` §1；`narrow-gate-implementation-plan` §1.1 / §2 B1-B6；`diff_gate.py`（`snapshot`/`differs`/`collect`/`check_diff_gate`）；事实包 §4 缺口 1（全仓原无 git diff 逻辑） | 进行中 |
| FR-B02 | 唯一拦截判据 | `success==True` 且 未豁免 且 可采集 且 `changed==False` → 422 `code=run.empty_diff`；插入点在 `api/runs.py` ReadEvidence 门禁之后、写 run 之前（约 `:288` 后、`:289` 前） | `narrow-gate-implementation-plan` §1.3 / §3；产品决策 §2 拦 vs 标注矩阵 | 进行中 |
| FR-B03 | 三档灰度开关 | `MIO_DIFF_GATE=off/warn/block`，默认 `warn`；`warn` 命中只写标注并放行，`block` 才 422；回退改 env，不需回滚代码或迁移 | 事实包 §1；`narrow-gate-implementation-plan` §2.1；`frozen-scope-20261021.md` §环境开关 | 进行中 |
| FR-B04 | 只读统计端点 | `GET /api/v1/board/diff-gate-stats`，纯统计、不参与任何流程判定（与 `_delivery_quality` 同性质） | 事实包 §1；`frozen-scope-20261021.md` §第 4 步配套 | 进行中 |
| FR-B05 | 拦截对人可见 | 三链路：`Event(type=run.result_rejected, entity=task)` → 红色 toast（`aria-live`）；`HistoryEvent(type=rejected)` → 动态 tab 红行；`Run.annotations(level=block)` → 完成报告红框卡。标注 `level=warn` 只常驻提示行、绝不弹 toast、写明「不影响放行」。独立错误码，不套用通用 422 文案 | 产品决策 §1.1；`narrow-gate-implementation-plan` §4 / §5 三个坑 / §6 F1-F7 | 进行中 |
| FR-B06 | 拒绝次数上限 | `Run.rejections`，`MAX_REJECTIONS=2`；`<2` → 422 不消耗 attempt、同 run 内可改完再交；`≥2` → `exit_code=1`、state=FINISHED、reason=`empty_diff_repeated`，交既有 `_handle_failure` 走重试；不把「被拦」混进「任务失败」口径 | `narrow-gate-implementation-plan` §1.4 / §3 | 进行中 |
| FR-B07 | 灰度观测 M1~M5 | 见 4.2.3；唯一成功判据落在 M2 | `docs/taskhub/diff-gate-grayscale-log.md` | 进行中（待观察） |

#### 4.2.1 `MIO_DIFF_GATE` 三档语义

| 档 | 采集 diff | 命中判据时 | 用途 | 进出条件 |
|---|---|---|---|---|
| `off` | 不采集 | 不拦、不标注 | 完全关闭；出问题立即回退位 | 决策 §1 停止条件：误伤频繁时回退到 `off` 或 `warn`，**不加严** |
| `warn`（默认） | 采集、落 `diff_summary` | 只写 `annotations` + 放行 | 灰度期跑 3–5 个真实 run 确认零误伤 | 2026-10-21 落地即此档 |
| `block` | 采集 | 422 `run.empty_diff` | 正式拦截档 | 需先满足「切 block 前置」（见 4.2.4） |

#### 4.2.2 误伤边界（豁免与 fail-open）

| 条件 | 处理 | 理由 |
|---|---|---|
| `task.workspace` 为空 | 跳过 | 无工作区无从采集 |
| workspace 非 git 仓库 / git 不可用 | 跳过 + 标注「无法采集 diff」 | 采集失败不得变成拦截 |
| `task.task_kind ∈ {idea_review, change_tracking}` 等无代码产出 kind | 跳过 | 评审 / 变更跟踪本无代码产出（`models.py:60-63`） |
| `task.diff_gate_exempt = True` | 跳过 | 用户显式豁免、可回溯（同 `doc_gate_exempt` 先例） |
| 存量任务（迁移前） | 自动置 `diff_gate_exempt=1` | 保住历史行为 |
| 同一 workspace 同时有 ≥2 个活跃 run | 跳过 + 标注「并发冲突」 | 快照互相污染，判据不可信 |
| before 快照失败 | best-effort、不阻断 `claim`，判据退化为「只看 after 是否非空」 | 偏 fail-open，可接受 |
| `collect()` 抛错 | 跳过 + 标注，不拦 | 判据设计为 fail-open |
| 测试是否通过 / diff 改动质量 | 只标注，不拦 | 合法工作时会误触发，信号模糊（产品决策 §2） |

说明：窄门禁只补三处质量能力硬缺口中的第 1 条「无变更识别」；第 2 条「无测试执行」与第 3 条「评审无身份隔离」不在本期范围（事实包 §4）。

#### 4.2.3 M1~M5 观测（`diff-gate-grayscale-log.md`）

| # | 指标 | 数据来源 | 判据 | 当前值 |
|---|---|---|---|---|
| M1 | 零改动提交被拦次数 | `run.rejections > 0` 的 run 数 | ≥ 1 才算「拦到过」 | 待观察 |
| M2 | 其中「本会放过的假完成」 | 人工回看每条拦截的上下文 | ≥ 1 例 = 决策验收第 4 条 = 唯一成功判据 | 待观察 |
| M3 | 合法完成被误拒次数 | 误拒 = 后来证明真做了但没留下 diff | 必须 = 0（G-3 零误伤） | 待观察 |
| M4 | 拦截可见性 | 动态 tab 可见 + 红色 toast | 4 秒内可见（G-2） | 待观察 |
| M5 | 豁免标注的跳过原因分布 | `run.diff_summary.reason` | 分布合理，不该大量是 `collect_failed` | 待观察 |

查询方式（灰度 log §查询方式，原样保留）：

```bash
curl -s http://127.0.0.1:48620/api/v1/board/diff-gate-stats | python -m json.tool
```

```sql
SELECT json_extract(diff_summary, '$.reason') AS reason,
       COUNT(*) AS n,
       SUM(CASE WHEN rejections > 0 THEN 1 ELSE 0 END) AS blocked_runs
FROM run WHERE diff_summary IS NOT NULL GROUP BY 1 ORDER BY n DESC;
```

#### 4.2.4 切 block 前置与回退

| 动作 | 前置 / 触发 | 依据 |
|---|---|---|
| 切 `MIO_DIFF_GATE=block` | ① 至少 3 个真实 run 完成；② M3 = 0；③ M2 ≥ 1 例确认拦到过假完成。三条全满足才切 | 灰度 log「切 block 的前置条件」（对应需求 FR-9） |
| 回退为仅标注 / `off` | 窄门禁频繁误伤合法完成（说明任务分类没分清）→ 立刻回退，**不要加严** | 产品决策 §1 停止/回退条件、§5 复审条件 1 |

### 4.3 FR-Cxx 条件性需求（G2 结论驱动，逐条标触发条件与「未触发即不做」）

| 编号 | 条件性需求 | 触发条件 | 未触发即不做 / 当前裁决 |
|---|---|---|---|
| FR-C01 | D2-a 门控效应 A/B 对照（V1） | 真实 Agent 可用 + P4 B 系列 agent 额度审批通过 + n≥30/组随机对照 | 当前证据不足（n=1、B 系列 defer、真实 agent 不可用）→ **不做**；且不得据此宣称「提升质量」 |
| FR-C02 | D2-b 门控协议化 | D2-a 通过（p<0.05、一次通过率不下降、force/绕过率 ≤10%）且 ≥1 非作者接入闭环 | G2 裁决 V1 证据不足 → **暂缓 D2-b** |
| FR-C03 | V2 文档质量的对照实验 | 补 A/B 对照（当前 `quality_strata.py` 为观察性 Fisher p=0.037、非对照） | 观测支持、非对照 → **不做对照宣称**，维持「结构完整性校验」口径 |
| FR-C04 | SLO / tracing / Prometheus 解冻 | 出现第二个真实用户（复审条件 4） | 当前外部使用者 0 → **冻结** |
| FR-C05 | 冻结项解冻（8 项） | 作者能演示某冻结项真的省了具体时间（复审条件 2） | 无此类证据 → **冻结** |
| FR-C06 | 可靠性判断上修 | 诚实记账后发现运行失败主因是口径失真而非真实故障（复审条件 3） | G2 三分根因：真死 27.7% / 链路断 70.9%（项目外）/ 观测不足 1.4%，D1 部分成立 81.9% → 维持「个人/小团队效率工具」，**不上修** |
| FR-C07 | D3 可验证完成回执 | ≥1 真实审查者 / 消费方（当前 0） | 依赖不存在的消费方且概念已不新颖 → **不做** |
| FR-C08 | 窄门禁回退为仅标注 | M3 > 0（频繁误伤合法完成） | 当前 M3 待观察 → 未触发；一旦触发按 FR-B 回退 |
| FR-C09 | 社区增长 / 定位升级目标 | 首启成功率 n≥100 后 ≥90% 且出现外部使用者 | 当前 81.9%（未达 90%）、外部使用者 0 → **不做**，仅保留内部分发观测（§7.3） |

### 4.4 人机边界（涉及 AI 的环节）

| 环节 | 执行方 | 理由 | 兜底策略 |
|---|---|---|---|
| 完成声明 `success: bool` | 智能体自报 | 现状如此（`submit_result` 成功路径直接采信自报 `success`） | 由服务端 `diff_gate.py` 空 diff 判据兜住，不改造为模型判定 |
| 空 diff 拦截 | 确定性代码 | 只在「明确的失败或撒谎」上拦截；一条 git 命令、几乎不误伤真完成 | `MIO_DIFF_GATE` 三档 + fail-open 采集 + `MAX_REJECTIONS=2` |
| 测试是否通过 / diff 改动质量 | 确定性代码（只标注） | 合法工作中大量在途 / 无测试场景会误触发 | 只写 `annotations`，不进流程判定、不弹 toast |
| 文档结构校验 | 确定性代码 | `doc_quality.check_content` 为启发式结构校验，判断不了内容正确性 | 不作内容质量宣称；`force=true` 绕过留痕 |
| 状态机 / 文档链门控 / 棘轮 | 确定性代码 | 领取 / 超时 / 状态 / 门控已全部确定性；N4 明确「不用 LLM 替代」 | `can_transition` 单点拒绝非法转移 |
| 人工确认 | 人工 | `force=true` 显式绕过、`diff_gate_exempt=true` 显式豁免、ReviewPanel 清单人手勾、`submit_review` 人工评审 | 绕过一律 emit 事件留痕；自审 409 缓解 |
| 智能体自主 | 智能体 | 注册 / 领活 / 上报进度 / 写文档 / 提交结果 | 受 `claim` 必读清单 + `submit_result` 指纹校验 + 窄门禁三重约束 |

原则：能用规则解决的不上模型；越权操作（destructive）由 `mcp_risk` 默认拒。

---

## 5. 需求优先级表（MoSCoW）

状态值域：已交付 / 进行中 / 条件性 / 冻结 / 不做。

### 5.1 主优先级表

| 编号 | 需求 | 优先级 | 依据 | 状态 |
|---|---|---|---|---|
| FR-A01~A14 | 已交付能力（任务 / 阶段 / 文档 / 门控 / 事件 / 分发 / 安全） | M | 事实包 §2 §3 已落地；项目问题不是「缺功能」而是「过度建设」 | 已交付 |
| FR-B01 | diff 证据采集（服务端自行采集，不采信自报） | M | 唯一投入项；补三处硬缺口之「无变更识别」 | 进行中 |
| FR-B02 | 唯一拦截判据 422 `run.empty_diff` | M | 决策 §1；唯一能把完成从自报变成有硬证据 | 进行中 |
| FR-B03 | `MIO_DIFF_GATE` off/warn/block 三档 | M | 灰度与回退的唯一手段 | 进行中 |
| FR-B04 | 只读统计端点 `diff-gate-stats` | M | 服务「判断唯一投入项是否产生价值」，不违反冻结原则 | 进行中 |
| FR-B05 | 拦截对人可见（三链路 + 独立错误码） | M | 决策 §1 验收第 5 条；不可见则成功判据无法举证 | 进行中 |
| FR-B06 | `MAX_REJECTIONS=2` 拒绝上限 | M | 防无限重试；不污染成功率口径 | 进行中 |
| FR-B07 | M1~M5 灰度观测 | M | 唯一成功判据（M2）的取证载体 | 进行中 |
| FR-A04（宣称口径） | 文档质量 lint 只称「结构完整性校验」 | M | V1 证据不足；冻结项 7「超出结构校验的文档质量阈值」 | 已交付（降宣称） |
| FR-C01 | D2-a 门控效应 A/B | C | G2 V1 证据不足，B 系列 defer | 条件性 |
| FR-C02 | D2-b 门控协议化 | C | G2 裁决暂缓 | 条件性 |
| FR-C03 | V2 对照实验 | C | 观测支持非对照 | 条件性 |
| FR-C04 | SLO / tracing 解冻 | W | 需第二个真实用户 | 冻结 |
| FR-C05 | 冻结项解冻 | W | 需作者演示省时证据 | 冻结 |
| FR-C06 | 可靠性判断上修 | W | D1 部分成立、根因在项目外 | 不做 |
| FR-C07 | D3 完成回执 | W | 无消费方、概念不新颖 | 不做 |
| FR-C08 | 窄门禁回退 | C | 误伤触发才执行 | 条件性 |
| FR-C09 | 社区增长 / 定位升级 | W | 首启 81.9%、外部使用者 0 | 不做 |
| — | G3 发布前置：D1 诊断可读化 + LICENSE(MIT) + README 统计脚本 + 干净机首跑 + Quickstart 计时 | S | 事实包 §4 G3 判据；属发布 checklist 工程修复，非新增产品功能 | 进行中【待确认具体交付形态与日历】 |
| — | G4 发布门：checklist 全绿 + 可复现构建 + SHA256 + changelog + 口径冻结 | S | 事实包 §4 G4 判据 | 待验收 |
| — | 8 项冻结能力（见 5.2） | W | 决策 §3 冻结清单 | 冻结 |
| — | 范围不做项 N1~N10（见 §6） | W | 事实包 §1 + `03-technical-gap` §6.1 | 不做 |

### 5.2 冻结 8 项逐条（决策 §3 / `frozen-scope-20261021.md`）

| 编号 | 冻结项 | 优先级 | 依据（决策证据） | 状态 |
|---|---|---|---|---|
| FZ-1 | creativity 调度（webhook / idea-generate） | W | 假设 61 行全空、可验证增量≈0；调度侧已无定时调用 | 冻结 |
| FZ-2 | observer 机会发现（taskhub 侧） | W | 素材噪声、洞察错配、有效产出≈0；`MIO_OBSERVER_AUTOSTART` 2026-10-21 已改默认关 | 冻结 |
| FZ-3 | Prometheus / SLO / tracing | W | 单用户本地工具，无人看指标；README §8 已标冻结 | 冻结 |
| FZ-4 | memory gateway | W | 无省时证据；README §5.6 已标冻结 | 冻结 |
| FZ-5 | ADR 投影 | W | 同上；README §5.7 已标冻结 | 冻结 |
| FZ-6 | idea 子系统扩张 | W | 53 条仅 1 采纳、无交付追踪；`MIO_IDEA_REVIEW_ENABLED` 已改默认关 | 冻结 |
| FZ-7 | 超出结构校验的文档质量阈值 | W | lint 仅结构校验、内容正确性未知；只称「结构完整性校验」 | 冻结 |
| FZ-8 | 多入口合并（advance/move、create/create_from_template） | W | 决策 §3 列为「合并收敛」，但 `frozen-scope` §为什么本轮不做：两入口非纯重复、无维护成本痛点证据、合并属结构性改动要改状态机流转。本轮不执行 | 冻结 |

冻结定义（不得写成删除）：代码不删、接口不删、文档保留；停投入、停调度、停当价值宣称、移出主链路。
勿擅自扩大范围：`MioDigestJob`、`ContractJob` 默认仍开，不在 8 项清单内；若要冻结需先扩决策清单。
已改默认关的两个调度：`MIO_OBSERVER_AUTOSTART`、`MIO_IDEA_REVIEW_ENABLED`（保留显式开启开关）。

---

## 6. 范围边界（不做什么）

来源 A = 事实包 §1「明确非目标」；来源 B = `docs/roadmap/03-technical-gap.md` §6.1「不值得继续开发」N1–N10。

| 编号 | 不做项 | 来源 | 理由 |
|---|---|---|---|
| N1 | Evidence → Insight 自喂养闭环 | B | `01` E10「Evidence→新 Insight」0/未建立，已裁定不建，无业务语义 |
| N2 | custom alertrule 前端运营入口 | B | `capability-map` §7 裁决「保留 API，不补 UI」 |
| N3 | taskreview 接线启用 | B | `capability-map` §7「降级保留，不接线」，无真实 review 行为 |
| N4 | 定时创意生成 / 灵感 webhook | B | 曾自我制造 99 条垃圾任务；3 个 scheduledjob 现全部 `enabled=0`，其一 `ERROR(502)`。与 FZ-1 对应 |
| N5 | 夜间 idle-plan 自动拉起 | B | `night_runner spawn … 脚本不存在` 50 次仍在发；用户已倾向下线，保持关闭、仅保留手动试跑 |
| N6 | RUoW 事务边界重构 | B | P1-4 并发实验证明当前边界无问题（零重复 / 100% 成功率），任务 837b156f 已关闭；触发条件：并发>1000 / 跨进程 / 多租户 |
| N7 | 与 vibe-kanban / paseo 正面拼 diff 评审、worktree 合并、内置浏览器 | B | 该位被 28.3k★ / 20.3k★ 占据，且 vibe-kanban 正在 sunset，竞争充分、变现存疑 |
| N8 | 云端多租户 / 团队权限体系 / 通用多 Agent 框架 | B（并见 A） | 与「本地单用户」定位冲突；多租户仅字段存在无隔离测试；上云后运维 / 安全成本指数上升；CrewAI/AutoGen/LangGraph 已占位 |
| N9 | 新增看板视图 / 甘特 / 拓扑类可视化扩展 | B | 能力已存在且有使用痕迹，继续扩张属功能面通胀；只修缺陷、不新增 |
| N10 | README 数字人工维护 | B | 6 项数字全部与代码相反；停人工维护，改 `scripts/gen_readme_stats.py` 由 CI 生成校验（属工程修复） |
| A-1 | 云端托管 / 多租户 / 团队权限 | A | 同 N8，与定位冲突 |
| A-2 | 通用多 Agent 框架（规划器 / Agent 运行时 / 新 LLM 编排） | A | 同 N8；E9 明确「没用更多 LLM 解决状态机问题，这点做对了」 |
| A-3 | 与 vibe-kanban 正面拼 diff 评审与 worktree 合并 | A | 同 N7 |
| A-4 | 用 LLM 替代确定性状态机 / 门控 | A | 领取 / 超时 / 状态 / 门控已全部确定性，方向正确，不得回退 |
| A-5 | Evidence → Insight 自喂养闭环 | A | 同 N1 |
| A-6 | 继续扩功能面 | A | E11：问题不是「缺功能」，是「价值证明」；方向为收敛 + 度量 |
| A-7 | 超出结构校验的文档质量阈值 / 测试执行器 / diff 质量评分 | A（并见 FZ-7、narrow-gate §10） | 只拦明确假完成；测试执行器与 diff 质量评分只标注不拦，独立立项本期不做 |

划界依据（`03` §6.2 口径）：约束不在功能数量，在价值证明与可靠性；资源压在「稳住执行 + 证明唯一投入项是否产生价值」，不拓边界、不追功能、不冲榜单。

---

## 7. 可度量验收标准（业务侧）

硬纪律（事实包 §4）：Star / Trending / fork / 转发数**不得**作为任何 Gate 判据，仅作分发观测。下表所有验收项均不含此类指标。
所有条目绑定一条可复现命令或可查询证据；无命令者不列为验收项。

### 7.1 当前可验收（已交付能力，命令可立即复现）

| 编号 | 验收项 | 可复现命令 / 可查询证据 | 判定 |
|---|---|---|---|
| AC-01 | 全量测试与基线 | `pytest -q`（`addopts = "-m 'not e2e'"` 默认排除 4 个 e2e）；覆盖率 `pytest --cov=mio_taskhub --cov-report=term-missing --cov-report=xml -q` | 0 红；G0 已修复 CI 全绿、覆盖率 80.0%、40 份关键文档入库 |
| AC-02 | 窄门禁功能（单测层） | `pytest tests/test_run_diff_gate.py -q --basetemp=<独立目录>`（10 用例：零改动 422 / 未 commit 有改动 200 / 已 commit 200 / exempt 200 / idea_review 200 / 非 git 200+标注 / 被拒后改完再交 200 / 连续拒绝超限判失败 / 迁移幂等 / 拦截事件落库） | 10 用例全绿；`MIO_DIFF_GATE=block` 下「success + 零改动」→ 422 且 `code=run.empty_diff` |
| AC-03 | 拦截事件确实落库（避开坑位①） | 查库：`SELECT * FROM event WHERE type='run.result_rejected' AND entity='task'` | 能查到记录，证明「抛 422 前已 `db.commit()`」；仅看界面不算通过 |
| AC-04 | 只读统计端点可用 | `curl -s http://127.0.0.1:48620/api/v1/board/diff-gate-stats \| python -m json.tool` | 返回统计；该端点不参与任何流程判定 |
| AC-05 | 三档开关语义 | 分别以 `MIO_DIFF_GATE=off/warn/block` 启动 hub，构造 `success=true` + 零改动 run | off 放行无标注；warn 放行且 `run.annotations` 有 `level=warn`；block 返回 422 |
| AC-06 | 误伤边界（豁免 / fail-open） | 分别构造：非 git 目录、`diff_gate_exempt=true`、`task_kind=idea_review`、同 workspace ≥2 活跃 run | 四类均 200（或跳过 + 标注 `diff_summary.reason`），无一被拦 |
| AC-07 | 拦截可见性 | 被拦后查 `Event` / `HistoryEvent(type=rejected)` / `Run.annotations(level=block)`，并观察动态 tab 与 toast | 三链路齐全；标注不弹 toast、提示行写明「不影响放行」 |
| AC-08 | ReadEvidence 指纹门控 | 构造未落 ReadEvidence 或改文档后再 `submit_result` | 缺失 / 指纹不符 → 422；G2 T2.1 单元 T 拦 422 / C 放 200（n=1，方向正确） |
| AC-09 | 结构完整性校验口径 | `GET /api/v1/tasks/{id}/doc/quality`；`GET /api/v1/tasks/{id}/doc/{kind}/revision` | 返回 `errors`/`warns`/`score` 与追溯矩阵；对外文案只称「结构完整性校验」，不含内容质量宣称 |
| AC-10 | 状态机 / 阶段 / 追溯一致性 | `GET /api/v1/tasks/{id}/doc/statuses`；`GET /api/v1/tasks/graph`（`has_cycle`/`cycle_path`）；`GET /api/v1/stages/requirements`（`lifecycle_gate`） | 非法转移被 `can_transition` 拒绝；`Event.seq` 单调可增量订阅 |
| AC-11 | 版本与规模口径一致 | 读 `mio_taskhub/version.py` / `pyproject.toml` = `0.6.1`；`python scripts/gen_readme_stats.py` 后 CI 校验 README 统计一致 | 不引用 README 手工数字；注口径与取证日 |

### 7.2 需先满足前置条件才可验收

| 编号 | 验收项 | 前置条件 | 可复现命令 / 可查询证据 | 判定 |
|---|---|---|---|---|
| AC-12 | 唯一成功判据 M2 ≥ 1 | ① 进入一个真实使用周期（灰度 log 起止需填写）；② 至少 3 个真实 run 完成；③ 切 `MIO_DIFF_GATE=block` | 灰度 log M2 人工回看记录 + `SELECT ... FROM run WHERE rejections > 0` | 能指着一例本会被放过的假完成被 422 拦下 |
| AC-13 | 零误伤 M3 = 0 | 同 AC-12，且完成合法性回看 | 灰度 log M3 | 合法完成误拒数 = 0；否则按 AC-16 回退 |
| AC-14 | 切 block 前置（FR-9） | 3 个勾选全满足：≥3 真实 run、M3=0、M2≥1 | 灰度 log「切 block 的前置条件」清单 | 三条全勾才切，缺一维持 `warn` |
| AC-15 | M4 拦截可见性 4 秒内 | 有真实拦截发生 | 每次拦截的动态 tab 红行 + 红色 toast 计时记录 | ≤4 秒 |
| AC-16 | 误伤回退有效性 | 触发 M3>0 | `MIO_DIFF_GATE=off`（或 `warn`）+ 复现原用例 | 不改代码、不迁移，拦截消失 |
| AC-17 | G3：干净 Windows 机零依赖首跑 ≥3 台 | 有可分发 Release + 干净机可得 | `Get-FileHash mio-taskhub-win64.zip -Algorithm SHA256`；解压双击 `mio-taskhub.exe` + `setup-agent.bat`；记录 3 台踩坑 | ≥3 台成功 |
| AC-18 | G3：Quickstart ≤5 分钟 | 同上 | 陌生用户 register→claim→submit 计时记录 n≥5 | ≤5 分钟 |
| AC-19 | G3：LICENSE(MIT) 与 README 单一事实源 | LICENSE 入库 + 统计脚本接入 CI | 查 `LICENSE` 文件；CI job 校验 `gen_readme_stats.py` 输出一致 | 文件存在且 CI 绿 |
| AC-20 | G4：同 commit 可复现构建 | 发布门 checklist 全绿 | `powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1` 同 commit 跑两次，比对产物 SHA256 + changelog | 两次 SHA256 一致；changelog 齐 |
| AC-21 | V1 门控效应结论（FR-C01） | 真实 Agent 可用 + P4 额度审批 + n≥30/组 | A/B 报告（当前 n=1、B 系列 defer） | 未满足前置前不得验收，也不得对外宣称 |
| AC-22 | D2-b 协议化 / SLO 解冻 / 冻结项解冻 | 对应 FR-C02 / C04 / C05 触发条件 | 复审记录 | 未触发即不验收 |

发布前置的假设前提（涉及成本 / 工期 / 人力时按事实包 §7 要求给出）：事实包 §4 基线「14–21 人周（3.5–5 个月）」为 2026-10-10 制定口径，前提是单人全职 + Agent 辅助、熟悉本仓库、含设计/实现/测试/取证，不含外部评审等待、审批墙钟、分发运营；墙钟依赖 reaper 样本 2–4 周 / B 系列 4 周封顶 / D1 生产样本 2–4 周。本段不排日历（决策 §6「具体排期由作者定」），具体日历【待确认】。

### 7.3 分发观测（非验收判据）

以下仅为分发观测指标，**不计入任何 Gate / 验收判据**（事实包 §4 硬纪律）：Star 周增 ≥15 持续 2 周、≥3 独立来源、≥3 外部闭环使用者、≥5 外部 Issue-PR。Star / Trending / fork / 转发数同理。

---

## 8. 需求侧风险与待确认项

### 8.1 风险（风险 / 概率 / 影响 / 应对）

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| 窄门禁误伤合法完成（任务分类没分清） | 中（当前 `warn` 未切 block，M3 待观察） | 任务推不动；历史上门禁一严就被关掉（`force=true` + `doc_gate_exempt` 迁移把存量任务全置豁免） | `MIO_DIFF_GATE=off/warn` 立即回退且不加严；M3=0 才允许切 block；按「只拦明确假完成」重定判据 |
| 采集失败被当成拦截 | 低 | 提交全被拒 | fail-open：`collect()` 抛错跳过 + 标注；before 快照失败退化为只看 after |
| 并发 run 快照污染 | 低 | 误判有改动 | 同 workspace ≥2 活跃 run → 跳过 + 标注「并发冲突」 |
| 无限重试 | 低 | run 反复提交 | `MAX_REJECTIONS=2`，超限判失败走既有重试 |
| 拦截事件未落库 / 人看不见 | 中 | 判据生效但唯一成功判据无法举证 | 抛 422 前 `db.commit()`；`entity='task'`；用 `HistoryEvent` 而非 `TaskEvent`；AC-03 查库确认 |
| V1 证据不足被误当作质量结论 | 中 | 对外宣称越界 | 遵守禁用词纪律；lint 只称「结构完整性校验」；D2-b 暂缓；G4 口径冻结 |
| 口径失真（cancelled/failed 混用、自审） | 中 | 证据与成功率不可信 | P1-1 / P1-2 前置止血；被拦不混进「任务失败」口径（FR-B06）；自审 409 缓解 |
| 观察周期样本不足（样本自限 n16 教训、外部使用者 0） | 高 | M2 无法举证，唯一成功判据判不了 | 一个真实周期 + ≥3 真实 run；不达标维持 `warn`，不强行切 block |
| 过度建设回潮 / 冻结项被擅自重启 | 中 | 违背「做减法」现行最高约束 | 冻结清单 + 勿擅自扩大范围（MioDigestJob / ContractJob 默认仍开）；按复审条件 4 条裁决 |
| 三处质量能力硬缺口只补了一条 | 已识别 | 完成判定仍不自足 | 窄门禁仅补「无变更识别」；测试执行器独立立项本期不做；评审身份隔离以 409 缓解；如实登记为已知限制 |

### 8.2 待确认项【待确认】（不编造，逐条给缺口与取证方向）

1. 灰度观察周期起止：`diff-gate-grayscale-log.md` 起止仍为 `____-__-__`，M1~M5 全「待观察」，2026-10-21 记录「尚未产生真实拦截」。当前是否已进入观察、是否已切 `block`【待确认】。取证：灰度 log 记录表 + 当前 `MIO_DIFF_GATE` 实际值。
2. 「代码类任务」的完整 `task_kind` 分类清单：决策 §1 写「任务属代码类」，豁免表只列 `idea_review` / `change_tracking`。完整分类【待确认】。取证：`models.py` task_kind 枚举与 `diff_gate.check_diff_gate` 实际判断。
3. `GET /api/v1/board/diff-gate-stats` 的确切返回字段 schema：事实包与冻结文档只说明「纯统计、不参与流程判定」。字段【待确认】。取证：`api/board.py`。
4. P1-1（区分 `cancelled`/`failed`、API 暴露含失败的全量统计）、P1-2（删 `self_review` 通道 / 禁空 reviewer）当前完成度：决策 §4 列为前置止血，事实包未给完成状态【待确认】。取证：决策 §4 验收项 + 对应端点 / DB 语义。
5. 「无测试执行」缺口的测试执行器是否独立立项及排期：`narrow-gate` §10 列为「本期不做、独立任务」。口径【待确认】。
6. 第二个真实用户 / 外部使用者计数：复审条件 3、4 依赖此数据，当前外部使用者 0【待确认】。
7. G3 / G4 具体日历排期：决策 §6 明确「不排日历，具体排期由作者定」；本段不排日历【待确认】，假设前提见 §7.2 末。
8. D1 诊断可读化的具体交付形态（字段名 / 入口 / 呈现位置）：事实包 §4 只写「把根因以可读诊断形式交付用户（已有 `_diagnose_command` 先例）」【待确认】。
9. MCP 工具数口径不一致：事实包 §2 与 README 概览表记 **56 个 MCP 工具**；README §4.1 正文、§4 分组、§11 目录仍写「40 个 MCP 工具」（旧口径）。本方案统一采用事实包 §2 的 56（取证日 2026-11-06），README 正文 40 是否为未更新的旧值【待确认】。同理 README §11 出现「119 个端点 / 1305 用例 / 26 个路由模块」等旧数字，与概览表 176 / 1601 不一致，需由统计脚本收口。
10. `diff-gate-grayscale-log.md` 中「需求 FR-9」的具体出处文档（切 block 前置对应编号）：本方案按灰度 log 原文引用为 FR-9，其所属需求文档【待确认】，不与本方案 FR-A/B/C 编号混用。

---

（本段交付完毕。技术选型、总体架构、里程碑排期表由系统架构师与主理人负责；本段未提供工期日历与人力估算，未对业务判断拍板，未虚构事实包之外的功能。）
