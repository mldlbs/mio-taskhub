# 素材库 P5 提取器修复与重验证（2026-10-28）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 范围 | `doc.review` 分类、原子切分、`run.failure` 泛化状态、`signal` 主张、回归诊断 |
| 旧样本 | `calibration/sample-20261028.jsonl`（仅回归诊断，不再作独立校准） |
| 新样本 | `calibration/sample-p5-20261028.jsonl`（200 条，来自新提取器副本） |
| 新标签 | `calibration/labels-p5-20261028.jsonl`（规则辅助，非人工金标准） |
| 独立复核 | `calibration/independent-review-p5.jsonl`（17 条，独立 agent） |
| 生产库 | **未变更**；修复在 pre-backfill 副本上验证 |
| 阈值 | **未冻结、未调整** |
| 日志 | 仍为 `trial` |
| 新 exe | 未部署（仍被 `task.updated_at` 缺列阻塞） |

---

## 1 修复内容

| 修复 | 实现 |
|---|---|
| `doc.review` 分类 | 用首段明确 verdict（验收通过/批准/PASS vs 不通过/驳回/FAIL）判定；不再用全文“失败”子串 |
| 原子切分 | review / discussion / run.result：按句/编号项切分；单条 ≤240 字、≤2 句、≤2 编号项；超限拒绝，不静默截断 |
| `run.failure` 泛化状态 | `agent offline`、`heartbeat timeout`、`never started`、`exit <code>` 等不再生成素材 |
| `signal` 主张 | 问句、纯观点、裸产品名/版本号不进入 active；短发布标题（如 `Picard 3.0 Released`）保留 |
| 硬闸 | 新增 HG-9 原子性（review/discussion/run.result/signal）；pipeline 新增 HG-10 无主张、HG-11 泛化失败 |

---

## 2 旧 200 样本回归诊断

旧样本只用于诊断，不作为新校准样本。

| 错误类型 | 修复前 | 修复后 | 说明 |
|---|---|---|---|
| `problem|internal` 评审误分类 | 19 条 problem | **0 条 problem；19 条 decision** | 分类缺陷已修复 |
| 泛化失败状态 | 20 条可入库 | **0 条** | 全部排除/跳过 |
| 非原子长段 | 28 条旧长 quote | **旧 quote 0 条仍发出；28 条全部切分** | 新 quote 最长 223 字 |
| signal 无主张 | 50 条旧标签 no | 4 条仍可入库 | 4 条均为旧标签过严的**真实信号**（卵巢/Weball/Show HN/地震），无假放行 |
| 分类错误总数 | 28 条 | **1 条** | 27 条已纠正；剩余 1 条是分句后语义变化，不是同一 quote |

结论：四个针对性修复都减少了对应错误类型。

---

## 3 新样本（新提取器副本）

样本：`sample-p5-20261028.jsonl`，200 条 = 197 active + 3 quarantine，来自 `rehearsal_p5_20261028.db`（新提取器回填副本）。

| 指标 | 结果 |
|---|---|
| 素材总量 | **1,555**（旧 2,389，减少 35%） |
| active / quarantine | 1,552 / 3 |
| active 可用率（规则辅助） | 未加权 75.1% / 覆盖层加权 **76.3%** |
| active 误收率 | 未加权 24.9% / 覆盖层加权 **23.7%** |
| quarantine 漏收率 | **100%**（3/3，全部是可用的短发布/事件） |
| 分类准确率（同规则一致性） | 100%（**不是独立指标**，见 §4） |
| 覆盖层 | 7 层；21 层 active 证据不足；23 层漏收证据不足 |

分层：

| 层 | active 样本 | 误收率 | 分类准确率 | 证据 |
|---|---|---|---|---|
| signal \| external | 60 | 43.3% | 100% | 漏收 3/3 全可用 |
| decision \| internal | 61 | 52.5% | 96.7% | 漏收不足 |
| result \| task | 60 | 15.0% | 70.0% | 漏收不足 |
| problem \| task | 10 | 30.0% | 100% | active <20 |
| fact \| task | 3 | 0% | 66.7% | active <20 |
| lesson \| task | 2 | 0% | 50% | active <20 |
| decision \| task | 1 | 0% | 0% | active <20 |

说明：`decision/internal` 误收仍高，主要来自切分后的短片段/无谓词片段；`signal/external` 仍有 43.3% 的 active 无主张（观点/问句/裸名漏网），说明 signal 规则还需继续收紧。

---

## 4 独立复核与标签不确定性

17 条关键样本由另一个 agent 盲标（只读规则与 review pack，不读已有标签）：

| 项 | 结果 |
|---|---|
| 类型一致率 | 15 / 17 = **88.2%** |
| 可用性一致率 | 7 / 17 = **41.2%** |
| 主要分歧 | 短决策/短结果（2/3/6/84/96）、科学标题（154/156/173/180）、混合缺陷+修复（86） |
| 结论 | 可用性标签高度主观；本报告的 76.3% / 23.7% **不能当作精确误差率** |

这也说明：当前规则 U1/U2（自包含/原子）仍有歧义，需要独立人工复核后再用于阈值决策。

---

## 5 副本演练审计与幂等

在 `rehearsal_p5_20261028.db`（生产回填前备份的副本）执行新提取器 30 天回填：

| 检查 | 结果 |
|---|---|
| 业务表 7 张哈希前后 | **一致** |
| 审计 | `ok: true` |
| 重复 / 孤儿 | 0 / 0 |
| 第二次回填 | accepted=0 / materials_new=0 / merged=0 |
| 素材表 | raw 8,658 / material 1,555 / witness 2,115 / extract 2,115 |

生产库未变更；新提取器尚未写入生产。

---

## 6 结论与建议

| 项 | 裁定 |
|---|---|
| 四个针对性修复 | **有效**：旧样本对应错误 19→0、20→0、28→0、分类 28→1 |
| 新样本整体质量 | **未达标**：active 误收仍约 24–43%，signal/decision 仍有明显漏网 |
| 阈值 0.40 / 0.65 | **不冻结、不调整** |
| 生产写入 | **未执行**；需单独批准 |
| 新 exe | 继续阻塞 |
| 日志 | 保持 `trial` |

### 下一步

1. 收紧 `decision` 切分：切片必须带谓词/结论，纯列表项/短片段直接拒绝。
2. 收紧 `signal`：观点/问句/裸名的判定还需覆盖“科学标题但无事件”的情况；短发布标题单独白名单。
3. `run.result` 的 fact/lesson/problem 分类仍有 3 层准确率偏低，继续收敛。
4. 用独立人工（至少 2 人）复核关键 20–30 条，先解决 U1/U2 歧义，再谈阈值。
5. 修复并复核通过后，才讨论生产重跑与激活范围。

---

## 7 证据

| 文件 | 内容 |
|---|---|
| `calibration/regression-20261028.json` | 旧 200 条逐条回归诊断 |
| `calibration/sample-p5-20261028.jsonl` / `manifest-p5-20261028.json` | 新样本与分层 |
| `calibration/labels-p5-20261028.jsonl` | 新样本规则辅助标签 |
| `calibration/independent-review-p5.jsonl` | 17 条独立 agent 复核 |
| `calibration/analysis-p5-20261028.json` | 新样本指标 |
| `docs/taskhub/materials/calibration/RULES-20261028.md` | 规则 v1.1 |
