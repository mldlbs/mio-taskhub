# mio-taskhub 代码质量验收能力审计（quality-audit）

- **审计对象：** mio-taskhub 现有的代码交付验收能力
- **审计日期：** 2026-10-10
- **基线：** commit `1e7d00e`，分支 `task-516bd0c7`
- **方法：** 源码静态核查 + 可复现实验（`scripts/audit_quality_gaps.py`，17 个探针）
- **证据等级：** 已证实 / 已复现 / 待核查 / 设计建议（沿用方案 §5.3）
- **复现命令：** `.venv/Scripts/python.exe scripts/audit_quality_gaps.py`

> 本报告只回答一个问题：**现有机制能不能拦住「不合格的代码被交付」。**
> 不评价代码风格，不提改造设计（改造优先级见 `quality-plan-review-20261010.md` §4）。

---

## 1. 结论摘要

| 判定 | 数量 | 含义 |
|---|---:|---|
| **缺** | 8 → **3** | 期望阻断，实际放行 —— 缺口成立 |
| **有** | 6 → **11** | 期望阻断，实际阻断 —— 机制有效 |
| **绕** | 4 | 存在已知绕过路径（设计上可接受，但需约束与留痕） |

> **修复状态（2026-10-10 已落修）**：P0-1 / P0-2 / P0-4 已修，探针复跑 `缺 8 → 缺 3`。
> 剩余 3 项（A1、A2、B1）属能力建设（变更识别 / 测试执行 / 证据覆盖代码），非缺陷修复，
> 需独立设计。详见 §9。

**一句话结论：**

> mio-taskhub 的门控集中在**文档链**上，且在文档链上做得扎实；
> 但一旦谈到「代码」——实际改了什么、测试跑没跑、谁审的——**系统基本不设防（3 项缺口）**。
> 另有 **2 处「门控存在但对常态场景失效」的实现缺陷**（D1、D4）已于 2026-10-10 修复。

---

## 2. 缺口清单（按修复优先级）

### P0-1 文档生命周期门控对全部新任务失效 —— 已复现

| 项 | 内容 |
|---|---|
| 缺口 | `doc_statuses` 为空时，`LIFECYCLE_GATE` 对 `spec`/`api`/`plan` **完全不校验**，未批准即可推进 |
| 实测 | 新建任务从 `brainstorming` → `design` → `planning` → `ready` 全程 200，`doc_statuses={}`，**无任何文档 approved**（探针 D4） |
| 代码位置 | `api/task_stages.py:82-121`。`STRICT_KINDS = {"requirement"}`（:73）—— 只有 `requirement` 在「已跟踪但未设状态」时被拦；`spec`/`api`/`plan` 走 `continue`（:99-100） |
| 为什么没被发现 | `_check_lifecycle_gate` 的 `tracked = bool(statuses)` 判断，把「历史任务」和「新建任务」当成同一类 |
| 影响 | AGENTS.md 声称的「进 design 需 spec/api 已 approved」在**常态路径上从未生效**；spec/api 文档生命周期形同建议 |
| 建议 | 区分「从未登记」与「显式登记但未批准」：新任务进入 `design` 时若 `spec` 无状态，应提示或阻断，而非静默放行 |

**对照验证（证明门控本身是好的）**：一旦把 `spec` 显式设为 `draft`，再进 `design` 立即被 422 拦截（探针 D2b）。**问题在触发条件，不在门控逻辑。**

### P0-2 拖拽路径自动伪造审查结论 —— 已复现

| 项 | 内容 |
|---|---|
| 缺口 | `stage/move`（拖拽路径，`strict=False`）在无任何审查证据时，自动把 `review_result` 填成 `"（拖拽完成）"` 并放行进入 `done`（探针 D1） |
| 对照 | `stage`（advance 路径，`strict=True`）同一场景正确返回 422 `done stage requires review_result or a review document`（探针 D1b） |
| 代码位置 | `api/task_stages.py:181-184` —— `else: text = "（拖拽完成）"` |
| 影响 | **同一门控两条路径强度不一致**。且写入的是一条「看起来像审查结论」的假记录，事后无法从数据上区分「真审查」与「拖拽自动填」 |
| 建议 | 至少给该默认值打标记（如 `review_auto_filled=true`），或在 `done` 门控上要求两条路径一致 |

### P0-3 交付提交不要求任何变更或测试证据 —— 已复现

| 项 | 内容 |
|---|---|
| 缺口 1 | `POST /runs/{id}/result` 只带 `success: true` 即 `completed`。提交体**无需**提供变更文件清单、测试命令、测试输出、代码版本（探针 A1） |
| 缺口 2 | 提交后自动 `completed → review`，期间**无任何代码质量判定**；Agent 自报的 `success` 布尔值被直接采信（探针 A2） |
| 代码位置 | `api/runs.py:239-286`（成功路径校验项：①所有权 ②状态可提交 ③ReadEvidence 版本一致）、`_handle_success` `runs.py:289-336` |
| 已核实 | 全仓无 `git diff` / `changed_files` 逻辑；无 pytest / 测试执行逻辑。`test` 是一种**文档 kind**，不是 runner |
| 影响 | 这是整条链上**最大的可信度缺口**：系统采信的是 Agent 的自我声明，而非代码事实 |

### P0-4 评审环节无身份隔离、无内容下限 —— 已复现

| 项 | 内容 |
|---|---|
| 缺口 1 | `reviewer` 为执行该 run 的 agent 本人时，`approve` 成功（探针 A3，`reviews.py:66-107`，`reviewer=body.get("reviewer","")`，`models.py:206`） |
| 缺口 2 | `summary` 为 1 个字符 `"x"` 即写入 `review_result`，并满足 `done` 门控（探针 A4） |
| 影响 | 方案 §6.5「独立验收」当前不成立：自审 + 一句话结论即可解锁交付 |

### P1 读证指纹不覆盖代码 —— 已复现

| 项 | 内容 |
|---|---|
| 缺口 | 读证完成后新增代码文件 `impl.py`，evidence 指纹未失效、submit 仍 200（探针 B1） |
| 代码位置 | `read_evidence.py:65-76` —— 只对 `doc_paths` 里的**文档**求 sha256 |
| 对照 | 读证后修改**文档** `api.md` → submit 被 422 拦截（探针 B2，机制有效） |
| 结论 | 指纹机制本身正确，但**覆盖面仅限文档**。「证据完整」目前只等于「读过文档且文档没变」，与代码可信度无关 |

---

## 3. 已证实有效的机制（正面结论）

| 探针 | 机制 | 实测结果 | 代码位置 |
|---|---|---|---|
| B2 | 文档改动后读证失效 | 422 拦截，`stale: ["api"]` | `read_evidence.py:161-191` |
| B3 | 读证按 run 隔离 | 新 run 未读即 submit → 422，`missing: ["spec","api","requirement"]` | 同上，evidence 绑定 `run_id` |
| C3 | MCP 通道拒 destructive | `set_doc_status` 默认 `allowed=False` | `mcp_risk.py:60-76` |
| D1b | advance 路径的 done 门控 | 无审查证据 422 拒绝 | `api/task_stages.py:314` |
| D2b | 已登记文档的 lifecycle 门控 | `spec=draft` 时进 design 被 422 | `api/task_stages.py:82-121` |
| D3 | 文档状态严格向前 | `review → draft` 回退被 422 | `doc_lifecycle.py:93-111` |

**这 6 项是本系统真正「守得住」的部分**，也是 P0 改造时应复用的调用点模式（在服务端、默认开、绕过需显式开关）。

---

## 4. 已知绕过路径

均为**设计已知**（非缺陷），但方案 §7.4 要求登记，且当前无任何审计约束：

| 探针 | 绕过方式 | 实测 | 留痕情况 |
|---|---|---|---|
| C1 | `MIO_READ_GATE=0` | 未读任何文档即可 submit → 200 | 无审计记录 |
| C2 | `MIO_RATCHET_DISABLED=1` | 棘轮关闭，质量分可回退 | 无 |
| C3b | `MIO_MCP_ALLOW_DESTRUCTIVE=1` | 放行全部 destructive（含 `set_doc_status`） | 无 |
| C4 | `force=true` | 空心 spec（质量分 0、缺全部必需章节）仍被推入 review | 事件流有记录 |

- **C4 是唯一留痕的绕过路径**（`forced_gate` 返回被绕过条目并落事件），可作为其他三条的改造模板。
- `git push --no-verify` 可绕过 pre-push 钩子 —— **本轮未测**（需真实 hub 运行 + git 仓库），记为「待核查」。

---

## 5. 门禁七字段表（方案 §7.4）

| 门禁 | 保护目标 | 执行位置 | 输入证据 | 失败行为 | 绕过路径 | 回归测试 | 运行证据 |
|---|---|---|---|---|---|---|---|
| 读证门控 | 实施人动手前读过规��� | `api/runs.py:252-256`（服务端） | spec/api/requirement 的 sha256 | 422 禁提交成功 | `MIO_READ_GATE=0`；文件缺失不纳入强制 | 有 `tests/test_read_evidence.py` | B2/B3 有效，C1 可关 |
| 文档生命周期 | 未批准文档不得进下一阶段 | `api/task_stages.py:82` | `doc_statuses` | 422 | `force=true`；**`doc_statuses` 为空时不校验（P0-1）** | 有 `tests/test_doc_lifecycle.py` | D2b 有效 / D4 失效 |
| 文档质量门 | 空心文档不得进 review/approved | `doc_quality.py` + 状态推进 | 章节/表格/子块 | 422 | `force=true`（C4，有留痕） | 有 `tests/test_doc_quality.py` | C4 |
| 阶段产出物门控 | 无产出物不得进阶段 | `api/task_stages.py:139` | `doc_paths` / `review_result` | 422 | **move 路径 `strict=False` 自动填充（P0-2）** | 有 `tests/test_doc_gate.py` | D1 失效 / D1b 有效 |
| 棘轮基线 | 质量只升不降 | `ratchet.py` | 历史最好值 | 422 | `MIO_RATCHET_DISABLED=1` | 有 `tests/test_ratchet.py` | C2 |
| MCP 风险门控 | Agent 不得自主执行破坏性操作 | `mcp_server._request` 收口 | 方法+路径匹配 | 拒绝 | `MIO_MCP_ALLOW_DESTRUCTIVE=1` | 有 `tests/test_mcp_risk.py` | C3 |
| pre-push 钩子 | 代码进主干前校验文档批准 + FR 引用 | `scripts/git-hooks/pre_push_runner.py` | 分支名 `task-<id>`、diff | 拒绝 push | `git push --no-verify` | 有 `tests/test_doc_gate.py` | **待核查**（需 hub + git） |
| **代码交付验收** | —— | —— | —— | —— | —— | —— | **不存在** |

---

## 6. 本轮未覆盖（待核查）

以下属方案 §7.1/§7.2 的范围，本轮**未做**实验，不应据本报告断言其状态：

| 项 | 未覆盖原因 |
|---|---|
| Agent 进程生命周期（`never_started` vs `agent_offline` 诊断） | 需真实 agent CLI 与长时运行观测 |
| Hub 重启后的幽灵 Run / 重复执行 | 需重启实验 |
| 重试是否遗留旧 Run 证据（B3 只验证了 submit 层隔离，未验证 run 表状态） | 需检查 `late_result_recovery` 与 retry 的交叉 |
| pre-push 钩子实际拦截效果 | 需 hub 在 48620 运行 + 真实 git 仓库 |
| 事件流与状态机一致性（`tests/test_event_consistency.py` 存在但未纳入本轮） | — |

---

## 7. 与项目文档的偏差（需同步修正）

| 位置 | 现有表述 | 实测 |
|---|---|---|
| `AGENTS.md`「进 `design` 现要求 `requirement` 已登记且 approved」 | 表述为已收紧的门控 | 仅对 `requirement` 生效；`spec`/`api`/`plan` 在 `doc_statuses` 为空时不校验（D4） |
| `AGENTS.md`「完全未跟踪（`doc_statuses` 为空）的历史任务向后兼容放行」 | 限定为「历史任务」 | 新建任务同样 `doc_statuses` 为空，该豁免实际覆盖全部新任务 |
| `README.md`「641 用例 / 119 REST / 40 MCP」 | — | 实测 **1227 / 157 / 47** |

---

## 8. 证据清单（可复现）

```bash
cd mio-taskhub
.venv/Scripts/python.exe scripts/audit_quality_gaps.py
```

| 探针 | 验证内容 | 修复前 | 修复后（2026-10-10） |
|---|---|---|---|
| A1 | 无变更/测试证据能否提交成功 | 缺 | **缺（未修，需能力建设）** |
| A2 | submit 后是否有独立质量判定 | 缺 | **缺（未修，需能力建设）** |
| A3 | 执行者本人能否 approve | 缺 | **有** ✅ |
| A4 | approve 结论是否有内容下限 | 缺 | **有** ✅ |
| B1 | 读证指纹是否覆盖代码变更 | 缺 | **缺（未修，需能力建设）** |
| B2 | 文档改动后读证是否失效 | 有 | 有 |
| B3 | 读证是否按 run 隔离 | 有 | 有 |
| C1 | `MIO_READ_GATE=0` 能否关闸 | 绕 | 绕 |
| C2 | `MIO_RATCHET_DISABLED=1` 能否关棘轮 | 绕 | 绕 |
| C3 | MCP 是否默认拒 destructive | 有 | 有 |
| C3b | `MIO_MCP_ALLOW_DESTRUCTIVE=1` 放行范围 | 绕 | 绕 |
| C4 | `force=true` 能否绕过质量门、是否留痕 | 绕 | 绕 |
| D1 | move 路径进 done 是否要求审查证据 | 缺 | **有** ✅ |
| D1b | advance 路径进 done 是否要求审查证据 | 有 | 有 |
| D2 | `doc_statuses` 为空时能否进 design | 缺 | **有** ✅ |
| D2b | `spec=draft` 时能否进 design | 有 | 有 |
| D3 | 文档状态能否回退 | 有 | 有 |
| D4 | 新任务能否无任何文档批准直达 ready | 缺 | **有** ✅ |

**静态核查结论**（与实验互相印证）：

| 结论 | 复现命令 | 结果 |
|---|---|---|
| 全仓无变更识别 | `grep -rn "git diff\|changed_files\|--stat" --include=*.py . \| grep -v ".venv\|git_sync\|/tests/"` | 无命中 |
| 全仓无测试执行 | `grep -rn "run_pytest\|\"pytest\"\|subprocess.*pytest" --include=*.py . \| grep -v ".venv"` | 无命中 |
| 用例总数 | `.venv/Scripts/python.exe -m pytest tests/ --collect-only -q` | `1227/1231 collected (4 deselected)` |
| REST 路由 | `grep -rhoE "@router\.(get\|post\|put\|patch\|delete)\(" mio_taskhub/api/ \| wc -l` | `157` |
| MCP 工具 | `grep -rhoE "def taskhub_[a-z_]+" mio_taskhub/mcp_server.py \| wc -l` | `47` |

---

## 9. 修复记录（2026-10-10 已落修）

范围：P0-1、P0-2、P0-4。**A1 / A2 / B1 未修**（属能力建设，见 §10）。
基线 commit `1e7d00e` → 修复后全量 `1235 passed, 4 failed`（4 个为 pre-existing 的
`test_spinning_guard` webhook_guard，与本次改动无关）。

### P0-1 文档生命周期门控对全部新任务失效

| 位置 | 改动 |
|---|---|
| `models.py` | Task 新增 `doc_gate_exempt: bool = False` |
| `migrations.py` | 加列并把**存量任务全部置 1**（`doc_gate_exempt=1`），保证历史数据行为不变；新任务默认 0 |
| `api/task_stages.py` | ①豁免判定从「`doc_statuses` 是否为空」改为读 `doc_gate_exempt` 显式字段；②新增 `_doc_written()`——**文档已实际落盘**才要求 approved，只登记路径不阻断（保留「先声明产出物位置、边做边写」的工作流）；③门控改为**累计**式 `_gates_upto()` |
| `api/tasks.py` | `doc_gate_exempt` 接入 create / update / detail / list，并可读出以供审计 |

**为什么必须加「累计」**：门控原先按「目标阶段」查表，而 `move`（拖拽）可任意跳转 ——
修完单点后实测 `design` 已被 422 拦下，但紧接着的 `planning` / `ready` 仍 200 直达。
改为取「目标阶段之前所有阶段门槛的并集」后，跳阶段不再等于免检（D4 现全段拦截）。

**为什么用「已落盘」而非「已登记」**：先按「已登记即须批准」实现，实测造成 13 处既有测试回归
——项目日常的「推进阶段时顺手登记产出物路径」流程被堵死。改为落盘判定后回归降到 0，
且仍能堵住真正要防的场景（文档写完了却没批准就进下一阶段）。

### P0-2 拖拽路径伪造审查结论

- `api/task_stages.py`：删除 `else: text = "（拖拽完成）"`，`move` 与 `advance` 两条路径一致 422。
- 前端无需改动：`web/src/stageDocs.js:55-63` 本就在 move 到 done 时弹窗收审查结论，
  留空才触发该分支。

### P0-4 评审无身份隔离 / 结论无内容下限

- `api/reviews.py`：approve 时校验 `reviewer` 是否为本任务最近的执行者，是则 409
  `review.self_approval`；确有需要可显式 `self_review=true` 放行（仍留痕）。
  reject 不受限（自己发现自己做得不行应当允许）。
- approve 结论长度下限 `MIN_APPROVE_SUMMARY = 8`；已登记 `doc_paths['review']` 时豁免该限制
  （文档本身就是审查证据）。

### 新增/更新的测试

| 文件 | 内容 |
|---|---|
| `tests/test_review_gates.py`（新增） | 自审被拒 / 他人可批 / 显式自审放行 / reject 不受限 / 结论过短被拒 / 有 review 文档时豁免 |
| `tests/test_stage_lifecycle_gate.py` | 豁免改为显式字段；新增「已落盘未批准被拦」「仅登记路径不拦」「跳阶段不豁免」 |
| `tests/test_stage_move.py` | `test_move_to_done_lenient_review_result` → `test_move_to_done_requires_review_result`（断言反转：这是**有意移除**的行为） |
| `tests/test_task_doc_paths.py` | 3 处补 `force: true`（本意是验证字段回写/错误文案，非门控）；新增一条门控断言 |

### 未修项（需独立设计，非缺陷修复）

| 探针 | 内容 | 为什么不在本次范围 |
|---|---|---|
| A1 / A2 | 提交不要求变更清单与测试证据 | 需先定义「变更识别」机制（git 基线、diff 归属），涉及 Run 数据模型扩展 |
| B1 | 读证指纹不覆盖代码 | 需决定「代码版本」如何标识（git sha / 内容树），与 A1 是同一件事 |

---

**文档状态说明：** 本文所有「已复现」结论均由 `scripts/audit_quality_gaps.py` 在 commit `1e7d00e` 上跑出，可重跑验证。
第 6 节列出的 5 项为「待核查」，**不应被引用为缺陷或有效结论**。
第 9 节记录修复后的行为，判定列以「修复后」为准。