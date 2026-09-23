# 文档 Consult 强制保障方案（提案）

> 本文件为方案提案（过程记录），非正式落地文档。正式落地文档以 mio-taskhub 文档链（task → plan / spec）为准。

## 1. 问题定义

现状：七件套文档链（requirement → architecture → spec → data-model → api → test → runbook）已能
scaffold 骨架、写正文、做生命周期门控——`spec`/`api` 进 `design` 前须 `approved`、`plan` 进
`planning` 前须 `approved`。

缺口：现有门控保的是「**文档被写出来并被批准**」，保不了「**写实现代码的人动手前真的读过
spec/api**」。批准人读过 ≠ 实施人读过。文档存在 ≠ 开发时会去 consult。

## 2. 目标 / 非目标

- 目标：把「读文档」从自觉行为，变成**过不了关就交付不了**或**最省事**的路径。
- 非目标：物理强迫人打开文件（做不到）；取代 code review；保证「读了但理解错」。

## 3. 三层机制

### 3.1 ① 路径最短化（surfacing）

- 机制
  - `AGENTS.md` 加铁律：「实现某任务前，先 `taskhub_read_document` 读它的 spec/api（agent）；
    human 先点开 DocPanel 里该任务的 spec/api 再动手」。
  - 任务卡 / PR 描述**自动带文档链接**：从分支名 `task-<id>` 或 commit/PR 体 `task:<id>` 解析，
    调 taskhub 拉 `doc_paths` 渲染成链接，让人「一点就到」。
  - DocPanel 已在 UI 展示文档状态徽章（缺失 / 已批准），保持。
- 作用对象：人 + agent。
- 机器校验：否（靠约定）。
- 绕过风险：低阻力但无强制力，纯靠习惯；是「降阻力」层，不是「保底线」层。
- 成本：低（改 `AGENTS.md` + 一个 PR 模板 / 链接生成小脚本）。

### 3.2 ② 交付物门控（merge / push gate）

- 机制：在「代码真正进主干」前，校验该任务 `spec`/`api` 已 `approved`。
  - **推荐本地 `pre-push` git 钩子**（dev 本地 hub 在跑，能查 `localhost:48620`）：
    解析分支 `task-<id>` → `GET /api/v1/tasks/{id}` 读 `doc_statuses` →
    `spec`/`api` 非 `approved` 则拒绝 push。
  - 备选 / 加强：若 taskhub 有可达部署，加 GitHub Actions 服务端校验；否则仅靠 pre-push
    （绕过方式 `git push --no-verify`，需团队纪律 + 分支保护兜底）。
- 作用对象：人（PR / 合并）。
- 机器校验：是。
- 为什么比现有 advance 门控更强：现有 `advance_stage` 门控只在校验「显式推进阶段」时触发、
  **UI 当前无法触发**、且有 `force` 绕过；push gate 卡在**真实交付物**上，绕过需显式
  `--no-verify`，更显眼、更难糊弄。
- 成本：中（钩子脚本 + 安装说明；服务端版需可达部署）。

### 3.3 ③ FR-n 可追溯校验

- 机制：每个特性提交 / PR 的 diff 必须引用 `FR-n`，且每个被引用的 `FR-n` 必须**真实存在于
  该任务「已批准」的需求文档**。因为 FR 编号只活在需求文档，开发想 cite 出合法编号就
  **不得不读需求文档**。
  - 落地：本地 hook 或 pytest 扫描 diff 中的 `FR-\d+`，调 taskhub 取 requirement 文档正文，
    断言「引用集合 ⊆ 文档中的 FR 集合」。
  - 已有基础：需求已用 `FR-n` 编号、测试用 `TC-n` 追溯——只差校验闭环。
- 作用对象：人（代码 / 测试）。
- 机器校验：是。
- 成本：中（扫描 + 比对脚本，依赖 taskhub 可读）。

## 4. 流程（文字图）

```
开发领任务 task-<id>
   │
   ├─ ① 约定：开 DocPanel / agent 先读 spec + api
   ▼
写代码 / 测试（必须 cite FR-n）
   │
   ├─ ③ 本地 hook：diff 中 FR-n 必须全部存在于「已批准」requirement
   ▼
git push
   │
   ├─ ② pre-push hook：task-<id> 的 spec/api 必须 approved
   │     不通过 → 拒绝 push（除非 --no-verify）
   ▼
主干（代码已隐含「文档被 consult」）
```

## 5. 取舍与推荐组合

| 杠杆 | 强制力 | 机器校验 | 成本 | 角色 |
|------|-------|---------|------|------|
| ① surfacing | 无 | 否 | 低 | 降阻力 |
| ② push gate | 强 | 是 | 中 | 保底线（交付物） |
| ③ FR 可追溯 | 强 | 是 | 中 | 保底线（需求文档） |

- 单看：① 最省事但无强制；② 强但需可达性（本地钩子最优）；③ 强且精准逼读需求文档。
- **推荐：② + ③ 双机器门控为骨架（不靠自觉），① 约定 + 文档链接降阻力**。三层叠加后，
  「不读文档」≈「过不了 push / 写不出合法 FR」，只剩刻意 `--no-verify` 一条缝，配合分支保护可堵。
- 不要只靠 advance 门控 + force：它卡不住「已批准但没读」的实施人。

## 6. 落地步骤（决策后执行）

1. `AGENTS.md` 增加「开发前必读」节 + PR/commit 体 `task:<id>` 约定。
2. 写 `scripts/pre_push_doc_gate.py`：解析分支名、查 `doc_statuses`、校验 `spec`/`api` approved；
   写 `scripts/check_fr_trace.py`：扫描 diff、比对「已批准 requirement」的 FR 集合。
3. 在 repo 装 `.git/hooks/pre-push`（或用 husky / lefthook 管理，避免手动装）。
4. （可选）taskhub 可达部署后加 GitHub Actions 服务端兜底。
5. 说明补进 `HOWTO.md` 与相关 skill。

## 7. 风险与未覆盖

- 本地钩子依赖 hub 在跑；hub 挂了钩子应**放行并告警**（避免卡死开发），而非硬失败。
- `--no-verify` 绕过：靠团队纪律 + 分支保护（禁止强推 / 禁止带 `--no-verify` 的合并）。
- agent 侧：`AGENTS.md` 约定对「不读规则的 agent」无效，需在 agent 框架的 system prompt
  强制——你 MCP 工具已齐备（`taskhub_read_document` 等），加一句规则即可。
- 不覆盖「读了但理解错」——那是 code review 的职责。
- 现有 `advance_stage` 门控的 `force` 绕过、UI 无法触发门控等既有问题仍建议同步修，
  但即使不修，② 的 push gate 已是更强兜底。

## 8. 落地状态（2026-09-21）

已落地 ① + ② + ③ 的脚本与钩子（纯标准库，无第三方依赖），以及 agent 侧（MCP）强制规则：

- `scripts/doc_gate_common.py`：解析任务 id、查 taskhub、抽 FR-n、取 diff 等共用逻辑。
- `scripts/check_doc_approved.py`（②）：校验任务 `spec`/`api` 的 `doc_statuses[kind].state == 'approved'`。
- `scripts/check_fr_trace.py`（③）：扫描待推送 diff 新增行里的 `FR-n`，比对 `GET /tasks/{id}/doc?kind=requirement` 正文中的 FR 集合；缺失则阻塞。
- `scripts/git-hooks/pre-push`（bash 入口）+ `scripts/git-hooks/pre_push_runner.py`（主体，读 stdin ref 行、串起 ② ③）。
- `scripts/install_hooks.py`：拷贝 `pre-push` 到 `.git/hooks/` 并赋可执行位（已实际执行，钩子已装入）。
- `tests/test_doc_gate.py`：10 项纯逻辑测试（分支解析 / FR 抽取 / ② 放行·阻塞·不可达 / ③ 缺失·全在·未引用），全部 PASS。
- `AGENTS.md` 已加「开发前必读」节：`task-<id>` 分支约定、`taskhub_read_document` 读 spec/api、`FR-n` 引用、`git push --no-verify` 提示、`install_hooks.py` 安装。
- **agent 侧 system prompt 规则（2026-09-21 追加）**：`mio_taskhub/mcp_server.py` 的 `FastMCP(instructions=...)` 新增「文档强制 consult（开发前必读）」段——领取任务后写码前必须 `taskhub_read_document` 读 spec/api 与 requirement 的 FR-n、写码引用 FR-n、`task-<id>` 分支命名触发本地 pre-push 门控、spec/api 未批准先推进生命周期。**顺带修复一个真 bug**：`_tool` 装饰器原本忽略 `desc` 参数，导致全部工具的精心撰写描述对 agent 不可见；改为 `mcp.tool(..., description=desc, ...)` 后，这些描述（含 claim/read_document 上的读文档规则）现已对 agent 生效。`test_mcp_server.py`（40 项）+ `test_doc_gate.py`（10 项）共 45 项 PASS，无回归。MCP server 由 agent 以 venv 源码运行（`.venv/Scripts/python.exe -m mio_taskhub.mcp_server`），**改动下次 agent 会话即生效，无需重建 exe**。

### 8.1 修复与增强（2026-09-22）

上一版存在**功能性缺陷**，本轮修复并扩展：

- **task id 解析修复（致命）**：真实 id 是 8 位十六进制（`b3f970b4`），旧正则 `task[-_/]?(\d+)`
  只认数字 → `task-b3f970b4` 匹配不到（跳过门控）、`task-2f5db835` 被截成 `2`（查错任务）。
  改为 `\btask[-_/]?([0-9a-fA-F]{6,32}|\d+)`（hex 优先 + 向后兼容纯数字 + `\b` 防词内误判）。
- **plan 纳入门控**：`check_doc_approved.py` 的 kinds 从 `spec,api` 扩为 **`spec,api,plan`**
  （可用 `MIO_DOC_GATE_KINDS` 覆盖），与后端 `LIFECYCLE_GATE`（design=spec+api、planning=plan）对齐。
- **语义对齐后端**：由「未登记也阻塞」改为**「未登记任何文档→放行告警；已登记→要求全部 approved」**，
  与 `task_stages._check_lifecycle_gate` 的向后兼容语义一致（否则历史任务全部无法 push）。
  可用 `MIO_DOC_GATE_STRICT=1` 强制「未登记也须批准」。
- **多仓库批量安装**：`install_hooks.py` 支持 `--repo <path>`（可重复）/ `--scan <root>`（递归找
  git 仓库，跳过 node_modules/.venv/dist 等）/ `--dry-run`；生成的 pre-push 把 mio-taskhub
  根**绝对路径写死**，从任意仓库 push 都能定位 runner；修正 root 是仓库时不下钻、重复计数的 bug；
  Windows GBK 控制台 emoji 崩溃改为 ASCII 标记 + stdout/stderr 切 UTF-8。
- **实测**：`tests/test_doc_gate.py` 扩到 **15 项**（含 hex id 解析、plan 门控、未登记放行、STRICT 阻塞、
  `render_pre_push` 内容、`find_repos`）全 PASS；`test_mcp_server.py` 40 项无回归（共 50 passed）。
  以 `sh .git/hooks/pre-push` 在 `desktop-agent` 仓库实测跨仓库调用：默认放行(exit 0)、`MIO_DOC_GATE_STRICT=1`
  阻塞(exit 1)。已 `--scan E:/work/code/agent-dev` 装入 **9 个仓库**的 `.git/hooks/pre-push`。

**待办 / 未做**：GitHub Actions 服务端兜底未做（依赖 taskhub 可达部署）。`--no-verify` 绕过仍需团队纪律 / 分支保护兜底。plan 条目/subtask 与实现一致性的机器校验（"实现是否按 plan 走"）尚未做——当前只保「plan 已批准 + FR 可追溯」。

### 8.2 Read Evidence（run 级读取留痕 + submit 前置门控，2026-09-22）

前面 ①②③ 保的都是「文档**存在且被批准**」，保不了「实施人/agent **真的读过**」。本轮把"读过"变成**可校验事实**，并放到 **submit 服务端**（`git push --no-verify` 也绕不过）。

- **数据模型**：`models.ReadEvidence`（表 `readevidence`）——`task_id / run_id / agent_name /
  document_kind / document_version（内容 sha256 指纹）/ requirement_ids（FR-n）/ read_at`；
  一个 `(run_id, document_kind)` 一行（写入端 upsert + 迁移加唯一索引）。
  **绑定 `run_id` 而非 `task_id`**：回答「这次执行读没读」，不是「这个任务以前有没有人读过」。
- **核心模块** `mio_taskhub/read_evidence.py`：路径解析 / 内容指纹 / FR 抽取 / required kinds /
  `record_read` / `check_read_gate` / `build_claim_context`。
- **读取即留痕**：`GET /tasks/{id}/doc?kind=&run_id=&agent=` 传 `run_id` 时落 evidence；
  MCP `taskhub_read_document` 增加 `run_id`/`agent` 参数。
- **claim 内联**：`POST /tasks/claim` 返回 `branch / required_reads / required_fr / documents`（预览）。
  **内联 ≠ 已读**——不产生 evidence，agent 仍须 `read_document(run_id=...)`。这是刻意的：
  防止「只调 claim 就被认为已读」。
- **submit 前置门控**：`POST /runs/{id}/result` 成功路径校验 required reads（存在 + 指纹一致），
  缺失 → `missing`、文档被改动 → `stale`，任一非空 → 422。失败提交不受门控。
- **查询端点** `GET /runs/{id}/read-evidence` + MCP `taskhub_read_status`：提交前自查差哪些。
- **放行原则**（不卡死）：任务未登记这些 kind 或文件缺失 → 不纳入 required；`MIO_READ_GATE=0` 整体关闭；
  `MIO_READ_GATE_KINDS` 改要求项（默认 `spec,api,requirement`）。
- **边界（刻意不做）**：只证明「产出结果前获取过规定版本的规范材料」，**不证明 agent 理解了文档**
  （工具调用证据 ≠ 理解证据，后者几乎不可验证）。

**测试**：新增 `tests/test_read_evidence.py`（12 项：指纹/FR/required、record upsert、gate missing→stale→passed、
env 关闭、claim 内联、API e2e 未读 422→读后 200→改动后 stale）；`test_mcp_server.py` 增 1 项 MCP 链路。
相关回归 `test_mcp_server / test_read_evidence / test_task_doc_paths / test_documents_related /
test_stage_lifecycle_gate / test_models / test_m1_migration / test_api / test_heartbeat / test_transitions /
test_stage_move / test_timeout_misjudge / test_integration / test_task_trace / test_projection / test_middleware`
共 **234 passed**。

**注意**：MCP server 以 venv 源码运行（工具改动下次 agent 会话生效）；**hub HTTP API 需重启/重建 exe**
后 `/doc?run_id=`、`/runs/{id}/read-evidence`、claim 内联、submit 门控才在运行实例上生效。
