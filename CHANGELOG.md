# Changelog

## v0.4.0 (2026-09-23)

### Added — 文档生命周期在 Web UI 可见（此前 8 类生命周期前端完全看不到）

`doc_statuses` 早就写进了库、`set_doc_status` 也有质量门控，但前端**一个像素都没渲染**——缺口其实有两半：

- **后端清单漏字段**（`api/task_documents.py`）：`GET /tasks/{id}/documents` 的条目由 `_rel_entry()` 构造，从未带 `status`；只有 `GET /doc` 与 `PUT /doc` 的响应里有。前端即使想渲染也拿不到数据。现补 `status`（= `doc_statuses[kind]`，形如 `{state, at, note}`）。
- **前端未消费**：新增 `web/src/docLifecycle.js`（状态 → 中文名 + 色调的纯映射，与 `doc_lifecycle.py` 同源），`DocPanel` 据此渲染 ① 列表条目的状态徽标 ② 选中文档 meta bar 的生命周期赛道（`草稿 → 待审 → 已批准`，当前节点描边高亮，已过节点正常色、未到节点淡化）。新增 `api.getDocStatuses()`。

两条设计约束（都由实测暴露）：

- **扫描发现的文档不挂状态**：`doc_statuses` 是「该 kind 登记的那一份」的状态，而 `discover_task_docs` 的 kind 只是文件名启发式归类——同一任务实测有 **13 份文件都被判成 `requirement`**，若统一套状态会显示成一排假「草稿」徽标。故只有 `source=field` 的登记条目携带 `status`，并有测试锁定该边界。
- **未落状态也显示赛道**：`spec`/`plan`/`api` 等登记了文档但尚未 `set_doc_status` 的 kind，此前什么都不显示；现在按全 `todo` 渲染赛道，让「这个 kind 有生命周期、还没开始推进」本身可见。

### Added — 阶段推进的文档生命周期门控（draft 空契约不能再进 design）

`advance_stage` / `move_to_stage` 此前只查 `doc_path_of`（文档路径是否注册），从不查 `doc_statuses`——于是 draft / 空契约仍能推进到 design，且 `api` 契约从未被任何阶段门控（生命周期可见性缺口的「后半段」）。

- 新增 `LIFECYCLE_GATE`（`api/task_stages.py`）：目标阶段 → `{kind: 所需最低状态}`。进入 `design` 需 `spec` + `api` 都已 `approved`；进入 `planning` 需 `plan` `approved`；进入 `brainstorming` 需 `requirement` `approved`。
- **`api` 首次被门控**：此前不在任何阶段 `document_kinds` 里，契约写完（自动落位 draft）就能直接推进；现在未 `approved` 不能进 design。
- **向后兼容**：仅当 `doc_statuses[kind]` 已显式设过状态才校验；从未设状态的旧任务 / 仅靠 `spec_path` 登记路径（未走 `PUT /doc`）的任务一律放行，不阻断历史数据。
- **force 绕过 + 留痕**：请求带 `force=true` 可跳过门控，并 emit `task_stage_gate_forced` 事件（与 `set_doc_status` 质量门控绕过同源）。MCP 工具 `taskhub_advance_stage` / `taskhub_move_to_stage` 新增 `force` 参数。
- `GET /tasks/stages/requirements` 新增返回 `lifecycle_gate`，供前端 / agent 提示「推进到某阶段需要哪些文档达何状态」。
- 新增 `doc_lifecycle.reached_state(kind, current, required)` 判定线性生命周期是否已达门槛。
- 测试：`tests/test_stage_lifecycle_gate.py`（10 例）；`test_task_doc_paths.py::test_write_then_advance_design_without_workspace_doc` 改为断言 draft 被门控 + force 绕过。

### Added — 自动更新模块（update/）

- 新增 `mio_taskhub/update/`：`UpdateService` 状态机（idle→checking→up_to_date|needs_manual|available|dismissed|check_failed；available→downloading→ready→applying→done|failed），覆盖版本自检、清单拉取、差量下载、校验（sha）、应用全流程。
- 新增 `api/update.py`（`/update/status|check|download|apply|dismiss`）与前端 `UpdateBanner`：在 Web UI 提示「更新可用 / 下载进度 / 需手动处理」（如跨大版本或校验不符）。
- 更新源可配置：环境变量 `MIO_UPDATE_CHANNEL`（默认 `stable`）；`install_dir()` 按 frozen / 开发态自动解析；偏好存 `~/.mio_taskhub/update/prefs.json`，下载暂存 `~/.mio_taskhub/updates`。
- 配套 `tests/test_update_*.py`（manifest / downloader / apply / runner / service / source / ws / e2e）共 10 套。

### Added — 开发前文档 Consult 强制（pre-push 门控 + ReadEvidence 留痕）

- 新增 `scripts/git-hooks/pre-push` + `pre_push_runner.py` + `install_hooks.py`：push 前校验任务 `spec/api/plan` 已 `approved`、diff 引用的 `FR-n` 真实存在于已批准需求文档；hub 不可达时放行并告警。
- 新增 `mio_taskhub/read_evidence.py` 与 `taskhub_read_document` 的 ReadEvidence：`claim` 返回 `required_reads` / `required_fr`，动手前必须对每个 required kind 落一条带内容指纹的读证，提交成功路径校验「缺失 / 指纹不符」→ 422。
- 新增 `tests/test_doc_gate.py` / `test_read_evidence.py`。

### Fixed — 看门狗超时误判（18 个 FAILED 里 12 个是误杀）

排查 2026-09-17 的会话可视化需求时发现：库里 18 个 `FAILED` 任务中有 **12 个是误判**——agent 仍在正常上报 `progress`、随后还成功提交了 `result`（`exit_code=0`），任务却在它奔跑途中被判死。抽样 `c5bb23fd` 事件流：`12:03:33` 判 FAILED，`12:06:26` 成功提交，**判死比成功提交早 2 分 53 秒**。三处根因：

- **`_sweep` 用 agent 级 OFFLINE 旁路了 run 级心跳新鲜度**（`heartbeat.py` / `background.py`）：判死条件是 `run.agent_offline or expired`，而 `agent_offline` 取 agent 表 `last_heartbeat`（`AGENT_TIMEOUT_SECONDS=180`），agent 又往往只在 claim 前心跳一次 —— 于是「agent 心跳过期」单独就能杀死一个正在正常上报 progress 的 run。新增 `HeartbeatSweep.effective_timeout()`：agent OFFLINE **只把有效超时收紧到基线**（`AGENT_OFFLINE_TIMEOUT_SECONDS=120`），绝不跳过 run 级新鲜度判定。既保住「agent 掉了要快速回收」的原意，又消除误杀。
- **系统侧状态迁移全部丢弃 TaskEvent**（`background.py` 9 处）：`apply_transition()` 会构造 `TaskEvent` 但不负责持久化，而 `background.py` 的 9 处调用**都丢掉了返回值**、从不 `db.add(event)`。后果是超时判死、重入队列、放行依赖等系统动作在 `taskevent` 里**完全不留痕**（实测 `taskevent` 只有 71 行，而 `event` 728 行）——事后根本无法归因。新增 `_apply_event()` / `_try_apply_event()` 统一 add，9 处全部替换。
- **FAILED 是终态，迟到的成功结果无法回写**：`_on_timeout` 记 `FAILED`，agent 之后提交成功结果时 `_safe_transition` 静默吞掉 `IllegalTransition`，库里留下 `task=FAILED + run=成功/exit0` 的矛盾终态且永不自动纠正。

### Fixed — 三处状态转换在状态机里根本不存在（静默失败）

排查过程中用 `validate_transition()` 实测发现，代码正在调用的三个转换**未在状态机定义**，全部抛 `IllegalTransition`：

- **`RUNNING → QUEUED`**（`_on_timeout` 重试重入分支）：原表只有 `CLAIMED→QUEUED` 的 T21/T20。更糟的是异常会**连带回滚同一事务里的 run 回收** —— 该 run 每轮扫描重复失败、永远回收不掉。新增 **T23** `(RUNNING,IMPLEMENTING)→(QUEUED,READY)`（SYSTEM）。
- **`RETRYING → QUEUED`**（`_requeue_retries` 退避到期重入）：命中的是 T16，而 T16 的 RETRYING 支 `allowed_actors={USER}`，scheduler 以 SYSTEM 调用直接抛「无权执行 T16」→ **指数退避重试从未真正重入过队列**。状态机索引按 `(from,from_stage,to,to_stage)` 唯一，同一 key 无法再挂第二个 T-id，故直接把 T16 的 RETRYING 支 actor 集合扩为 `{USER, SYSTEM}`（FAILED 各支保持 user-only，`test_t16_only_user` 锁定不变）。
- **`FAILED → COMPLETED`**（迟到成功纠正）：新增 **T22** `late_result_recovery`，允许 SYSTEM/AGENT，`requires_reason=True`。

`_on_timeout` 同时改为：先无条件落库 run 回收，任务迁移走 `_try_apply_event`（非法只记 warning 不中断），并对已终态任务短路。

### Fixed — 误判会被迟到成功结果悄悄翻案的反向风险

`runs.py` 新增 `_is_system_timeout_failure()` 守卫：只有当任务当前 `FAILED` 且**最近一次 TaskEvent 是系统超时判死**（`actor_type=system` 且 reason 前缀为 `agent_offline:` / `heartbeat_timeout:`）时，才允许 `_handle_success` 走 T22 改判。agent / user 主动报的失败**不接受**迟到的成功结果翻案，必须人工复核。

### Changed — 超时基线与单一事实源

- **`DEFAULT_TIMEOUT_SECONDS` 120 → 300**（可用 `MIO_TASKHUB_TIMEOUT_SECONDS` 覆盖）：实测 agent 心跳间隔 67s~199s，原值 120s 过紧，正常长任务也会被判死。agent 离线基线单独用 `MIO_TASKHUB_AGENT_OFFLINE_SECONDS`（默认 120）。
- **删除 `background.py` 里重复的 `HeartbeatSweep` / `RunInfo`**，统一从 `heartbeat.py` 导入（单一事实源）。此前两份几乎相同的判死逻辑各自演化，同一个缺陷得修两遍。线程心跳钩子改为可选回调 `on_tick` / `on_error`。
- **`api/board.py` 的超时告警口径改为复用同一常量**：原先自己硬编码 120s，会提示「将被重置重领」而 sweep 实际要等到 300s，告警与行为不一致。
- `RunInfo.timeout_seconds` 默认值改为 `None`（= 沿用 sweep 自身超时），否则 dataclass 默认值会覆盖调用方显式配置的 `timeout_seconds`。

### Tests

- 新增 `tests/test_timeout_misjudge.py`（9 例）：覆盖三条防线 —— agent OFFLINE 不旁路 run 新鲜度、系统迁移必须留痕、迟到成功可纠正系统超时判死；并反向验证 agent 自报失败不允许被翻案、正常成功路径不受影响。
- `tests/test_agent_heartbeat.py` 补 3 例：fresh run 在 agent OFFLINE 下不得判死、心跳落后 71s（实测生产值）仍在基线内不得判死、落后 130s 超出基线仍回收。
- `tests/test_task_doc_paths.py` 补 1 例：清单条目带 `status`（有生命周期的带 `{state}`、无生命周期的为 `None`）。
- `tests/test_documents_related.py` 补 1 例：扫描发现的文档不得携带生命周期状态（锁定 13 份同 kind 文档被误标的问题）。

### 数据修复

一次性脚本清洗 12 条历史误判终态：补记缺失的判死事件（`event_type=repair_note`，如实标注为「重建」而非伪造转换），再走 T22 改判回 `(completed, review)` —— 落到 review 队列，人工仍可驳回。改前备份 `~/.mio_taskhub/backups/taskhub.db.bak-*-pre-misjudge-repair`。

## v0.3.0 (2026-09-17)

### Added — 接口契约「事无巨细」标准（`api` kind）

用户要求：接口契约必须足够详细，记录每个细节。落地为全链最严的文档规格：

- **模板重写**（`doc_chain.py`）：`api` 骨架由 4 节扩到 **21 节**，含一份可直接整块复制的完整接口范例（请求参数/响应字段/错误码/示例四个子块 + curl 与 JSON 示例）。
- **10 个必需章节**（缺失即 error）：文档信息与范围 · 环境与基础地址 · 认证与鉴权 · 通用响应结构 · 统一错误码 · 字段命名与类型规范 · 幂等性与重试 · 接口清单 · 接口明细 · 变更记录。
- **11 个建议章节**（缺失 warn，只扣分）：版本策略 · 通用请求头 · 分页/排序/过滤 · 时间/数值/空值约定 · 枚举全集 · 限流与配额 · 超时与并发 · 文件上传与下载 · 安全与脱敏 · 兼容性与废弃策略 · 附录。
- **必需表格**：通用响应结构 ≥1 行、统一错误码 ≥1 行、接口清单 ≥1 行。
- **逐接口子块强制**（新增 `detail_rule` 机制）：`接口明细` 下每个 `### <Method> <Path>` 必须含 **请求参数 / 响应字段 / 错误码 / 示例** 四个 `#### ` 子块，且前三者各需 ≥1 行数据——缺任一项报 error 并附可执行修法。
- `SECTION_HINTS['api']` 扩到 21 条逐节写作指引，直接喂给 revision 指令（写作者照做即可）。

### Added — 质量规格两个新机制（`doc_quality.py`）

- **`recommended`（建议章节层）**：`QUALITY_SPEC[kind]['recommended']` 列出建议 H2 章节，缺失记 **warn**（不阻断门控，但计入评分）——让「该写但可以不写」和「必须写」分开。
- **`detail_rule`（明细单元完整性）**：`{'section', 'unit_label', 'min_units', 'unit_blocks'}`，把指定 H2 章节下的每个 `### ` 单元切开，逐个检查单元内必须存在的 `#### ` 子块及其表格数据行数；`unit_blocks` 值为 0 表示只要求子块存在（「示例」多为代码块）。
- 新增 `_split_by_heading(lines, prefix)` helper，供 H3/H4 分层解析复用。

### Fixed — 文档质量

- **`_table_data_rows` 数据行数始终多算 1 行**：旧实现仅在「表头下一行是分隔行」时置 `in_table=True`，导致**分隔行自身被计入数据行**——0 数据行的空表报告为 1 行，`min_table_rows: 1` 形同虚设，空表也能通过填充度校验。新实现先排除分隔行、再排除表头行，剩余才是数据行。该缺陷因接口契约的逐接口行数校验而暴露。

### Fixed — 可观测性两块裸 SQL 长期坏死（root cause 同类）

后台日志里 `Failed to evaluate stalled tasks` 每 2 分钟刷一次，且前端「任务链路」面板恒为空——根因是同一类「裸 `text()` SQL 绕过 ORM」的缺陷：

- **`observability/remediation.py`**：`state NOT IN ('completed','failed','cancelled')` 用了**小写字面量**，而枚举列存的是大写枚举名（`QUEUED`/`COMPLETED`/…），导致状态过滤完全失效、匹配全表；且原生 SQL 返回的 `created_at` 是字符串，直接取 `.tzinfo` 抛 `AttributeError`，函数**永远返回空、自动重排功能从未真正工作**。
- **`observability/task_trace.py`**：`get_task_trace` / `get_task_traces_summary` 三处叠加缺陷：①`Session.exec(stmt, params)` 不接受位置参数（**TypeError**，两函数直接全废）；②状态过滤同样小写字面量（`get_task_traces_summary` 永远匹配不到行）；③裸 SQL 返回的 `created_at/completed_at` 当 `datetime` 用（`.isoformat()` / `str - str` 必崩），且 `event_metadata` 查成 `event_metadata`（DB 列名实为 `metadata`）——导致 `GET /api/v1/traces/{id}` 恒 404、`GET /api/v1/traces` 恒 `[]`。
- **修复做法**：状态过滤改用大写枚举名；时间比较一律在 SQL 里用 `julianday()`（不再取回 Python）；裸 SQL 时间值经新增的 `utils.parse_utc()` 统一转 aware UTC datetime；`db.exec(...)` 改 `db.execute(...)`；`event_metadata` 用 `metadata AS event_metadata` 取列并 JSON 还原。
- **自动重排语义收窄（防误伤）**：旧逻辑「非终态 + 超 30 分钟」会把整块排队中的 `QUEUED` 任务当卡住反复重排（当前库有 93 条）。现判定改为**只有 CLAIMED/RUNNING/RETRYING 且超过阈值、且重试额度未耗尽**才算卡住；阈值可用 `MIO_TASKHUB_STALL_MINUTES` 覆盖。当前库活跃态为 0 条，**修复后零行为变化**。
- **测试补强**：旧 `test_remediation.py` 只断言「返回 list」，给 bug 背书；新增断言真实卡住任务被识别、QUEUED 不被误判、重试额度耗尽即停、阈值可配；新增 `test_task_trace.py` 断言 span 时长数值化、summary 含已完成任务且时长为数值、metadata 还原为 dict、未完成任务不出现。`_table_data_rows` 修复一并保留。

### Added — 接口契约纳入生命周期，质量门控真正生效

此前 `api` 没有生命周期，而质量门控挂在 `set_doc_status` 上 —— 接口契约的质量 error 只出现在 `PUT /doc` 响应与质量报告里，**不阻断任何推进**，等于「最严的规格 + 最软的约束」。现补齐：

- **`api` 加入 `DOC_LIFECYCLE`**，与 `spec` 同形：`draft → review → approved`（初始态 `draft`，首次写入自动落位）。文档生命周期由 7 类扩到 **8 类**（含 Task 自身共 9 条状态机）。
- 效果：`POST /tasks/{id}/doc/api/status` 推进到 `review`/`approved` 时要求 `errors == 0`，**空心的接口契约被 422 拒绝**；`force: true` 仍可绕过并落事件留痕。
- `tests/test_doc_lifecycle.py` 覆盖断言由 7 类扩到 8 类；`test_doc_quality.py` 新增门控端到端用例（空心契约 422 → force 通过；填满的契约 draft→review→approved 全通）。

### Tests — 文档体系

- `test_doc_quality.py` 新增 5 个用例：接口契约是全链最严规格（必需章节数最多 + detail_rule 齐备）、模板覆盖全部必需与建议章节且逐节报「未填写」、填满的参考契约 0 error 满分、`detail_rule` 三种失败形态（缺子块/子块空表/无接口小节）、`recommended` 缺失只 warn 不阻断而必需章节缺失仍 error。新增 `GOOD_API` 参考契约夹具（21 节齐全）。
- 另新增 api 生命周期质量门控用例（见上），合计 6 个新用例。

### Added — 任务文档体系（首轮，已提交于 6dc88a8）

- **22 类文档 kind**：新增 `doc_paths.py` 作为唯一事实源（`DOC_KINDS`），`Task.doc_paths` 以 kind→相对路径的 JSON 落库，覆盖七件套主链 + 工程记录 + 复用资产三类。
- **七件套文档链**（`doc_chain.py`）：`requirement → architecture → spec → data-model → api → test → runbook`。`POST /api/v1/tasks/{id}/docs/scaffold` 一键补骨架，**默认不覆盖已有文件**，支持 `kinds` 指定子集与 `overwrite`；骨架自带跨链导航头、`FR-n`/`TC-n`/`ADR-n` 追溯编号与 TODO 占位，正文按分点+换行书写。
- **文档生命周期状态机（首轮 7 类，后追加 api 至 8 类）**（`doc_lifecycle.py`）：requirement `draft→approved`、spec `draft→review→approved`、decision `proposed→accepted→superseded`、plan `draft→approved→done`、test `planned→passed/failed`、milestone `planned→released`、incident `open→resolved→closed`，加 Task 自身。规则为严格向前：不许回退、不许跳级、终态不可改判。新增 `Task.doc_statuses` JSON 列（含迁移），首次写入自动初始化初始态。
- **质量保证三层闭环**（`doc_quality.py`）：
  - 写时 lint —— 每次 `PUT /doc` 同步检查必备章节、表格有效数据行、TODO 残留、分点书写，按 `score = max(0, 100 - 20*errors - 5*warns)` 评分并随响应返回；
  - 状态门控 —— 推进到 `review`/`approved`/`done` 要求 `errors == 0`，`force=true` 可强推但落事件留痕；
  - 修订指令 —— `GET /doc/{kind}/revision` 返回逐条可执行的修改要求，附章节写作指引与模板片段，把评分反哺回写作阶段。
  - 追溯矩阵 —— `traceability()` 校验 `requirement` 的 `FR-n` 是否被 `test` 的 `TC-n` 覆盖。
- **4 个新 MCP 工具**：`taskhub_scaffold_docs` · `taskhub_set_doc_status` · `taskhub_doc_quality` · `taskhub_doc_revision`（总数 33 → **40**）。
- **5 个新 REST 端点**：`POST /docs/scaffold`、`POST /doc/{kind}/status`、`GET /doc/statuses`、`GET /doc/{kind}/revision`、`GET /doc/quality`（总数 93 → **119**）。
- **阶段产出物门控扩展**：`STAGE_ARTIFACT_REQUIREMENTS` 由单 `document_kind` 改为 `document_kinds` 列表，新增 `brainstorming → requirement`、`implementing → changelog` 两个此前缺失的门，`design` 明确要求讨论会话；`GET /stages/requirements` 同时返回 `document_kinds` 与兼容字段 `document_kind`。
- **前端文档面板**（`DocPanel.jsx`）：22 类 kind 分类展示、七件套一键起骨架、质量分与生命周期状态徽标；新增 `web/src/stageDocs.js` 作为阶段产出物要求的单一来源，阶段推进弹窗由硬编码改为查表（无要求的阶段不再弹空输入框）。

### Added — 结构化可观测性

- **`mio_taskhub/observability/report.py`**：新增 agent 可直接消费的结构化快照，避免解析 Prometheus 文本。
  - `GET /api/v1/observability/report` —— database / http / process / threads / tasks / agents / slo / alerts / insights 全量快照。
  - `GET /api/v1/observability/integrity` —— integrity_check 风格总报告，逐组件 PASS/WARN/FAIL + `overall_status` + 完整 snapshot。
- `GET /api/v1/observability/summary` 改从 `collect_observability()` 取值，不再正则解析指标文本。
- 前端新增**可观测性视图**（`ObservabilityView.jsx`）。

### Fixed — 可观测性

- **`middleware.py` 错误率统计错误**：原先把所有响应都计入 `_error_count`，导致 2xx 也被算作错误；改为仅 4xx/5xx 计数。此前 `HighHttpErrorRate` 告警与可用性 SLO 会基于虚高的错误率误报。
- **`slo_history.py` SQLModel 参数传递**：`db.exec(text, {...})` 改为 `params={...}`，否则查询与清理静默失效。
- **`insights.py` 重复洞察刷屏**：60s 一次的评估会把同一个未确认洞察反复插入；现按 `title + severity + acknowledged=0` 去重，已存在则直接返回原记录。
- `AlertManager` 的 `HighHttpErrorRate` 告警文案改为中文并带百分比与请求数。

### Changed

- `PUT /api/v1/tasks/{id}/doc` 响应新增 `quality` 与 `status` 字段。
- 任务创建/更新接口统一走 `doc_paths`，`spec_path` / `plan_path` 降级为兼容入口并保留双向同步（`merge_doc_paths` / `sync_legacy_fields`）。
- `/dashboard` 移除对 `web/dist/dashboard.html` 构建产物的依赖，改由 `main.py` 内置 `_DASHBOARD_HTML` 兜底（`web/dist/dashboard.html` 已删除，876 行）。

### Tests — 可观测性

- 新增 `tests/test_doc_chain.py`（骨架生成与不覆盖语义）、`tests/test_doc_lifecycle.py`（状态机合法性）、`tests/test_doc_quality.py`（质量评分、门控、修订指令）、`tests/test_task_doc_paths.py`（22 类 kind 与兼容字段）。
- 用例总数 509 → **603**（53 文件 / 7,585 行）。

### Docs

- 重写 `README.md`：修正全部统计数字（后端 76 模块 / 13,749 行、119 端点、40 MCP 工具、前端 40 文件 / 11,694 行、603 用例），新增「任务文档体系」独立章节（22 类 kind / 七件套链 / 9 类生命周期 / 质量三层闭环 / 端点表 / 典型闭环），更新阶段门控表、MCP 工具分组表与目录结构。

---

## v0.2.1 (2026-09-14)

### Fixed
- `/dashboard` 在 `web/dist/dashboard.html` 缺失时返回 404（且错误文案误写为 "Landing page not found"）。现改为回退到 `main.py` 中已有的内置仪表盘 HTML（`_DASHBOARD_HTML`），无需构建产物即可访问。

### Docs
- 重写 `README.md`：补充核心模型（状态机 / 7 段研发阶段 / 想法生命周期 / 实体表）、三种部署方式、MCP 33 工具分组、Web UI 视图表、可观测性与 SLO、完整环境变量表、7 个后台线程、打包踩坑与已知限制。
- 修正文档中的 API 文档地址：为 `/docs`（非 `/api/v1/docs`）。

---

## v0.2.0 (2026-09-11)

### Observability Overhaul (P0-P3)
- **OpenTelemetry**: Auto-instrumentation for FastAPI, SQLAlchemy, httpx
- **Built-in alerting**: `AlertManager` with thread/HTTP health rules
- **Alerts API**: `GET /api/v1/alerts` returns active alerts
- **Dashboard**: Self-contained HTML dashboard at `/dashboard` with Chart.js
- **USE metrics**: CPU, memory, threads, DB connection pool utilization
- **Business metrics**: Success/failure/cancel rates, throughput, P50 latency, retry stats
- **Dependency latency**: SQLite/Git/MCP call tracking (avg/P50/P90/P99/errors)
- **Thread health**: Heartbeat age, consecutive failures, alive status per thread
- **SLO/SLI**: Service level objectives with compliance metrics
- **Structured logging**: Trace context correlation (trace_id/span_id)
- **Log query API**: `GET /api/v1/logs` with level/logger filtering

### Prometheus Alert Rules
- `packaging/alerts.yml`: 24 alerting rules (critical/warning/info)
- `packaging/alertmanager.yml`: Alertmanager production config
- `packaging/prometheus.yml`: Prometheus scrape config

### New Modules
- `otel.py`: OpenTelemetry initialization and instrumentation
- `alerts.py`: Built-in AlertManager (no external dependencies)
- `dep_metrics.py`: Dependency latency collector with track() context manager

### Metrics Added
```
# System
taskhub_process_cpu_percent
taskhub_process_memory_rss_bytes / vms_bytes / percent
taskhub_process_threads
taskhub_db_pool_size / checked_out / checked_in / overflow / utilization
taskhub_thread_pool_alive / total / utilization

# Business
taskhub_task_success_rate / failure_rate / cancel_rate
taskhub_task_terminal_total
taskhub_task_throughput_24h / 7d
taskhub_task_retries_total / avg / max
taskhub_task_avg_completion_seconds / p50_seconds
taskhub_agent_utilization

# Dependencies
taskhub_dep_latency_count / errors / avg_ms / max_ms / p50_ms / p90_ms / p99_ms
taskhub_dep_error_rate

# SLO
taskhub_slo_availability_30d / target / breach
taskhub_slo_error_budget_remaining
taskhub_slo_latency_avg_ms_1d / breach
```

### API Endpoints Added
- `GET /dashboard` - Self-contained monitoring dashboard
- `GET /api/v1/alerts` - Active alert status
- `GET /api/v1/logs` - Log query with filtering

---

## v0.1.0 (2026-09-09)

### Initial Release
- FastAPI + SQLite task coordination hub
- Multi-agent dispatch with state machine
- MCP server for agent integration
- WebSocket real-time notifications
- ADR projection to Git
- Background thread management with heartbeat
- SQLite auto-backup (hourly snapshots)
- HTTP rate limiting (120 req/min/IP)
- PyInstaller EXE build
- Web SPA dashboard