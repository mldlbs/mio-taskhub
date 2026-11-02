# 门禁与读证：数据核验（2026-11-02）

> 目的：核验「`doc_gate_exempt` 182/193（94%）、`238974e3` spec 40 分仍 done、`completion_run_id` 全空、ReadEvidence 29/167、insight 10/10 全失败、UI 278 vs 库 193」这组指控，判断是否要据此改代码。
>
> 结论：**指控的现象部分为真，但引用的数字不来自本机任何生产库或备份；根因也与「94% 豁免」不同。**

## 1 数据源核验

生产库：`C:\Users\admin\.mio_taskhub\taskhub.db`（41.2 MB）。

| 项目 | 指控 | 生产库实测 |
|---|---|---|
| 任务总数 | 193 | **1,977** |
| 状态分布 | CANCELLED 81 / COMPLETED 75 / FAILED 29 / QUEUED 8 | CANCELLED **210** / COMPLETED **194** / FAILED **1,528** / QUEUED **45** |
| `doc_gate_exempt=1` | 182 / 193（94%） | **383 / 1,977（19.4%）** |
| `completion_run_id` 非空 | 0 / 193 | **0 / 1,977**（这一条为真） |
| ReadEvidence | 29 行 / 167 run | **152 行 / 68 run / 53 任务** |
| run 总数 | 167 | **5,213** |

覆盖范围：本机全部 24 个 taskhub 库副本（生产 + 每小时备份 + 迁移演练副本），任务数区间 **1,931–1,977**，最早备份 2026-10-21。**没有任何一个库出现过 193 个任务、29 条读证或 167 个 run 的组合。**

`238974e3` 在生产库和全部备份中均不存在；无法复核「spec 40 分仍 done」。

「UI 278 vs 库 193」的 278 是当前交付面口径（`observability/scope.py`），不是全量；把 278 当全量、把 193 当库内总数，两个口径都错了。库内 `CANCELLED` 是 210，不是 81；`81/75/29/8` 与任何 project 的分布都不吻合（`agent-dev` 的 COMPLETED 75 / FAILED 29 恰好相同，其余不符）。

## 2 insight 任务

指控：10 个 `[insight] taskhub_task_failure_rate/success_rate` 全部 FAILED，每天两条自动生成、自动失败。

实测：库内共 20 条 insight 任务，**18 COMPLETED / 2 FAILED**；最近两条是 2026-11-02 06:39 和 06:43 完成。失败集中在 10-21 与 10-27，不是「持续每天两条全失败」。监控自造失败的说法不成立（至少当前不成立）。

## 3 门禁为什么确实拦不住（真实根因）

### 3.1 `completion_run_id` 全空：已定位并修复

`submit_result → check_submit` 原本只在 `issues` 为空时写 `completion_run_id`。生产 `MIO_COMPLETION_GATE` 默认为 `warn`，且每个成功提交都带一条 `gate.verification_missing`：

- 任务有 `acceptance_criteria` 时，即使 light tier 也要求 `evidence.verification.commands/tests`；
- 实际提交只带 `artifacts`（如 `docs/taskhub/<id>/review.md`），因此永远缺机器验证；
- warn 不阻断，但也不写 run_id → 1,977 条任务全部为空。

用生产副本重放最近 8 个成功 run，**8/8 都是 `gate.verification_missing`**，其中 1 条还叠加 `gate.deliverable_missing`。

本轮修复：

| 改动 | 位置 |
|---|---|
| 成功提交后按实际完成的 run 写回 `completion_run_id`（warn/off 也写） | `api/runs.py::_handle_success` |
| warn 模式把 `gate_mode` 和 `gate_issues` 写入 `run.evidence`，事后可归因 | `completion_gate.py::check_submit` |
| 新增 2 个回归测试（warn 写回、off 仍写回） | `tests/test_completion_gate.py` |

### 3.2 文档门禁对新任务不生效

`_check_lifecycle_gate` 有两条快路径：

```python
if tier_reason and tier == "light":
    return []                       # 新任务默认走这里，完全跳过
if tier_reason and tier == "standard":
    ... 只检查「已登记文件是否存在」，不检查 approved，也不检查质量分
```

`gate_tier_reason` 为空的旧任务才走严格路径（requirement/spec/api approved + 质量分 ≥80）。

生产分布：

| gate_tier / reason | 数量 | 走的门禁 |
|---|---|---|
| light / `''` | 1,945 | 严格状态门禁（但绝大多数已是终态） |
| light / `default` | 31 | **完全跳过** |
| standard / `spec_registered` | 1 | 只查文件存在 |

所以「门禁从没拦住过」这个结论**是对的**，但机制不是 94% 豁免，而是：**Phase 1 之后创建的任务默认 light，直接跳过文档生命周期门禁；standard 也只查文件存在。** 阶段推进仍要求产物文件存在（design 要 spec、planning 要 plan），但文件可以是 0 分。

`doc_gate_exempt` 实际分布：CANCELLED 169 / COMPLETED 132 / FAILED 57 / QUEUED 25。按活跃口径，**45 条 QUEUED 中有 25 条豁免（55.6%）**——要收敛应针对这 25 条，而不是 182 条。

### 3.3 读证门禁

按当前规则在生产副本上回放：

| 指标 | 数量 |
|---|---|
| 无 required reads 的任务 | 1,862 |
| 有 required reads 的任务 | 115 |
| 其中有成功 run 的 | 57 |
| 读证齐全 | 38 |
| 缺读证或版本过期 | 19 |

115 条有要求的任务全部来自已登记文档的旧任务；新任务默认 light 只要求 `requirement`，多数根本没登记文档，所以读证门禁覆盖面很窄。152 条证据证明机制能工作，但覆盖不到新任务。

## 4 对指控的逐条裁决

| 指控 | 裁决 | 依据 |
|---|---|---|
| 94% 豁免导致门禁失效 | **数字不成立**，但「门禁失效」成立 | 豁免 19.4%；新任务是 light 直接跳过 |
| `238974e3` spec 40 分仍 done | **无法复核** | 该 ID 在全部库中不存在 |
| `completion_run_id` 全空 | **成立** | 0/1,977，根因已定位并修复 |
| ReadEvidence 29/167 | **不成立** | 152 行 / 68 run / 53 任务 |
| insight 10/10 全失败 | **不成立** | 18 COMPLETED / 2 FAILED |
| UI 278 vs 库 193 | **不成立** | 278 是交付面口径，库内 1,977；CANCELLED 210 |

## 5 下一步建议（待确认）

保持「先修小口子、再改策略」的顺序：

1. **已完成**：run_id 写回 + warn 拦截原因入证据。
2. **待授权（生产数据）**：清除 25 条活跃 QUEUED 任务的 `doc_gate_exempt`，然后观察是否出现卡单。
3. **待规格（策略变更）**：门禁三档
   - light：登记了文档就必须 approved，不再直接放行；
   - standard：spec/api 必须 approved 且质量分 ≥80，不再只查文件存在；
   - high：保留人工确认。
4. **待规格（读证链）**：required reads 由 tier 推导（已实现）、claim 内联正文（已实现）、版本变更重读（已实现）、边界强制（未做）、读取覆盖率指标（未做）。

第 3 项会把默认路径从「不拦」改成「拦」，影响所有新任务，需单独确认后再动。
