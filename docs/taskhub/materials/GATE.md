# 素材库第一层门禁：数据质量（2026-10-29）

> 对应裁定：编号 13。第一层立即执行，失败阻止发布；第二层下游效用实验暂不启动。

## 1 边界

第一层只证明**数据质量与系统正确性**：

- 素材能逐字回源；
- 切分满足原子性规则；
- 重复、孤儿、版本和幂等问题受控；
- 来源分组与佐证计数一致；
- 时效字段符合 TTL 规范；
- 处理过程可审计。

它**不证明**：检索能命中关键证据、召回不夹杂无关内容、下游 Agent 因此做出更好决策。这些属于第二层，当前**未测量**。

## 2 检查项

实现：`scripts/materials_gate.py`；退出码 0 通过，1 阻塞。

| 组 | 检查 | 失败含义 |
|---|---|---|
| 审计 | `audit:I1`–`I16` | 孤儿、主 witness 数、quote 一致性、occ/corr、witness/raw 版本、locator 版本、extract 指针、版本区间 |
| 回源 | `structural:verbatim` | locator 切片 ≠ witness.quote_exact ≠ material.quote |
| 原子性 | `structural:atomic` | 原子来源 >240 字、>2 句末标点、≥3 编号项 |
| 结论 | `structural:decision_shape` | decision <10 字或以冒号结尾 |
| 裸计数 | `structural:bare_count` | result 只是 `67 passed` / `failed=25` |
| 标记 | `structural:marker_leak` | quote 以 `①` / `1)` / `1.` / `-` 开头 |
| 去重 | `duplicates:*` | 素材键、raw 版本、witness、extract 重复 |
| 隔离 | `isolation:*` | witness/source 的 `independence_group` 为空或版本非法 |
| 时效 | `freshness:expires_consistency` | `expires_at` ≠ `last_seen_at + TTL`，或 `observed_at` 晚于 `last_seen_at` |
| 状态 | `audit:raw_status_*` | 非法 extract 状态、rejected 无原因、extracted 无 witness、skipped 有 witness、raw 无 source |
| 指标 | `freshness:expired_active` | **非阻塞**：已过期但仍 active 的数量（副本无后台 stale job） |

## 3 当前结果

| 库 | 结果 | 说明 |
|---|---|---|
| `rehearsal_p52_20261028.db`（当前代码） | **PASS**（退出码 0） | 1,378 素材 / 1,375 active / 3 quarantine；17 条过期 active 仅为指标 |
| `rehearsal_p51b_20261028.db`（P5.1） | **FAIL**（退出码 1） | `structural:bare_count`：已知缺陷 `failed=25` 被门禁捕获 |

报告：`calibration/gate-p52-20261028.json`、`calibration/gate-p51b-20261028.json`。

第二行是门禁自身的验收证据：它不是只会盖章，能对旧版本稳定报错。

## 4 使用

```bash
python scripts/materials_gate.py --db <db> --out <report.json>
```

发布流程：

1. 在副本上回填（生产重跑仍需单独审批）；
2. 跑第一层门禁；
3. `ok: true` 才允许进入发布评审；`ok: false` 直接阻止；
4. 门禁报告随发布证据归档。

## 5 第二层（暂不启动）

下游任务效用实验的前提，缺一不可：

- 选定一个真实任务与固定的任务集；
- 固定模型、提示词与工具配置；
- 控制随机性（固定 seed / 温度），重复运行；
- 预先定义主要指标（成功率、证据命中率、错误率、成本）；
- 使用同一任务集做「有素材 / 无素材」对照，而不是比较两批不同难度的任务；
- 报告置信区间与失败样本，不只报平均成功率。

在满足这些条件前，**真实可用率保持“未测量”**。0.40 / 0.65 仍用于 active / quarantine 的可见性分层，但那是启发式过滤参数，不是经过验证的质量准入阈值，也不冻结。
