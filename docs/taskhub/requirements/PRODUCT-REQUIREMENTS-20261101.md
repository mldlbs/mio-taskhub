# mio-taskhub 产品需求文档（PRD）

## 0 文档信息

| 项 | 值 |
|---|---|
| 文档编号 | MIO-PRD-20261101 |
| 版本 | 1.0（功能基线） |
| 日期 | 2026-11-01 |
| 适用产品版本 | v0.6.1 |
| 状态 | 基线，可评审；不替代各模块已批准的 spec |
| 事实来源 | 代码、README、`docs/taskhub/` 模块文档、生产部署记录 |
| 变更规则 | FR 编号稳定；新增需求追加编号，不复用；冻结项不删除，只标记 |

本文件回答“产品是什么、为谁解决什么问题、必须满足哪些可验收需求”。实现细节以模块 spec 为准；本文不重新定义状态机内部表结构。

---

## 1 产品定义

### 1.1 一句话

**mio-taskhub 是本地单用户的跨 Agent 任务协同中枢**：把任务、执行、评审、文档、想法与素材放在同一个可追溯的本地事实库里，让多个 AI Agent 通过 MCP/HTTP 注册、领取、执行、上报，用户在看板上掌握全局。

### 1.2 要解决的问题

| 问题 | 现状后果 | 产品应对 |
|---|---|---|
| 多个 Agent 各干各的 | 重复劳动、状态不可见、任务转交靠人 | 统一任务状态机 + 领取/心跳/超时回收 |
| 完成质量不可验证 | “说完成了”没有证据 | 评审记录、完成门禁、diff 门禁、文档门禁、Read Evidence |
| 文档散落在会话里 | 需求/设计/接口/测试缺失，返工 | 22 类文档、7 件套文档链、9 类生命周期、质量 lint |
| 任务上下文跨会话丢失 | 同类坑反复踩 | 任务事件、执行历史、经验 lessons、素材库 |
| 外部信息不可追溯 | 引用无出处、重复采集 | 素材 raw/witness/extract 三层与逐字回源 |

### 1.3 产品目标

- G1：单个用户在本机用一条命令/一个 exe 启动，不依赖云服务。
- G2：任何 Agent 通过标准协议（MCP 优先，HTTP 兼容）接入，完成“注册→领取→心跳→提交”闭环。
- G3：每个任务的完成都有可核验证据（评审、测试、diff、文档、运行记录）。
- G4：任务上下文与外部素材可追溯、可去重、可重建，不因会话结束而丢失。

### 1.4 非目标

- NG1：不做多租户 SaaS、组织级 RBAC、云端协同。
- NG2：不做通用 Agent 编排引擎或模型训练平台。
- NG3：不把“人工评审”作为默认质量门禁；不提供人工标注流水线。
- NG4：不以“素材库业务效用”作为本版本验收前提；该指标未测量。
- NG5：不追求功能数量扩张；冻结项不因“看起来有用”恢复。

---

## 2 用户与角色

| 角色 | 说明 | 主要操作 |
|---|---|---|
| 本机用户（唯一人类） | 产品所有者与最终决策者 | 建任务、看板、评审、配置、审批高风险操作 |
| Worker Agent | 执行任务的 AI Agent（opencode / claude code / codex / hermes / WorkBuddy 等） | 注册、领取、心跳、提交结果、写文档 |
| Reviewer Agent | 独立评审者 | 提交 approve/reject/comment，禁止自审（除非显式声明） |
| 运维/安装者 | 本机管理员 | 安装、升级、备份、迁移、排障 |
| 外部系统 | Git、Prometheus、mio-intelligence 等 | 通过 GitRef、指标、记忆代理集成 |

权限模型：本地单用户信任模型；默认无认证，可通过 Bearer token 保护 `/api/*`；不做细粒度权限。Agent 身份仅用于归属与防自审，不是安全边界。

---

## 3 运行形态与总体架构

### 3.1 运行形态

| 形态 | 说明 |
|---|---|
| Hub | FastAPI + SQLite(WAL)，默认 `127.0.0.1:48620`；supervisor 管理 worker 进程 |
| Web UI | React SPA，hub 直接托管；托盘/浮动面板可打开 |
| MCP stdio | `mio-taskhub-mcp`，供 Agent 原生调用 |
| HTTP API | `/api/v1/*`，兼容非 MCP 客户端 |
| CLI | `mio-taskhub`（serve/hub 等） |
| Windows 绿色版 | 单 exe 目录，免 Python |
| Windows 安装包 | 图形安装/卸载，数据目录保留 |
| Docker/Linux | 源码或镜像部署 |

### 3.2 组件关系

```
Agent ──MCP/HTTP──> Hub ──> SQLite(WAL)
                     │        ├─ task/run/agent/event
                     │        ├─ doc/review/gate
                     │        └─ material raw/witness/extract
                     ├─ 后台线程：心跳、调度、备份、cron、夜跑、Git 投影
                     └─ WebSocket ──> Web UI
```

### 3.3 数据与部署位置

- 默认数据目录：`%USERPROFILE%\.mio_taskhub`
- 默认数据库：`~/.mio_taskhub/taskhub.db`
- 备份：`~/.mio_taskhub/backups/`，在线备份，默认每小时、保留 31 份
- 换机器：复制数据库文件即可；文档路径存相对路径

---

## 4 术语

| 术语 | 定义 |
|---|---|
| Task | 任务主体，含执行状态、研发阶段、依赖、文档、门禁字段 |
| Run | 一次领取后的执行实例，绑定 agent、进度、心跳、结果 |
| Stage | 研发阶段：brainstorming→design→planning→ready→implementing→review→done |
| State | 执行状态：queued/claimed/running/retrying/completed/failed/cancelled |
| Document kind | 22 类任务文档类型（requirement/spec/api/test/...） |
| 文档链 | requirement→architecture→spec→data-model→api→test→runbook |
| Read Evidence | Agent 在提交前确实读取指定文档的凭证 |
| Ratchet | 文档质量/测试用例数的历史最好值基线，只升不降 |
| Material | 逐字摘录 + 出处 + 类型/来源的证据片段 |
| Raw / Witness / Extract | 素材原始记录 / 回源见证 / 提取历史 |
| Frozen | 停止投入与价值宣称，保留端点/代码，不新增功能 |

---

## 5 功能需求

状态说明：`已实现` 表示代码与测试存在；`冻结` 表示保留但停止扩张；`未验证` 表示能力存在但业务效果未测量。

### 5.1 任务生命周期（FR-TASK）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-TASK-01 | 创建任务：标题、描述、目标 Agent、优先级、截止时间、验收标准、项目/工作区、标签、依赖 | API/MCP 创建成功；必填校验；返回 task_id | 已实现 |
| FR-TASK-02 | 执行状态机：queued→claimed→running→completed/failed；支持 retrying、cancelled、释放/退回 | 非法转移拒绝；每次转移写 TaskEvent | 已实现 |
| FR-TASK-03 | 研发阶段：brainstorming→design→planning→ready→implementing→review→done | 阶段推进受文档生命周期门控；force 留痕 | 已实现 |
| FR-TASK-04 | 优先级与目标 Agent：目标 Agent 优先领取，超时后允许 fallback | `fallback_after` 生效；非目标 Agent 不能提前抢 | 已实现 |
| FR-TASK-05 | 依赖编排：depends_on 数组；依赖满足才可领取；环检测 | 环拒绝；依赖图/关键路径可查询 | 已实现 |
| FR-TASK-06 | 超时与重试：timeout_min、max_retries、retry_at；失败可手动重试 | 超时回收；重试次数与退避可追踪 | 已实现 |
| FR-TASK-07 | 取消：取消原因枚举与可选详情；终态不可再领取 | 取消写事件；下游依赖保持阻塞 | 已实现 |
| FR-TASK-08 | 领取模式：`grab_mode` 仅允许 Agent 主动领取，不进入自动派单 | 自动调度跳过；claim 仍走 CAS | 已实现 |
| FR-TASK-09 | 任务查询：列表/详情/看板/事件/历史/运行记录 | 过滤、分页、状态/阶段/agent 维度可用 | 已实现 |
| FR-TASK-10 | 任务事件日志：append-only，投影当前状态 | 事件不可改；时间线可回放 | 已实现 |
| FR-TASK-11 | 任务层级：一层 parent/child；main/leaf/child 角色 | 子任务不可再有子任务；main 不可被普通 worker 领取 | 已实现 |
| FR-TASK-12 | 看板展示：状态×阶段合成、阻塞标签、工作流聚合 | 看板/列表/拓扑/甘特一致 | 已实现 |

### 5.2 执行与 Agent 协作（FR-RUN）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-RUN-01 | Agent 注册与心跳：name 唯一，心跳超时转 offline | 注册幂等；180s 无心跳 offline | 已实现 |
| FR-RUN-02 | 领取任务：返回 run_id、branch、required_reads、required_fr、documents、lessons | 无任务返回明确空态；指定 task_id 可定向领取 | 已实现 |
| FR-RUN-03 | 运行心跳：progress 0–100；agent 名必须匹配 run 所有者 | 非所有者 403；run 结束 409 | 已实现 |
| FR-RUN-04 | 提交结果：success 进 completed；failure 可 retrying/failed | 成功路径校验 Read Evidence；失败路径不强制 | 已实现 |
| FR-RUN-05 | 超时回收：watchdog 按心跳与 agent 状态回收 run | 回收写事件；不误杀有心跳长任务 | 已实现 |
| FR-RUN-06 | 分支与提交追溯：GitRef（branch/commit/PR/tag）与运行历史 | 任务可查全部引用；重复引用去重 | 已实现 |
| FR-RUN-07 | 经验回传：同项目 lessons/test_hints/file_layout/pitfalls | claim 返回同 workspace 近期完成经验 | 已实现 |
| FR-RUN-08 | 高风险确认：destructive 工具需显式确认 | MCP risk gate；确认留痕 | 已实现 |

### 5.3 评审与质量门（FR-GATE）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-GATE-01 | 评审记录：approve/reject/comment，追加不可变 | 评审历史可查；自审默认拒绝 | 已实现 |
| FR-GATE-02 | 完成门禁：必须有 completion_run 与交付证据 | 缺证据拒绝 done；可配置 gate_tier | 已实现 |
| FR-GATE-03 | Diff 门禁：声明成功但零改动拦截 | 豁免需字段与原因；事件留痕 | 已实现 |
| FR-GATE-04 | 文档门禁：requirement/spec/api/plan 批准后才可进下一阶段 | 未批准拒绝；force 写事件 | 已实现 |
| FR-GATE-05 | Read Evidence：claim 指定必读，submit 校验读取与版本 | 缺失/过期 422；`read_status` 可查 | 已实现 |
| FR-GATE-06 | 棘轮基线：文档质量分与测试用例数只升不降 | 低于历史最好值 422；force 留痕 | 已实现 |
| FR-GATE-07 | 偏差记录：执行偏离计划需 log/resolve | 偏差可查、可关闭、关联任务 | 已实现 |
| FR-GATE-08 | 阶段门禁清单：可查询某阶段要求的文档与质量门槛 | `stages/requirements` 返回清单 | 已实现 |

### 5.4 文档体系（FR-DOC）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-DOC-01 | 22 类文档 kind，唯一事实源 `DOC_KINDS` | 新增 kind 三处同步；路径特征可识别 | 已实现 |
| FR-DOC-02 | 7 件套文档链与骨架生成，默认不覆盖 | scaffold 可指定 kinds/overwrite | 已实现 |
| FR-DOC-03 | 9 类生命周期状态机，严格向前、终态不可改判 | 非法转移拒绝；allowed_next 可查 | 已实现 |
| FR-DOC-04 | 写时质量 lint：必备章节、表格行数、空壳、分点 | 返回 score/errors/warns | 已实现 |
| FR-DOC-05 | 质量门控：review/approved/done 要求 errors=0 | 不达标拒绝；force 留痕 | 已实现 |
| FR-DOC-06 | 修订指令：按章节给出可执行修法，而非只报错 | revision 返回逐条指引 | 已实现 |
| FR-DOC-07 | 路径安全：读写限制在 workspace 内 | 越界 400；相对路径入库 | 已实现 |
| FR-DOC-08 | FR/TC/ADR 追溯编号与缺失检查 | doc_quality 报告追溯缺口 | 已实现 |
| FR-DOC-09 | 文档发现：doc_paths 登记 + workspace 扫描 | 列表标注 source=field/scan | 已实现 |
| FR-DOC-10 | 评审与文档联动：review 文档可替代短摘要 | approve 校验摘要或 review 文档 | 已实现 |

### 5.5 分解、子任务与工作流（FR-DECOMP）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-DECOMP-01 | 分解预览与落库：context package、依赖、角色 | 预览不写库；apply 生成子任务 | 已实现 |
| FR-DECOMP-02 | 子任务清单：add/update/delete，独立状态 | 删除接口；依赖缺失结构化错误 | 已实现 |
| FR-DECOMP-03 | 工作流面板：`part-of:<owner>` 聚合平级任务 | 返回 edges/layers/blocked/critical_path | 已实现 |
| FR-DECOMP-04 | 拓扑与甘特：依赖图、关键路径、时间轴 | 图/表数据一致 | 已实现 |
| FR-DECOMP-05 | 层级约束：只允许一层父子，子任务必须 ready | 非法层级 422 | 已实现 |

### 5.6 想法、讨论、模板、ADR（FR-IDEA）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-IDEA-01 | 想法记录与生命周期：new→fermenting→formed→broken_down | 状态推进合法；可拆解为任务 | 已实现（冻结扩张） |
| FR-IDEA-02 | 想法评分、搜索、统计、关联 | 评分可复算；导出可用 | 已实现（冻结扩张） |
| FR-IDEA-03 | 讨论会话：挂 idea/task，双向消息，结论落库 | role=ask/user；close 写结论 | 已实现 |
| FR-IDEA-04 | 任务模板与版本：快照、回滚、从任务沉淀 | 版本可恢复；实例化成功 | 已实现 |
| FR-IDEA-05 | ADR 状态与 Git 投影 | 状态合法；Outbox 投影 | 冻结 |
| FR-IDEA-06 | 自动想法评审扫描 | 默认关闭，显式开关才运行 | 冻结 |

### 5.7 定时、空闲与夜跑（FR-SCHED）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-SCHED-01 | 标准 cron 定时任务：启停/暂停/恢复/手动触发 | 表达式校验；执行记录可查 | 已实现 |
| FR-SCHED-02 | 空闲计划：汇总可执行任务与窗口 | 计划可查询；不误跑依赖未满足任务 | 已实现 |
| FR-SCHED-03 | 夜跑：空闲窗口落成实际任务 | spawn 幂等；失败可见 | 已实现 |
| FR-SCHED-04 | 调度与领取不冲突：grab_mode 不入自动派单 | 自动调度跳过 | 已实现 |

### 5.8 素材库（FR-MAT）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-MAT-01 | 素材定义：逐字原文片段 + kind + origin，不含 LLM 生成物 | 非原文/LLM 产品拒绝 | 已实现 |
| FR-MAT-02 | 来源登记：source_key、adapter、模式、采集间隔、健康状态 | 未登记来源拒绝；`/materials/sources` 可查 | 已实现 |
| FR-MAT-03 | raw 版本化：不可变、可回源、按 revision 区分 | 同 key 重复采集不覆盖历史 | 已实现 |
| FR-MAT-04 | 确定性提炼：同一 raw 重复提取结果一致，零 LLM | 3 次提取一致；无网络依赖 | 已实现 |
| FR-MAT-05 | 硬闸：逐字回源、原子性、结论谓词、信号可引用、泛化失败、裸计数、标记泄漏 | 违规隔离并记录原因 | 已实现 |
| FR-MAT-06 | 去重与计数：内容指纹去重；occurrences/corroboration 分离 | 同源重复不增 corroboration | 已实现 |
| FR-MAT-07 | 状态与时效：active/quarantine/stale/refuted/archived；TTL | 非法转移拒绝；过期不删除 | 已实现 |
| FR-MAT-08 | 重建与提取历史：按版本重放，失败回滚 | extract 历史可查；重建不丢 linked 素材 | 已实现 |
| FR-MAT-09 | 引用关系：素材可被想法/文档/run/评审引用 | 同一引用唯一；可反查 | 已实现（link 表） |
| FR-MAT-10 | 数据质量门禁：回源/原子/去重/隔离/时效/审计 I1–I18，失败阻塞发布 | 门禁退出码非零；报告绑定 DB/commit | 已实现 |
| FR-MAT-11 | 来源模式：trial 不自动 active，只隔离 | trial 来源不产生 active | 已实现 |
| FR-MAT-12 | 可见性分层：quality 0.40/0.65 仅决定 active/quarantine | 文档/API/UI 不表述为质量达标线 | 已实现 |
| FR-MAT-13 | 真实可用率与语义误收 | 无独立标签，明确“未测量” | 未验证 |
| FR-MAT-14 | 素材面板：按来源/类型/时间浏览、标记支撑强度 | 面板可用；旧入口隐藏 | 已实现 |

### 5.9 可观测性与运维（FR-OBS）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-OBS-01 | 健康/就绪探针：`/healthz`、`/readyz`（DB 检查+备份信息） | 异常 503；正常 200 | 已实现 |
| FR-OBS-02 | 指标：Prometheus 文本；系统/业务/依赖/SLO | `/metrics` 可抓取 | 已实现（冻结扩张） |
| FR-OBS-03 | 看板与告警：HTML 看板、活跃告警、日志查询 | `/dashboard`、`/alerts`、`/logs` | 已实现（冻结扩张） |
| FR-OBS-04 | SLO/SLI 与错误预算 | 目标与预算可查 | 已实现（冻结扩张） |
| FR-OBS-05 | 结构化日志与 trace_id/span_id；OTel 埋点 | 日志可按 request/trace 关联 | 已实现（冻结扩张） |
| FR-OBS-06 | 备份与恢复：在线备份、保留策略、`/readyz` 可见 | 备份一致性；恢复替换 DB | 已实现 |

### 5.10 事件、接口与集成（FR-INT）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-INT-01 | 全局事件流：单调 seq，增量订阅 | `poll_events` 不丢不重 | 已实现 |
| FR-INT-02 | WebSocket 实时广播：任务/运行/记忆事件 | UI 实时更新；断线可重连 | 已实现 |
| FR-INT-03 | REST API：任务/运行/文档/评审/看板/素材/配置/更新等 | 176 个端点；OpenAPI 可用 | 已实现 |
| FR-INT-04 | MCP：53 个工具，覆盖任务/文档/评审/想法/讨论/素材 | 工具可列出；读写注解正确 | 已实现 |
| FR-INT-05 | 更新：latest.json/Release 资产校验与安装 | 版本比较、下载、校验 | 已实现 |
| FR-INT-06 | 记忆网关：代理 mio-intelligence MCP 为 HTTP | 健康/查询/记录/策略/观测 | 冻结 |
| FR-INT-07 | ADR Git 投影：Outbox 落盘 | 幂等；可清理 | 冻结 |

### 5.11 安装、配置与分发（FR-DIST）

| ID | 需求 | 验收要点 | 状态 |
|---|---|---|---|
| FR-DIST-01 | Windows 绿色版：单目录 exe，免 Python | 双击启动 hub；数据目录固定 | 已实现 |
| FR-DIST-02 | 图形安装/卸载：单文件 setup，数据默认保留 | 安装到 LOCALAPPDATA；卸载不删数据 | 已实现 |
| FR-DIST-03 | Agent 自动配置：opencode/claude code/codex/WorkBuddy | setup 脚本可识别并写入配置 | 已实现 |
| FR-DIST-04 | 托盘/浮动看板：后台驻留、快捷打开 | 关闭窗口不退出；托盘可退出 | 已实现 |
| FR-DIST-05 | 配置：环境变量，均有默认值 | 不配可跑；敏感项不入库 | 已实现 |
| FR-DIST-06 | 版本与升级：版本号、CHANGELOG、latest.json | 版本一致性测试；升级可回滚 | 已实现 |
| FR-DIST-07 | 可复现构建：确定性 zip、资产清单、SHA256 | 两次构建归一化一致 | 已实现 |

---

## 6 非功能需求

| ID | 类别 | 需求 | 验收 |
|---|---|---|---|
| NFR-01 | 本地优先 | 默认不依赖云；断网可完成核心闭环 | 断网启动/领取/提交通过 |
| NFR-02 | 可靠性 | SQLite WAL；supervisor 拉起 worker；崩溃可恢复 | 强杀 worker 后自动恢复；DB 完整 |
| NFR-03 | 数据一致性 | 状态转移、评审、事件、文档历史 append-only；备份为在线快照 | 审计脚本/测试通过 |
| NFR-04 | 性能 | HTTP P50<100ms、P99<2000ms；任务吞吐满足单用户 | 指标/SLO 可观测；压测记录 |
| NFR-05 | 安全 | 默认绑定本机；可选 Bearer；路径越界拒绝；高风险工具确认 | 越权/越界/未确认被拒 |
| NFR-06 | 兼容性 | Windows 10+；Python≥3.10；Linux/Docker 可运行 | CI windows-latest；容器构建 |
| NFR-07 | 可维护性 | 模块边界清晰；公开接口有测试；文档与代码同步 | 全量测试 1,800+；以 CI 为准，当前 2 项失败待跟踪 |
| NFR-08 | 可追溯 | 任务/运行/评审/文档/素材均有 ID 与时间线 | 任一结论可回查到来源 |
| NFR-09 | 可移植 | 数据库单文件可复制；文档相对路径 | 换机复制即可用 |
| NFR-10 | 范围纪律 | 冻结项不新增投入；不把未测量指标当结论 | 冻结清单/文档审查 |
| NFR-11 | 可访问性 | 高对比模式；键盘可达；关键操作有确认/反馈 | UI 手测/截图 |
| NFR-12 | 可测试性 | 关键行为可通过 API/CLI 黑盒验证；测试数据隔离 | 测试环境不碰生产库 |

---

## 7 外部接口

| 接口 | 说明 |
|---|---|
| REST `/api/v1` | 176 个端点：tasks/runs/agents/docs/reviews/board/ideas/discussions/templates/scheduled/ materials/config/update |
| MCP stdio | 53 个工具：任务领取/心跳/提交、文档读写/状态/质量、评审、想法/讨论、素材、定时等 |
| WebSocket | 任务/运行/记忆事件实时广播 |
| 更新 | GitHub Releases / latest.json / SHA256 校验 |
| 记忆网关 | 可选代理 mio-intelligence MCP（冻结） |
| 监控 | `/metrics`、`/dashboard`、`/alerts`、`/logs`（冻结扩张） |

---

## 8 数据模型概览

| 域 | 主要实体 | 关键约束 |
|---|---|---|
| 任务 | Task、TaskEvent、TaskReview、Run、Agent | 事件/评审 append-only；状态转移合法 |
| 文档 | Task.doc_paths/statuses、ReadEvidence、RatchetBaseline | 生命周期严格向前；质量分不降 |
| 协作 | Subtask、GitRef、HistoryEvent、Discussion/Message | 引用可追溯；讨论可挂任务/想法 |
| 想法 | Idea、IdeaScore、IdeaChange/History、IdeaAssumptionLink | 状态合法；版本留痕 |
| 模板 | TaskTemplate、TaskTemplateVersion | 版本快照可回滚 |
| 调度 | ScheduledJob、ScheduledJobExecution | cron 校验；执行记录 |
| 素材 | MaterialSource、MaterialRaw、Material、Witness、Extract、Link、Audit | raw 不可变；quote 可回源；去重/计数 |
| 事件 | Event、OutboxEvent、RouteDecision | seq 单调；outbox 可清理 |
| 配置 | AppConfig、RolePrompt | 变更留痕 |

---

## 9 验收与测试策略

| 层级 | 方法 | 当前证据 |
|---|---|---|
| 单元/集成 | pytest（API、状态机、文档、素材、迁移） | 当前 HEAD 全量 1,812 passed / 2 skipped；2 项已知失败（installer GUI、materials daily cap）待跟踪 |
| 数据质量 | 素材门禁 I1–I18、已知争议、结构校验、漂移/幂等 | 门禁测试与演练记录 |
| 文档质量 | lint + 生命周期门控 + 棘轮 | 文档质量 100 分基线 |
| 端到端 | Web UI + API 手测；e2e marker | 部署冒烟（API/素材/看板） |
| 发布 | 可复现构建、SHA256、门禁报告绑定 | 构建/部署记录 |
| 迁移 | 备份、反向迁移、并发写入、回滚 | `task.updated_at` 生产迁移验收 |

验收原则：

1. 任何“完成”必须有可回查证据；
2. 自动化可验证的，不引入人工签字；
3. 无法可靠测量的指标，明确标注“未测量”，不包装成结论。

---

## 10 当前状态与已知边界（2026-11-01）

### 10.1 已交付

- 任务/执行/评审/文档/依赖/模板/定时/事件/素材/安装/更新主链路；
- 生产 `task.updated_at` 迁移完成并验收；
- 素材形式化质量门禁、审计与报告绑定；
- Web UI 工作流面板、创建时间排序、子任务删除；
- 当前运行 exe `30f1dd48`（10-30 最新版）。

### 10.2 未验证 / 未测量

| 项 | 状态 |
|---|---|
| 素材真实可用率 | **未测量** |
| 素材语义误收 | **未测量** |
| 素材对下游任务的增量价值 | **未测量**；相关实验已关闭 |
| 分支保护 required checks 实际生效 | 未确认（不阻塞数据库迁移） |
| 素材生产回填是否重跑 | 未重跑；当前数据为历史回填 |

### 10.3 冻结项

- 想法子系统扩张（自动评审默认关闭）；
- 可观测性新增投入（端点保留）；
- 记忆网关新投入（代理保留）；
- ADR Git 投影新投入（逻辑保留）；
- 文档质量阈值扩张（结构门控保留）。

### 10.4 已知风险

1. 历史任务质量数据受重复任务/修复标记污染，不能直接做效用实验；
2. 新 exe 构建（`abdf1d73`）曾出现 worker 崩溃，当前回退到已知可用 `30f1dd48`；从当前 `main` 重建需隔离验证；
3. 生产素材库仍使用历史提炼结果，门禁能力存在但未对生产数据重跑；
4. 单用户本地模型的安全边界是“本机可信”，不是多用户隔离；
5. 当前 HEAD 全量测试有 2 项已知失败：installer GUI 安装、materials job daily cap；不阻塞本 PRD，但应单独立项。

---

## 11 优先级与范围

| 优先级 | 范围 |
|---|---|
| P0 | 任务闭环、领取/心跳/提交、评审与完成门禁、文档门禁/Read Evidence、素材回源与质量门禁、备份 |
| P1 | 工作流/拓扑/甘特、模板、定时/夜跑、安装/升级、可观测性端点 |
| 冻结 | 想法扩张、记忆网关、ADR 投影、可观测性新增、素材效用实验 |
| 非目标 | 多租户、云协同、通用 Agent 编排、人工标注流水线、未定义任务的效用宣称 |

---

## 12 追溯

| 需求组 | 主要代码/文档 |
|---|---|
| FR-TASK/RUN/GATE | `mio_taskhub/models.py`、`workflow/`、`api/tasks.py`、`api/runs.py`、`api/reviews.py` |
| FR-DOC | `doc_paths.py`、`doc_chain.py`、`doc_lifecycle.py`、`doc_quality.py`、`api/task_documents.py` |
| FR-DECOMP | `api/decompose.py`、`api/task_subtasks.py`、`api/task_graph.py`、`web/src/components/Workflow*` |
| FR-IDEA | `api/ideas*.py`、`api/discussions.py`、`api/adr.py`、`ideas/` |
| FR-SCHED | `scheduling/`、`api/scheduled_jobs.py`、`api/nightrun.py` |
| FR-MAT | `mio_taskhub/materials/`、`scripts/materials_*.py`、`docs/taskhub/materials/` |
| FR-OBS | `mio_taskhub/observability/`、`api/observability.py`、`ops/backup.py` |
| FR-INT/DIST | `mcp_server.py`、`main.py`、`installer/`、`update/`、`packaging/` |
