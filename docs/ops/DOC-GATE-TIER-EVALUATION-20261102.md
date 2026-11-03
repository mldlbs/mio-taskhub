# 门禁三档评估（2026-11-02，2026-11-03 更新）

> 背景：豁免收敛后，活跃任务不再有显式豁免；但「没登记文档就放行」仍然让门禁 fail-open。
> 2026-11-03：方案 B 已实施（light 登记文档必须 approved）；「无文档」是否 fail-closed 仍待决策。

## 1 当前实际生效的规则

| 路径 | 条件 | 生效门禁 |
|---|---|---|
| 旧任务 | `gate_tier_reason=''` | 目标阶段前的所有文档必须到 required 状态（requirement/spec/api approved、plan approved）；质量分在 `set_doc_status` 已强制 ≥80 |
| light（新） | `reason` 非空且 tier=light | **已登记文档必须 approved**；未登记放行（2026-11-03 起） |
| standard（新） | `reason` 非空且 tier=standard | 已登记文档必须 approved（2026-11-02 起） |
| high | tier=high | 人工确认（字段存在但生产 `human_confirmed_at=0`，从未使用） |

阶段产物要求独立于 tier：进 design 必须有 spec 文件、进 planning 必须有 plan 文件、进 implementing 必须有 changelog、进 done 必须有 review。也就是说，**文件必须存在，但内容可以是 0 分**。

## 2 影响面

| 分组 | 数量 | 有任意文档 | 有 requirement | 无文档 |
|---|---|---|---|---|
| 活跃（QUEUED/CLAIMED/RUNNING） | 45 | 20 | 13 | 25 |
| 新路径（reason 非空） | 32 | 7 | 2 | 25 |

活跃任务的文档状态：requirement approved 4、draft 8；spec approved 7、draft 3、无状态 7；api 无状态 8、draft 2、review 1。

## 3 已改：standard 与 light 都不再「只查文件存在」

`_check_lifecycle_gate` 的 standard / light（`reason` 非空）分支统一为：

1. 登记的文档文件必须存在；
2. 登记的文档状态必须达到 required（design 前 spec/api approved 等）。

生产影响（只读副本推演，2026-11-03）：

| 结果 | 数量 | 说明 |
|---|---|---|
| standard 被拦 | 1 | `976be80e` requirement/spec/api 仍 draft |
| light 登记文档、放行 | 5 | 都在 review→done，登记项已满足 |
| 无文档、不受影响 | 25 | 仍 fail-open |

新增测试：`test_stage.py::test_standard_tier_requires_approved_doc`、`test_light_tier_registered_doc_must_be_approved`、`test_light_tier_without_docs_still_passes`、`test_light_tier_registered_missing_file_blocks`、`test_doc_registered_in_same_move_does_not_gate_that_move`、`test_unresolvable_doc_path_fails_open`。

### 3.1 同次登记的文档不参与本次校验

`_check_lifecycle_gate` 新增 `skip_kinds`：本次请求通过 `spec_path` / `plan_path` / `doc_paths` 新登记的 kind 不参与本次校验，否则「进 design 时才登记 spec」会被「spec 必须已 approved」永久卡死。该文档在下一次推进时开始受门禁约束。

workspace 缺失或路径不可解析时沿用既有惯例 fail-open（与 `_doc_written` 一致），不把「无法校验」当成「未审批」。

## 4 已决策：方案 B；方案 A/C 现状

### 方案 A：维持现状（未采用）

light 继续完全跳过；无文档继续放行。

- 影响：0
- 风险：门禁只对「已经登记文档」的任务有效，对绝大多数新任务无效；等于没有默认约束。

### 方案 B：light 登记了文档就必须 approved（**已于 2026-11-03 实施**）

只改一条：light 不再直接 `return []`，而是对**已登记**的 kind 走状态门禁；没登记仍然放行。

- 影响：当前 7 条新路径任务有文档；实施后 1 条 standard 被拦、5 条 light 放行、25 条无文档不受影响。
- 风险：低。已有文档的任务本来就在写文档，只是要求它走完审批。
- 代价：agent 必须先把 spec/api 推到 approved 才能进 design，不能拿 draft 直接开工。

### 方案 C：所有新任务必须先有 requirement（fail-closed）

在 ready → implementing 增加 requirement approved 门禁；没登记就拦。

- 影响：活跃任务中 25 条无文档的会被拦。
- 风险：中。会把「先干起来再补文档」完全堵死，需要同时提供 scaffold/自动生成 requirement 的路径。
- 建议：先跑 B 两周，观察卡单率，再决定是否上 C。

## 5 建议的度量

| 指标 | 用途 |
|---|---|
| 每日被门禁拦下的阶段推进次数 | 判断门禁是否真的在拦，而不是只写在代码里 |
| 被拦任务的补文档耗时 | 判断成本是否可接受 |
| 登记文档的任务占比 | 判断覆盖面上限 |
| `force=true` 使用次数 | 判断是否存在绕过惯性 |

没有这些埋点，三档门禁会重蹈「0 活跃告警」的覆辙：规则存在，但没人知道它有没有工作。

## 6 结论

- standard 与 light 的「已登记文档」都已收紧为必须 approved；生产影响 1 条 standard 被拦、5 条 light 放行。
- 「无文档」仍 fail-open；是否按方案 C fail-closed 需单独决策，且应先有 scaffold/自动生成 requirement 的路径。
- 读证侧保持现状：required reads 由 tier 推导、claim 内联正文、版本变更 422 已生效；边界强制与覆盖率指标仍未做。
