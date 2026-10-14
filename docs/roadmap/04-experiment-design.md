# 04 · 核心技术主张的可复现实验设计（mio-taskhub）

| 项 | 值 |
|---|---|
| 版本 | v0.2（评审通过 · 判据冻结） |
| 日期 | 2026-10-14 |
| 状态 | **已评审冻结 · 未运行**（本文档不含任何实验结果；§1/§4/§5/§7 自本版起只许追加 `deviations.md`，见 §0 自律 1） |
| 评审 | opencode 三视角结构化评审（2026-10-14，方法论与统计/工程实现/红队）；冻结基线引用 T0.9 `g0_manifest.json`（HEAD `ac21dfc0`、1284 passed、cov 80.0%、双开关 unset default） |
| 冻结声明 | SHA256（全文去掉本哈希串后的字节）= `b950496548f400f8baf0ea74c82fcd454e67671bd4964a8ca61223fe567ca49e`；核验：提取本哈希串、从全文删除、重算 SHA256 须相等。评审记录（双证据）：taskhub discussion `14a194af`（任务 `8ffd3169`）+ `docs/roadmap/04-review-20261014.md` |
| 作者 | opencode（实验设计；未改任何代码、未运行任何实验） |
| 上游 | `docs/roadmap/01-positioning.md`（主张 V1/V2/V3）、`docs/roadmap/02-current-state-audit.md`（现状基线 F1–F17）、`docs/roadmap/03-technical-gap.md`（方向 D1/D2、主张 E1–E6、Go/No-Go） |
| 依据 | 本仓库源码码位（见附录 A）+ 既有测试/探针实跑记录（`02` §4/§8、`scripts/audit_quality_gaps.py`） |
| 约束 | ① 按 §0 三档标注；② §7 判定规则在运行前冻结；③ **未经 §9 审批不得运行**；④ 功能冻结决策（memory 2026-09-30）仍然有效——本设计只产生验证性脚本，不新增产品功能 |

---

## 0. 阅读约定与四条自律

**证据分档**（沿用 `01`/`02`/`03` §0）：**事实** = 代码/命令输出直接证明（给路径+码位）；**推断** = 由实现推出（标"（推断）"）；**待验证** = 本文要检验的对象（标"（待验证）"）。

**四条防自欺规则（本文的预注册纪律）**：

1. **预注册**：假设、指标口径、样本量、随机化、判定阈值（§1/§4/§5/§7）在**运行前冻结**；运行后只允许追加结果与偏差记录（`deviations.md`），不允许修改任何已注册条目。
2. **全样本落盘**：每一次尝试（含 harness 自身出错）都写原始 jsonl，**不得删除、不得挑选**；失败案例与成功案例同权进入分母。
3. **指标不事后改**：任何指标定义/阈值在运行后变更 → 该变更之后的所有轮次**作废重跑**，且变更原因必须写入 `deviations.md`。
4. **不预填结果**：本文件所有"结果"列均为空占位（`（summarize 填充）`）；任何期望值只以"假设/判据"形式出现，不以"已达"形式出现。

> 与"机制 ≠ 效应"（`03` §0 自律 2）的对应：**M 系列只证明机制层**（门控是否真拦、恢复是否真发生），**不证明 agent 行为因此改变**（那是 B 系列），**不证明文档质量因此提升**（那是 P 系列）。三层证据不得互相替代、不得互相引用为结论。

---

## 1. 被检验的主张与可证伪假设

### 1.1 三模块总览

| 模块 | 检验对象 | 执行方式 | 可复现性 | 对应上游主张 |
|---|---|---|---|---|
| **M（机制层）** | 服务端治理机制的**强制性、正确性、开销** | 纯脚本驱动（HTTP/TestClient），无真实 agent | **高**：同 seed + 同 commit 可完全复算 | `03` §6.2 E1/E3/E4 的机制前置、`01` V1 的"门控在跑"半句 |
| **B（行为层）** | 治理机制**是否改变真实 agent 行为**（A/B） | 双 hub 实例随机路由真实 agent 执行同批任务 | 中：受 agent 可用性与样本自限威胁（n=16 教训） | `01` V1 = `03` E1 |
| **P（质量层）** | 质量门 + 棘轮**是否提升文档质量**（前后对照） | 生产库历史数据只读回溯 + 未来自然样本 | 中：观察性、有混杂 | `01` V2 = `03` E2 |

**分层结论规则**：M 通过 ≠ V1 成立；V1 成立必须 B 通过；V2 成立必须 P 通过。任何对外材料引用时必须写明模块。

### 1.2 假设清单（可证伪、带预注册判据）

> 全部为 **H0 对立式**陈述：写法是"若观测到 X 则不支持"，使每个假设都有明确的失败形态。
> 主假设族 = H1–H6（Holm 校正，族 α=0.05）；H7 为开销假设（单列，见 §5.4）。

| # | 假设（可证伪陈述） | 判据（预注册） | 覆盖场景 | 对应主张 | 档位 |
|---|---|---|---|---|---|
| **H1** | 治理档（T）下，**未读文档的成功提交** 100% 被 422 拦截；对照档（C）0% 拦截 | 配对 n=30：T 拦截 = 30/30 且 C 拦截 = 0/30；配对精确 McNemar p<0.05（Holm 后）。**T 出现 1 例逃逸即不支持** | S2 | E1 机制前置 | 事实（码位在）+ 待验证（效应） |
| **H2** | 文档在读证后被修改 → T 档以 `stale` 422 拦截；C 档 0% 拦截 | 配对 n=30：T = 30/30 stale 拦截、C = 0/30；McNemar p<0.05。**T 出现 1 例指纹过期后放行即不支持** | S2b | 证据版本一致性 | 同上 |
| **H3** | 质量分门与棘轮在 T 档阻断"低分文档进 approved"与"质量分回退"；C 档（force 路径）放行 | 配对 n=30/项：T 阻断率 100%、C 阻断率 0%；McNemar p<0.05。**T 出现 1 例低分 approved 或回退成功即不支持** | S6 | E2 机制前置 | 同上 |
| **H4** | 阶段生命周期门在 T 档阻断"requirement/spec 未 approved 即进 design"；C 档放行 | 配对 n=30：T = 30/30 阻断、C = 0/30；McNemar p<0.05 | S7 | 文档链门控（AGENTS.md 2026-09-23 收紧） | 同上 |
| **H5** | **恢复率**：进程中断（agent 心跳停止）后，任务在有界时间内回到正确终态，T 档与 C 档均 ≥95%，且两档差 ≤5pp（非劣） | 配对 n=42（每档）：T 恢复 = 42/42 → Clopper–Pearson 95% 下界 ≈0.91（支持"≥90%"）；若为 41/42 → 下界 <0.85 → 降为"证据不足"。两档恢复率之差的 95% CI 必须含 0 且 |差| <5pp | S4a | E3/E4 前置 | 待验证 |
| **H6** | **误拦截率**：合规提交（读齐 ∧ 指纹未变 ∧ success=true）在 T 档被 422 拦截率 ≤5%；`success=false` 的失败提交**两档均 0 拦截**（码位：门控只在 `success` 分支，附录 A-A6） | T 档合规提交 n=60、观察 0 拦截 → 单侧 95% 上界 = 3/60 = **5%**（rule of three），支持"≤5%"；要支持"≤2%"需 n≥150。失败提交 n=30，两档均 0 拦截（1 例即不支持） | S1+S3 合规子集 | 摩擦反噬风险（`03` D2 风险③） | 待验证 |
| **H7** | **重复提交防护**：同一 run 第二次成功提交 100% 被 409 拒绝，且任务只发生**一次** completed 转移；两档行为一致 | 配对 n=30：409 = 30/30 × 2 档；`event` 表中该任务 `task_result(success=true)` 与 completed 转移计数 = 1（任何 1 例双计即不支持） | S5 | 状态机正确性（非差异点，作对照基线） | 事实倾向（码位在）+ 待验证（并发形态） |
| **H8（开销）** | 治理档端到端任务耗时中位数 ≤ 对照档 1.5×，且 submit 单请求 P95 增量 ≤200ms | 见 §5.4；超过阈值 → "治理摩擦过高"成立（对 D2 是负面证据） | S1 | `03` D2 成本项 | 待验证 |

**明确不检验的（范围外，防止隐性扩指标）**：pre-push 钩子（git 场景，非任务链路）、限流/F11、MCP 风险门、多租户、网络分区、UI。这些属 `02` §9 技术债或 `03` §6.1 冻结项，不进本实验。

---

## 2. 对照设计：治理档位与开关

### 2.1 档位定义（全部使用现有开关，码位事实）

| 机制 | 开关（事实码位） | 治理档 T | 对照档 C | 备注 |
|---|---|---|---|---|
| Read Evidence 读证门 | `MIO_READ_GATE`（`read_evidence.py:38-39`，`0/off/false/no/disabled` 关闭）；`MIO_READ_GATE_KINDS`（`:42-46`，默认 `spec,api,requirement`） | 不设（默认开） | `MIO_READ_GATE=0` | `scripts/audit_quality_gaps.py` C1 已实证 C 档"未读也能 submit" |
| 棘轮（质量只升不降） | `MIO_RATCHET_DISABLED`（`ratchet.py:31-32`） | 不设（默认开） | `MIO_RATCHET_DISABLED=1` | audit C2 已实证可关 |
| 文档质量分门（≥80 才进 review/approved/done） | **无 env 开关**；唯一绕过 = 请求体 `force=true`（`api/task_documents.py:614-647`） | 不带 force | **带 `force=true`** | **已知混杂（推断）**：`force` 同时跳过质量门与棘轮两条检查且留痕 `forced=true` → C 档该场景测的是"force 路径"而非"完全无门"。写入 §8 局限 |
| 阶段生命周期门（进 design 须 requirement approved 等） | **无 env 开关**；`force=true`（`api/task_stages.py:171,369,429`） | 不带 force | **带 `force=true`** | 同上混杂 |
| 重复提交 / 所有权 / 状态机 | 无开关（`api/runs.py:46-75` 409 判据） | 同 C | 同 T | **核心正确性，非治理开关** → 只做两档一致性核对 + 绝对判定（H7） |
| 心跳超时回收（reaper） | `MIO_TASKHUB_TIMEOUT_SECONDS`、`MIO_TASKHUB_AGENT_OFFLINE_SECONDS`（`heartbeat.py:27-30`） | 两档同值（实验值，见 §5.3） | 同 T | 恢复机制不属本次治理自变量；两档用**相同实验时长参数**以保证可比 |

### 2.2 部署形态

- **快速场景（S1–S3、S5–S7）**：进程内 `TestClient(app)`（模式沿用 `scripts/audit_quality_gaps.py:25-31`），每档**独立一次性 DB**（`MIO_TASKHUB_DB` 指向 temp 目录，`MIO_HOME` 指向空目录）。
- **中断场景（S4）**：**真 uvicorn 子进程**（模式沿用 `tests/test_e2e_real_service.py` `RealServer`：随机空闲端口 + 独立 DB + `MIO_OBSERVER_AUTOSTART=0` + `MIO_TASKHUB_RATE_LIMIT=100000`），因为心跳回收线程只在真实服务 lifespan 里运行。
- **B 系列**：同时起 2 个真实 hub 实例（T 端口 / C 端口、各自 DB），按 seed 把任务随机分配到实例，agent 的 `MIO_TASKHUB_URL` 指向被分配实例（agent 不感知档位）。

### 2.3 档位自检（防"开关没生效"的假对照）

每轮开始前，harness 对两档各跑 1 个探针（复用 audit C1/C2 逻辑）：

| 探针 | T 档必须 | C 档必须 | 不满足时 |
|---|---|---|---|
| 未读 submit | 422 | 200 | **整轮无效**（档位未生效），修好重跑，记 `deviations.md` |
| 棘轮回退 | 422 | 200 | 同上 |

---

## 3. 任务集与场景设计

### 3.1 任务与文档模板（固定 ground truth）

每个任务单元由脚本生成，内容**预生成、与档位无关**（保证两档任务完全同构）：

- `workspace`：`<tmp>/exp_<seed>_<scenario>_<replicate>/`，内含 `spec.md` / `api.md` / `requirement.md`。
- **合格文档模板**：含 `QUALITY_SPEC` 全部必需章节、无模板注释残留、含 FR-1…FR-3（`doc_quality.py:251-313` 扣分项逐条满足，目标 score=100）。
- **不合格文档模板**：缺全部必需章节（score=0，复用 audit C4 构造法 `audit_quality_gaps.py:277-279`）。
- `ground_truth` 预写进该单元的 jsonl 首行：`{should_read, doc_mutated, submit_success, expected_status_by_arm, expected_terminal_state}` —— **先写期望、后发请求**，杜绝事后标定。

### 3.2 场景集（只选项目实际能力；每场景标注覆盖假设与 n）

> n 为**每档**重复次数；配对设计 = 同一 replicate 序号在 T/C 各执行一次（任务同构、顺序 AB/BA 随机）。
> 全部场景分 **R=3 轮**独立执行（每轮新 DB、新 seed），主分析合并 3 轮，同时报告各轮。

| # | 场景 | 注入/步骤（两档相同，除非注明） | 期望（T / C） | 覆盖假设 | n（每档） |
|---|---|---|---|---|---|
| **S1** | 正常执行 | 建任务(合格文档) → claim(带 task_id) → read_document(×3, 带 run_id) → heartbeat → submit success | 200 / 200；终态 completed+stage=review | H6（合规子集）、H8 | 30 |
| **S2** | **证据缺失** | claim → **跳过 read** → submit success | **422 / 200** | H1 | 30 |
| **S2b** | 指纹过期 | claim → read(×3) → **改写 spec.md** → submit success | **422(stale) / 200** | H2 | 30 |
| **S3** | **测试失败** | claim → (read ×3) → submit `success=false` → 观察退避 `retry_at` → 到期 requeue → 第 2 次 submit `success=false`（`max_retries=2`）→ 终态 | 两档一致：retrying → requeue → **failed**；**失败提交 0 拦截** | H6 失败子项、恢复率（重试口径） | 30 |
| **S3b** | 失败后恢复 | 同 S3，但第 2 次 submit `success=true`（先 read，新 run 证据按 run 隔离——audit B3 已证 run 隔离） | 两档一致：终态 completed | 恢复率（重试口径） | 30 |
| **S4a** | **进程中断**（agent 侧） | 真服务 + 短超时参数：claim 后**停止心跳**。亚型 ①从不 heartbeat（progress=0，`never_started` 路径，`background.py:246-277`）；亚型 ②heartbeat 过 progress=50 后停止（`heartbeat_timeout`/`agent_offline` 路径）。回收后再 claim → 完成 | 两档一致：reaper 在有界时间内回收 → requeue → 最终正确终态；`reaper_decision` 事件分类正确 | H5 | 42（21×2 亚型） |
| **S4b** | **进程中断**（hub 侧，**需 §9 批准**） | 真服务执行中 `proc.terminate()` → 重启同 DB → 观察 phantom run 被 reaper 回收 / 迟到提交行为（n=16 实验曾真实发生 hub 崩溃，`experiment-cohort-n16` §3.5） | 重启后任务可继续推进、无矛盾终态（task=FAILED 且 run=exit0 视为矛盾态） | H5 补充（单点恢复） | 5 |
| **S5** | **重复提交** | claim → read → submit success(200) → **同 run 再 submit success**；变体：30 轮"双 agent 并发抢同一 task"（模式沿用 `test_e2e_real_service.test_concurrent_claim_no_duplicates`） | **409 `run.result_already_submitted`**；并发变体：重复认领 = 0；completed 转移事件 = 1 | H7 | 30 + 30 轮并发 |
| **S6** | 质量门 + 棘轮 | ①不合格 spec：draft → review → approved；②合格 spec 先 approved(分=100) 再降级内容推进 approved（回退） | T：①422、②422；C（force）：①200、②200（`forced=true` 留痕） | H3 | 30（①15+②15） |
| **S7** | 阶段生命周期门 | 新任务 doc_statuses 置 `requirement=draft` → `stage/move` 或 `stage` 到 design（两条路径都测，模式沿用 audit D1/D2） | T：422；C（force）：200 | H4 | 30（move/advance 各 15） |

**场景选择声明（事实）**：以上 9 个场景全部有现成码位与先例探针（附录 A）；**不设计**系统不具备的场景（如"网络分区判定""多 agent 评审冲突""质量门在 CI 中的行为"——`03` §1.2/§6.1 已裁定不属于本项目或未实现）。

---

## 4. 指标定义与计算口径

> 统一规则：**每个指标必须携带 n**；**分母为 0 → 报 `N/A（无样本）`，禁止记 0**；分子分母的每一条都可回溯到 raw jsonl 行号或 DB 事件 id。

### 4.1 主指标

| 指标 | 分子 | 分母 | 数据源 | 方向 |
|---|---|---|---|---|
| **场景终态达成率**（每场景） | 终态 = ground truth 预期状态的任务数（S3/S4a 失败口径 = 耗尽重试后 FAILED 或按规则恢复；成功口径 = COMPLETED） | 该场景该档全部任务数（含 harness ERR，ERR 计为未达成） | raw jsonl + `GET /tasks/{id}` 终态快照 | 越高越好 |
| **任务成功率**（S1/S3b/S4a 完成口径） | `state=completed` 且 stage 到达 review 的任务 | 同上分母 | DB `task` 表终态 | 越高越好 |
| **逃逸率 E1：未读完成** | `completed ∧ 缺 required ReadEvidence` 的任务 | **`completed` 任务数**（分母是完成数，不是总数——防止"完成少则逃逸少"的假改善） | `readevidence` 表按 run_id 连接 `task` | 越低越好；T 档期望 0 |
| **逃逸率 E2：指纹过期放行** | `submit 200 ∧ 读证后文档指纹已变` 的尝试 | S2b 全部尝试 | raw jsonl（响应码）+ 文件 sha256 前后对比 | 越低越好；T 期望 0 |
| **逃逸率 E3：低质/回退 approved** | `approved ∧ quality_score<80`（S6-①）或 `回退后 approved ∧ score<基线`（S6-②） | S6 对应分组的推进尝试数 | `event` 表 `task_doc_status` payload 的 `quality_score`/`forced`（码位 `task_documents.py:657`） | 越低越好；T 期望 0 |
| **逃逸率 E4：未批准进 design** | `stage=design ∧ requirement 未 approved` 的任务 | S7 全部尝试 | `task.doc_statuses` + `task_stage` 事件 | 越低越好；T 期望 0 |
| **恢复率**（H5） | 中断后在 `T_limit` 内到达正确终态的任务（`T_limit` = 实验超时参数下 reaper 2 个扫描周期 + 1 次重跑预算，见 §5.3） | S4a 中断任务总数 | `reaper_decision` 事件 + 终态快照 | 越高越好 |
| **恢复时延** | 从中断注入时刻到任务重回 `queued/终态` 的秒数（**服务器 event 时间戳**，不用客户端时钟） | 同上（仅达成者） | `event.created_at` | P50/P95 越低越好 |
| **误拦截率**（H6） | 合规提交被 422/非预期 409 拒绝的次数 | 合规提交总数（`should_read ∧ 已读齐 ∧ 指纹未变 ∧ success=true`） | raw jsonl + ground_truth 标签 | 越低越好；报 rule-of-three 上界 |
| **失败提交拦截率** | `success=false` 被 422 拒绝的次数 | S3+S3b 失败提交总数 | raw jsonl | 必须 0 |
| **重复提交拒绝率**（H7） | 第二次成功提交返回 409 的次数 | S5 重复提交总数 | raw jsonl | 必须 100% |
| **双状态转移数** | 同一任务 `task_result(success=true)` >1 或 completed 转移 >1 的任务数 | S5 任务总数 | `event` 表按 task_id 聚合 | 必须 0 |

### 4.2 成本指标

| 指标 | 口径 | 数据源 |
|---|---|---|
| **端到端任务耗时** | `task_created` 事件 → 终态事件的服务器时间差（毫秒） | `event` 表 |
| **单请求时延** | 客户端 `perf_counter`：claim / read / submit 各自 P50/P95/P99 | raw jsonl `latency_ms` |
| **门控开销** | `median(submit_lat | T) − median(submit_lat | C)`；同法算 read 环节 | raw jsonl |
| **重试成本** | `runs/task`（n16 基线 1.56 作参照，非判据）、`heartbeat 数/task` | `run` 表、`event` |
| **事件/存储成本** | `event 行数/task`、DB 文件字节增量/task | `event` 表 + 文件大小 |
| **进程成本** | hub 进程 CPU 秒/task、峰值 RSS（每 5s 采样） | `Get-Counter`/`psutil`（子进程侧） |
| **B 系列专属** | token 成本/task（agent 侧日志可得则报，不可得标 N/A） | agent 运行日志 |

---

## 5. 样本量、随机化、环境控制、失败处理、统计不确定性

### 5.1 样本量（预注册，含推导）

| 假设 | 期望效应 | 检验 | 最低 n（推导） | **预注册 n** | 理由 |
|---|---|---|---|---|---|
| H1/H2/H3/H4 | 100% vs 0%（极端不一致配对） | 精确 McNemar（配对） | n=10 对 → 双侧 p=2×0.5¹⁰≈0.002 | **30 对** | 远超 α 要求，留出单例意外仍可判"部分"；对齐 `03` D2 "n≥30/组" |
| H5 恢复率 | 点估计 ≥95%、下界 ≥90% | 单臂 Clopper–Pearson | 全成 40/40 → 95% 下界 ≈0.912；39/42 → ≈0.868 | **42/档**（每轮 14） | 42 全成才支持"≥90%"；**n=42 不足以证明 ≥95%**（需 n≥72 且 0 失败）——判据如实降档（§7） |
| H6 误拦截率 ≤5% | 0 事件 | 单侧 rule of three（3/n） | n=60 → 上界 5% | **60 合规提交**（S1 30 + S3b 30） | 要支持 ≤2% 需 n≥150，**本轮不做该宣称** |
| H7 重复提交 | 100% 拒绝 | 单臂（期望全中） | n=30 → 若全中，下界 ≈0.884 | **30 + 并发 30 轮** | 与 e2e 并发用例互补 |
| H8 开销 | 中位数比 ≤1.5 | bootstrap 10,000 | 每档 S1=30 即可出稳定中位数 CI | 30/档 | 探索性精度，不参与 Holm |
| B 系列 | 未读完成率 T vs C 差异 | 配对/两比例 | 依 E1 预期效应（`03` 阈值 p<0.05，n≥30/组） | **≥30/组，4 周封顶** | 样本自限风险（n16 教训）→ 达不到即"证据不足" |

**重复次数**：R=3 轮独立执行（不同 seed、新 DB、间隔 ≥1 天以捕捉时间漂移）；主分析合并、分轮报告；**任一轮档位探针失败 → 该轮整体作废重跑**（不得只重跑"不利"轮）。

### 5.2 随机化

- **配对 + AB/BA 顺序随机**：每个 replicate 以 `seed = SHA256(master_seed || scenario || replicate)` 生成掷币结果决定先跑 T 还是 C，抵消单机负载的时间漂移。
- **master_seed**（默认 `20261010`）写入 `manifest.json`，运行后只读。
- 任务标题、agent 名、workspace 名均含 `seed/replicate` 后缀（保证唯一且可追溯），**内容与档位无关**。
- 场景执行顺序也按 seed 打乱（防止 S6 的棘轮状态积累影响排在其后的场景——另加隔离：S6 每个 replicate 独立 DB 分片）。

### 5.3 环境控制

| 项 | 固定值/规则 | 目的 |
|---|---|---|
| 代码版本 | 记录 `git rev-parse HEAD` + `git status --porcelain` 进 manifest；**实验期间禁止改动 `mio_taskhub/`**（违反 → 受影响轮次作废）；冻结基线 = T0.9 `scripts/baseline_freeze.py` 产出 `g0_manifest.json`（HEAD `ac21dfc0`、1284 passed / 0 failed、coverage 80.0%、`MIO_READ_GATE`/`MIO_RATCHET_DISABLED` 均 unset default） | 可复现 |
| Python | `.venv`（Python 3.12、pytest 9.1.1，同 `02` §1.2） | 消除环境漂移 |
| DB | 每轮独立 temp 目录（`MIO_TASKHUB_DB`），**只碰 temp，绝不连生产库**（`C:\Users\admin\.mio_taskhub\taskhub.db` 只读引用属 P 系列，另批） | 隔离 + 非破坏 |
| 观测/调度噪声 | `MIO_OBSERVER_AUTOSTART=0`、`MIO_DIGEST_DISABLED=1`、`MIO_CONTRACT_DISABLED=1`、`MIO_TASKHUB_RATE_LIMIT=100000`、`MIO_TASKHUB_TOKEN=""` | 降噪（F11/F8 类干扰） |
| 后台线程（F2） | ① **claim 一律显式带 `task_id`**（防止 insight_remediator 注入任务被抢——`02` §4.4 根因）；② 每轮开始先 `GET /tasks` 记基线快照，凡 harness 未创建的任务出现 → 标记为环境噪声并在汇总中单列（不删除、不计入分母） | 防测试非封闭 |
| S4 时长参数 | 两档同用 `MIO_TASKHUB_TIMEOUT_SECONDS=5`、`MIO_TASKHUB_AGENT_OFFLINE_SECONDS=3`（sweep 轮询默认 10s：`heartbeat.py:54`，实例化 `background.py:594`）→ `T_limit = 2×(5+10) = 30s` | 压缩实验时长；两档同参保证可比 |
| 限流/端口 | 随机空闲端口（`_free_port()` 模式） | 并行安全 |
| 时间戳 | 结论性时间一律取服务器 `event.created_at`；客户端 `latency_ms` 只用于时延分布 | 防客户端时钟误差 |
| 基线测试 | 每轮前跑 `pytest -q` 并留存输出；**T0.2 后无已知红**（原 F4 `test_spinning_guard` 4 例死引用已删 `ba290e6`，冻结基线 1284 passed / 0 failed，见上方代码版本行）；**出现任何失败 → 本轮不得开始** | 防"坏代码上跑实验" |

### 5.4 失败处理（含 harness 自身失败）

1. harness 异常（请求构造错、连接失败、断言前抛错）→ 记 `status=ERR` 进 raw jsonl，**计入分母**（场景达成率口径），不重试该单元。
2. **整轮/整场景重跑规则（预注册）**：某场景 ERR > 3/轮 或 全轮 ERR > 10% → 该场景（或该轮）**整组作废重跑**，作废数据保留于 `raw/aborted/`，原因写 `deviations.md`。**禁止只重跑失败单元**。
3. 服务子进程意外退出 → 记 ERR、重启、该轮标记；同轮 ≥2 次 → 整轮作废。
4. 实验期间代码/依赖变更 → 变更之后所有轮次作废（manifest SHA 不一致即触发）。

### 5.5 统计不确定性与多重比较

- **比例类**：一律报 **Clopper–Pearson 95% 精确区间** + 原始 k/n（如 `42/42`），不报裸百分比。
- **配对二元结局**：**精确 McNemar**（不一致对上做二项检验）；两档独立时才用 Fisher 精确检验。
- **时延类**：bootstrap 10,000 次重抽样的中位数差与 95% BCa CI。
- **多重比较**：主假设族 H1–H6 用 **Holm–Bonferroni** 控制族错误率 α=0.05；H7/H8 与所有分场景拆分指标、分轮指标均标"探索性"，**只报估计值与 CI、不下"支持"结论**。
- **样本量不足时的默认结论**：任何主假设在 4 周（或 3 轮后追加 2 轮）内未达预注册 n → 该假设判 **证据不足**，不得用"点估计有利"替代。

---

## 6. 原始结果落盘、失败留存、自动汇总与复现

### 6.1 目录结构（每轮一个不可变目录）

```
experiments/
  harness/                     # 实验脚本（评审后实现；属验证性代码，见 §9）
    run_m.py                   # M 系列执行器
    run_b.py                   # B 系列执行器（条件性）
    summarize.py               # 只读 raw → 指标与报告
    profiles.py                # T/C 档位定义（env 表，唯一真源）
  runs/
    <UTC时间戳>_<commit8>_seed<seed>_round<R>/
      manifest.json            # 冻结信息：seed、commit、env 全表、n、开始/结束时间、档位探针结果
      raw/<scenario>/<arm>/<replicate>.jsonl   # 一行一事件（见附录 B）
      raw/server/<arm>/{stdout,stderr}.log     # 真服务场景
      failures/*.json          # 全部非期望结局 + ERR 的展开副本（请求/响应/DB 快照）
      snapshots/db_<arm>.sqlite                # 轮末 DB 只读副本（供第三方复算）
      summary/{metrics.json, report.md}        # summarize.py 产出（唯一允许引用的数字来源）
      deviations.md            # 偏差登记（运行后只许追加）
```

**落盘规则**：jsonl 追加写、轮目录创建后 `manifest.json` 即只读（内容哈希记入 `summary/report.md`）；**失败案例同时进 `failures/`**（含完整响应体与当时 DB 快照），供人工归因。

### 6.2 自动汇总

`summarize.py` 硬约束：

- 输入**只有** `raw/` 与 `snapshots/`，**不接受任何手工数字参数**；
- 逐指标输出 `{numerator, denominator, ci95:[lo,hi], n}`，分母 0 → `{"n":0,"value":"N/A"}`；
- 主假设判定由脚本按 §7 规则**自动三态输出**（支持/不支持/证据不足），人只审阅不改写；
- 输出 `report.md` 含：manifest 哈希、每假设判定、全指标表、失败案例索引（链接 `failures/`）。

### 6.3 复现命令（预注册，运行时以此为准）

```powershell
# 0) 环境与基线（每轮前）
cd E:\work\code\agent-dev\mio-taskhub
git rev-parse HEAD; git status --porcelain        # 记录进 manifest
$env:MIO_TEST_DB_PATH = "$env:TEMP\exp_baseline.db"
.\.venv\Scripts\python.exe -m pytest -q           # 无已知红（T0.2 后基线 0 failed，见 §5.3）

# 1) 运行 M 系列（3 轮；未经 §9 批准不得执行）
.\.venv\Scripts\python.exe experiments\harness\run_m.py --seed 20261010 --rounds 3 --scenarios all
# 2) 汇总（只读 raw）
.\.venv\Scripts\python.exe experiments\harness\summarize.py --run experiments\runs\<dir> --out experiments\runs\<dir>\summary
# 3) 独立复算（第三方可只拿 snapshots/db_*.sqlite 重算比例类指标）
.\.venv\Scripts\python.exe experiments\harness\summarize.py --db experiments\runs\<dir>\snapshots\db_T.sqlite --only proportions

# B 系列（条件性；需 agent 可用性 + §9 批准）
.\.venv\Scripts\python.exe experiments\harness\run_b.py --tasks 60 --assign seed=20261010
```

**复现判据（预注册）**：同 seed、同 commit 重跑 → 二元结局（拦截/终态）必须 **100% 一致**；时延类允许 ±20% 漂移。二元结局不一致 = **复现失败**，该轮不得用于结论。

---

## 7. 判定规则（运行前冻结：支持 / 不支持 / 证据不足）

### 7.1 单假设三态判定

| 假设 | **支持** | **不支持** | **证据不足** |
|---|---|---|---|
| H1/H2/H3/H4 | T 档拦截 = n/n，C 档 = 0/n，Holm 校正后 McNemar p<0.05 | T 档出现 ≥1 例放行（机制逃逸），或两档差异不显著 | n 未达 30/档，或档位探针失败轮 >0 且无法重跑 |
| H5 | 每档恢复 ≥42/42 且两档差 95% CI 含 0 | 恢复率点估计 <90%，或出现"requeue 后永久卡死/矛盾终态" ≥1 例 | 恢复 40–41/42（下界 <0.90），或 n 未达 |
| H6 | 合规提交 0/60 拦截（上界 5%）且失败提交 0/30 拦截 | 失败提交被拦 ≥1，或合规拦截率点估计 >5% | 合规提交 <60 |
| H7 | 409 = 60/60（两档）且双转移 = 0 | 任一重复提交未被拒，或双转移 ≥1 | n 未达 |
| H8 | T/C 中位耗时比 ≤1.5 且 submit P95 增量 ≤200ms | 比值 >1.5 或 P95 增量 >200ms（"摩擦过高"成立） | 时延样本缺失或 round 间中位数漂移 >30% |
| **V1（B 系列）** | B 系列 n≥30/组：C 组未读完成率显著更高（McNemar/两比例 p<0.05，Holm 后）且 T 组一次通过率不下降 | 无显著差异，或 T 组一次通过率显著下降 | 4 周 n<30/组（样本自限） |
| **V2（P 系列）** | 门控收紧后低分 approved 占比显著下降（Fisher，且补全 26 条缺失分数后结论不变） | 差异不显著或方向相反 | 缺失分数无法补全导致分母不稳 |

### 7.2 总判定

- **支持**：主假设族 H1–H6 **全部**"支持"（H8 可"不支持"但须如实报告为摩擦成本）。
- **不支持**：任一主假设"不支持" → 对应子主张按上表回写到 `01`/`03`（含"不支持"字样与轮次链接）。
- **证据不足**：出现 §5.4 作废情形、n 未达、或复现失败 → 该假设保留"待验证"，**禁止用部分轮次的有利结果下结论**。

### 7.3 与 `03` §7 Go/No-Go 的衔接（只前推、不放松）

- M 全绿 = `03` D2-a 的**机制前置**达成，**不等于** D2-a 通过——D2-a 仍须 B 系列（E1）达标。
- H5（恢复）+ E4 修复后生产样本（n≥100 首启）共同构成 `03` D1 判据；本实验的 S4a 只提供**受控环境**样本，不能替代生产样本。
- 任何一档"不支持"都触发 `03` §7 对应退出动作，本文件不新增豁免。

---

## 8. 已知局限与混杂（先写死，防止事后辩解）

1. **机制 ≠ 行为**：M 系列的"agent"是脚本，天然合规或天然违规——它证明**门在**，不证明**人（agent）因此改变**。V1 的行为效应只有 B 系列能给。
2. **C 档 force 混杂（事实+推断）**：质量门/生命周期门无 env 开关，C 档用 `force=true`，而 force 一并跳过棘轮且留痕——C 档测的是"force 路径"，不是"绝对无门"。缓解：H3 的两组子场景（①质量②棘轮）在 C 档都用 force，两组结论合并解读时须注明；**根治**需新增 `MIO_DOC_QUALITY_GATE=0` 类开关（属代码改动，需 §9 单独批准，未批准前按现状执行）。
3. **harness 模拟 agent ≠ 真实 agent**：真实 agent 有 LLM 随机性、会话中断、token 限制（`experiment-cohort-n16` 的 10 次生命周期失败全部来自真实 agent）——M 系列**不覆盖**这部分失败分布。
4. **S4 用缩短超时**（5s/3s vs 生产 300s/120s）：恢复率在短窗口下的结论**不能直接外推**到生产窗口；两档同参只保证内部可比。
5. **样本自限**：B 系列可能因"系统健康 → 不派生任务"重演 n16 无法扩样（`03` §1.2 实验方法难度）→ 预设 4 周封顶即"证据不足"。
6. **P 系列观察性混杂**：历史文档与新文档不可比、26/103 次流转缺 `quality_score`（F16）、45 分边界样本（G6b）→ V2 结论强度天然低于 M/B。
7. **单机单库**：结论不外推到多进程并发 >1000、跨机部署（`03` §6.1 N6 触发条件）。
8. **~~`docs/` 不入库（F6）~~ 已解决（评审修订 R2，2026-10-14）**：T0.4（`4e0a7b2`）解除 `.gitignore` 整体忽略、白名单纳入 40 份文档，本设计文档已入库具备版本证据；原风险留档仅提醒引用时核对 git 历史。

---

## 9. 运行前置与审批（本设计当前状态：不运行）

**未经以下全部勾选，不得执行 §6.3 任何运行命令：**

| # | 前置项 | 类型 | 状态 |
|---|---|---|---|
| P1 | 本文档评审通过（`docs/roadmap/01–03` 系列同规格评审） | 流程 | **已评审并冻结（2026-10-14，三视角结构化评审，修订 R1–R4 后通过；记录见文档头 + `04-review-20261014.md`）** |
| P2 | `experiments/harness/*` 实现 + 代码评审（**功能冻结期的验证性代码**，需技术负责人确认不违反 2026-09-30 冻结决策） | 代码 | 未开始 |
| P3 | **S4b（kill 实验子进程）批准**——仅杀本实验启动的 uvicorn 子进程与 temp DB，不触生产、不触他人进程；仍按"破坏性操作"预检（`mio_policy_check`）逐轮执行 | 破坏性审批 | 未申请 |
| P4 | B 系列批准（消耗真实 agent 额度/时间；双 hub 实例端口与 DB 占用） | 资源审批 | 未申请 |
| P5 | 若采纳 §8-2 根治方案（新增质量门 env 开关）→ 按仓库文档链流程另立任务（requirement→spec→approved）后再动码 | 代码门控 | 未立任务 |

**运行前 checklist（每轮）**：基线 `pytest -q` 通过（除 F4 已知红）→ `manifest.json` 冻结 → 档位探针双绿 → 开跑 → 轮末 `summarize.py` → `report.md` 归档。

---

## 附录 A · 场景 ↔ 代码位/先例映射（事实）

| 场景要素 | 码位 / 先例 |
|---|---|
| 读证门 422 | `mio_taskhub/api/runs.py:254-259`（成功分支才校验）；`mio_taskhub/read_evidence.py:164-194`（missing/stale 判定）；探针先例 `scripts/audit_quality_gaps.py` B2/B3/C1 |
| 指纹计算 | `read_evidence.py:68-79`（sha256，文件缺失→""）；过期判定 `:180` |
| 对照开关 | `MIO_READ_GATE` `read_evidence.py:38-39`；`MIO_RATCHET_DISABLED` `ratchet.py:31-32`；先例 `audit_quality_gaps.py:239,256` |
| 质量门/棘轮 force 绕过 | `api/task_documents.py:614-647`（`forced` 落 `:657`）；先例 C4 |
| 生命周期门 | `api/task_stages.py:171,369,429`；先例 D1/D2/D4 |
| 失败提交→退避→重试 | `api/runs.py:351-389`（`_handle_failure`/`_fail_with_retry`）、`BASE_RETRY_SECONDS` `:104`；重入队 `background.py:196-230` |
| 失败提交不受读证门拦截 | `runs.py:255`（`if success and task is not None`）——H6 失败子项的码位依据 |
| 心跳超时回收 | `heartbeat.py:93-128`（`effective_timeout`）+ `background.py:233-338`（`_on_timeout`、`reaper_decision`、never_started 有界 requeue `:267-277`） |
| 重复提交 409 | `api/runs.py:46-75`（`assert_run_submittable`，码 `run.result_already_submitted`） |
| 所有权 403 | `runs.py:34-43`（`run.not_owner`，不传 agent 则跳过——harness 一律传 agent） |
| 并发不重复认领 | `tests/test_e2e_real_service.py::test_concurrent_claim_no_duplicates`（10 agent × 100 task 先例） |
| 真服务子进程 | `tests/test_e2e_real_service.py` `RealServer`（随机端口/独立 DB/`MIO_OBSERVER_AUTOSTART=0`/限流放开） |
| 测试非封闭（F2） | `02` §4.4：claim 必须带 `task_id`；背景注入源 `observability/insight_remediator.py:233-257` |
| 文档质量扣分项 | `mio_taskhub/doc_quality.py:251-313`、阈值 `:86-90`、`GATED_TARGETS :74` |
| 质量分落库 | `event.payload.quality_score/forced`（`task_documents.py` set_doc_status 分支）；生产分布见 `02` §8.4 |
| 迟到提交/系统超时翻案 | `runs.py:137-156,304-318`（S4b 观测点）；护栏测试 `tests/test_timeout_misjudge.py` |
| n16 样本自限 / hub 崩溃先例 | `docs/taskhub/experiment-cohort-n16-20261002.md` §3.4/§3.5 |

## 附录 B · jsonl 行 schema（预注册，示例为结构非结果）

```json
{"kind":"ground_truth","scenario":"S2","arm":"T","replicate":7,"seed":"...","expect":{"submit_status":422,"terminal":"n/a"},"labels":{"should_read":false,"doc_mutated":false,"compliant":false}}
{"kind":"http","scenario":"S2","arm":"T","replicate":7,"req":"POST /runs/{id}/result","ts_client":0,"latency_ms":0,"status":422,"detail_keys":["missing"],"server_ts":"<event.created_at>"}
{"kind":"state","scenario":"S2","arm":"T","replicate":7,"task_state":"<终态>","stage":"<阶段>","runs":1,"readevidence":0}
{"kind":"err","scenario":"S4a","arm":"C","replicate":3,"error":"<异常类+消息>","phase":"<步骤>"}
```

- `manifest.json` 必含：`master_seed, commit, dirty(bool+hash), python/pytest 版本, env 表（T/C 各一份）, n 表, R, 起止时间, 探针结果, harness 版本哈希`。
- `summary/metrics.json` 每指标固定形状：`{"metric":"E1","arm":"T","numerator":0,"denominator":0,"value":null,"ci95":[null,null],"n":0}`（0 分母 → `value:"N/A"`、`n:0`）。

## 附录 C · 指标计算参考实现要点（summarize.py 约束）

1. **E1**：`task JOIN run ON run.task_id=task.id LEFT JOIN readevidence ON readevidence.run_id=run.id`，`completed` 且 `missing_required = required_kinds(task) - evidence_kinds(run)` 非空 → 分子；分母仅 `completed`。
2. **恢复时延**：`max(event.created_at WHERE type='reaper_decision' AND entity_id=run) → min(event.created_at WHERE task 进入终态或重回 queued)`，全用服务器时间。
3. **双状态转移**：`select entity_id, count(*) from event where type='task_result' and json_extract(payload,'$.success')=1 group by 1 having count(*)>1`。
4. **CI**：比例用 `scipy.stats.beta` 分位数实现 Clopper–Pearson（`lo=beta.ppf(0.025,k,n-k+1), hi=beta.ppf(0.975,k+1,n-k)`；k=0/n 边界取 0/1）；无 scipy 环境则用预置查表（n≤150）并把查表值写入 summary 以保证复算一致。
5. **原则**：summarize 不写 raw、不改 raw、不接受手工输入数字；`report.md` 的每个数字必须能在 raw jsonl 中定位到行。

---

## 结论（可复述版）

1. **本设计把三条待验证主张拆成三层**：M（机制，脚本可复现、今天就能设计到位）→ B（行为 A/B，双 hub 随机路由）→ P（质量前后对照，观察性）。三层证据互不替代（§1.1）。
2. **8 条假设全部可证伪**（H1–H8），每条预先写死"支持/不支持/证据不足"三态判据（§7.1）；对照档完全由**现有开关**构成（`MIO_READ_GATE=0`、`MIO_RATCHET_DISABLED=1`、force 路径），无开关的质量门如实记录为已知混杂（§8-2）。
3. **场景只选有码位的**（S1–S7，附录 A 逐条映射）：正常执行、证据缺失/过期、测试失败与恢复、进程中断（agent 侧 + 可选 hub 侧）、重复提交与并发、质量/棘轮/生命周期门——不设计系统不具备的场景。
4. **口径先于数据**：每个指标给出分子/分母/数据源/空分母规则；样本量给出推导（30 配对、42 恢复、60 合规提交）；统计用精确检验 + Holm 校正；全样本 jsonl 落盘、失败案例留存、summarize.py 只读汇总（§6）。
5. **本文档不含任何实验结果**；§9 五项前置（评审、harness 实现、S4b 与 B 系列审批、可选开关任务）全部完成前，**不得运行**。运行后只允许追加结果与偏差，不允许回头改 §1/§4/§5/§7。
