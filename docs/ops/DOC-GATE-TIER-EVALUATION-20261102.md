# 门禁三档评估（2026-11-02）

> 背景：豁免收敛后，活跃任务不再有显式豁免；但「没登记文档就放行」仍然让门禁 fail-open。
> 本文只评估，不改变 light 与「无文档」策略。

## 1 当前实际生效的规则

| 路径 | 条件 | 生效门禁 |
|---|---|---|
| 旧任务 | `gate_tier_reason=''` | 目标阶段前的所有文档必须到 required 状态（requirement/spec/api approved、plan approved）；质量分在 `set_doc_status` 已强制 ≥80 |
| light（新） | `reason` 非空且 tier=light | **完全跳过**生命周期门禁 |
| standard（新） | `reason` 非空且 tier=standard | 原为「登记文件存在即可」；**本次改为必须 approved** |
| high | tier=high | 人工确认（字段存在但生产 `human_confirmed_at=0`，从未使用） |

阶段产物要求独立于 tier：进 design 必须有 spec 文件、进 planning 必须有 plan 文件、进 implementing 必须有 changelog、进 done 必须有 review。也就是说，**文件必须存在，但内容可以是 0 分**。

## 2 影响面

| 分组 | 数量 | 有任意文档 | 有 requirement | 无文档 |
|---|---|---|---|---|
| 活跃（QUEUED/CLAIMED/RUNNING） | 45 | 20 | 13 | 25 |
| 新路径（reason 非空） | 32 | 7 | 2 | 25 |

活跃任务的文档状态：requirement approved 4、draft 8；spec approved 7、draft 3、无状态 7；api 无状态 8、draft 2、review 1。

## 3 本次已改：standard 不再「只查文件存在」

`_check_lifecycle_gate` 的 standard 分支改为：

1. 登记的文档文件必须存在；
2. 登记的文档状态必须达到 required（design 前 spec/api approved 等）。

生产当前只有 1 条 standard 任务（`976be80e`，隧道内涝分级预警）。变更后它从 brainstorming → design 会被拦，因为 requirement/spec/api 都还是 draft。该任务本来就停在队列数周，无新增业务阻塞。

新增测试：`test_stage.py::test_standard_tier_requires_approved_doc`。

## 4 待决策：light 与「无文档」怎么办

### 方案 A：维持现状

light 继续完全跳过；无文档继续放行。

- 影响：0
- 风险：门禁只对「已经登记文档」的任务有效，对绝大多数新任务无效；等于没有默认约束。

### 方案 B：light 登记了文档就必须 approved（推荐先做）

只改一条：light 不再直接 `return []`，而是对**已登记**的 kind 走状态门禁；没登记仍然放行。

- 影响：当前 7 条新路径任务有文档，未来所有登记文档的 light 任务生效。
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

- standard 档已收紧，影响 1 条任务。
- light 档建议按方案 B 收紧；「无文档」是否 fail-closed 建议按方案 C 单独决策。
- 读证侧保持现状：required reads 由 tier 推导、claim 内联正文、版本变更 422 已生效；边界强制与覆盖率指标仍未做。
