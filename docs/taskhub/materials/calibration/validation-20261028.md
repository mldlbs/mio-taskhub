# 素材库自动化质量验证（2026-10-28）

> 对应裁定：编号 12。人工复核取消，质量验证转全自动。
> 本报告只声明可复现的自动检查结果；**不给出“素材库质量达标”的结论**。

## 0 摘要

| 问题 | 结果 |
|---|---|
| 已知争议是否被稳定捕获 | 24/24 通过，重复运行结果一致 |
| held-out 结构缺陷 | 0 / 1,054 |
| 重复素材 | 0 |
| 同版本两次独立回填 | 漂移 0 |
| 二次回填 | 新增/合并/接受均为 0 |
| 第一层数据质量门禁 | 当前副本 **PASS**；P5.1 副本 **FAIL**（bare_count） |
| 真实可用率 | **未测量**（无独立语义真值） |
| 语义误收率 | **未测量**（结构检查覆盖不到） |
| 质量准入 | **未通过** |
| 生产重跑 / 阈值冻结 | **不批准** |

---

## 1 证据冻结

`calibration/EVIDENCE-FREEZE-20261028.md` 记录了 30 条盲包、协议 v1.0-frozen、Agent 预复核的哈希与状态：

- 人工复核已取消，未产生人工资格标签；
- Agent 预复核（类型 80.0%、可用性 56.7%）只作工具预检，不是金标准；
- 这些材料不再进入任何质量门禁。

---

## 2 确定性测试集：已知争议

fixture：`tests/fixtures/materials/known_disputes.json`（24 条，人工按规则冻结口径编写，不随提取器输出自动更新）。
测试：`tests/test_materials_known_disputes.py`（24 条结果 + 24 条确定性 + 1 条覆盖检查）。

| 类别 | 条数 | 覆盖 |
|---|---|---|
| 类型归属 | 7 | 验收结论、测试计数、根因、驳回、run.result 结果/失败 |
| 短结论 | 4 | 纯 ID、冒号截断、无谓词、裸计数 |
| 名词短语信号 | 7 | 裸版本号/产品名、问句、观点标题、发布/事件/科学主张 |
| 无谓词片段 | 2 | 裸计数、列表标记剥离 |
| 混合内容 | 4 | 验收多句拆分、圈号列表拆分、数字列表、超长无标点段 |

结果：`24 passed`；同一输入两次提取的 `(kind, quote, char_start, char_end)` 完全一致。

```bash
python -m pytest tests/test_materials_known_disputes.py -q
```

---

## 3 独立结构验证（held-out）

脚本：`scripts/materials_validate.py`。**不 import 提取器或质量规则**，只按规范检查：

| 检查 | 定义 |
|---|---|
| 逐字回读 | `payload[field][start:end] == witness.quote_exact == material.quote` |
| 原子性 | 原子来源 ≤240 字、≤2 句末标点、≤2 编号项 |
| 结论形状 | decision 不以冒号结尾、不短于 10 字 |
| 裸计数 | result 不能只是 `67 passed` / `failed=25` |
| 标记泄漏 | quote 不以 `①` / `1)` / `1.` / `-` 开头 |
| 重复 | active 中 `content_fingerprint` 不重复 |

样本：`rehearsal_p52_20261028.db`（生产回填前备份副本，当前代码回填）。

| 子集 | 数量 | 结构缺陷 | 重复 | 回读不可验证 |
|---|---|---|---|---|
| held-out（排除 P4/P5/P5.1 已抽样 324 条） | 1,054 active | **0** | **0** | 0 |
| 全量 census | 1,378（1,375 active + 3 quarantine） | **0** | **0** | 0 |

全量 census 中 3 条 quarantine 的 `status_reason` 均为 `below_threshold`，结构检查未发现缺陷——只能说明“不是结构性问题”，不能据此认定应转为 active。

```bash
python scripts/materials_validate.py --db <copy.db> \
  --exclude-sample docs/taskhub/materials/calibration/sample-20261028.jsonl \
  --exclude-sample docs/taskhub/materials/calibration/sample-p5-20261028.jsonl \
  --exclude-sample docs/taskhub/materials/calibration/sample-p51b-20261028.jsonl \
  --out docs/taskhub/materials/calibration/validation-p52-20261028.json
```

---

## 4 漂移、可复现、幂等

脚本：`scripts/materials_drift.py`（按内容指纹 + 类型 + 状态对比）。

| 对比 | removed | added | kind changed | status changed | drift |
|---|---|---|---|---|---|
| P5.1 → 当前（本轮修复） | 8 | 8 | 32 | 0 | **3.46%**（union 1,386） |
| P5 → P5.1（上轮修复） | 180 | 189 | 0 | 0 | 23.68%（union 1,558） |
| 当前 vs 当前（两次独立回填） | 0 | 0 | 0 | 0 | **0** |

本轮 3.46% 漂移全部可归因：

| 变化 | 数量 | 原因 |
|---|---|---|
| `decision` → `result` | 32 | `doc.review` 测试/构建计数改归 result |
| 列表项拆分/去标记 | 8 对 | 圈号列表拆分；`1.` / `6.` 等数字标记剥离 |
| `failed=25` 移除 | 1 | HG-13 裸计数拒绝 |

可复现与幂等（`rehearsal_p52` 与 `rehearsal_p52b` 均从同一备份副本独立回填）：

| 检查 | 结果 |
|---|---|
| 两次独立回填素材集差异 | **0** |
| 第二次回填 | 全部来源 `accepted=0 / materials_new=0 / materials_merged=0` |
| 业务表 7 张哈希 | **一致** |
| 审计 | `ok: true` |
| 重复 / 孤儿 | 0 / 0 |
| 素材表 | raw 8,658 / material 1,378 / witness 1,940 / extract 1,940 |

---

## 5 指标分级（哪些能用于门禁）

| 指标 | 证据类型 | 值 | 能否用于质量准入 |
|---|---|---|---|
| 已知争议用例 | 确定性回归 | 24/24 | **可以**（只证明缺陷不复现） |
| 结构缺陷（held-out） | 规范级检查 | 0 / 1,054 | 仅限结构问题 |
| 重复素材 | 确定性检查 | 0 | **可以** |
| 同版本漂移 | 确定性检查 | 0 | **可以**（证明可复现） |
| 幂等 | 确定性检查 | 0 新增 | **可以** |
| 版本间漂移 | 描述性 | 3.46% | 不可（只说明变化，不说明好坏） |
| active 可用率 83.9% | 规则辅助标注 | — | **不可以**（同规则自证） |
| 真实可用率 | 无独立标签 | **未测量** | 不可以 |
| 语义误收率 | 无独立标签 | **未测量** | 不可以 |
| quarantine 漏收（held-out） | 无 held-out 隔离样本 | **未测量** | 不可以 |

---

## 6 本轮发现并修复的缺陷

| 缺陷 | 发现方式 | 修复 |
|---|---|---|
| `doc.review` 测试计数/根因被判为 decision | 已知争议用例 + 漂移对比 | `_review_kind`：计数→result，根因→problem |
| `discussion.closed` 圈号列表未拆分 | 混合内容用例 | `_split_items` 支持 `①-⑩` |
| `1.` / `6.` 数字标记泄漏 | 漂移对比发现 quote 以 `1.` 开头 | 统一剥离数字标记 |
| `failed=25` 裸计数入库 | 已知争议用例 | HG-13 |
| 审计 I2 漏检“零主 witness” | 门禁用例构造 | I2 改为逐素材计数，`!=1` 即失败 |

说明：`1.` 标记泄漏是在本轮独立验证中被漂移对比暴露的，不在原计划用例内——属于“独立检查暴露未知问题”的一例。

---

## 7 局限（必须保留）

1. **结构检查不是语义真值**。它只验证提取器是否遵守自己的规范，无法判断“观点被当成信号”这类语义误收。实际观察到 `Bring Back the Cool Save File Screens`、`Just say no to OpenAI and Anthropic` 通过 signal 闸，但当前结构检查不会把它们计为缺陷。
2. **held-out 不是新观察数据**。1,054 条是同一 30 天语料中未被 P4/P5/P5.1 抽中的行，不是规则冻结后新抓取的数据。
3. **quarantine 漏收在 held-out 上不可测**：3 条 quarantine 都已被历史样本覆盖，held-out 子集为 0。
4. **没有人工标签**。Agent 预复核不计入；任何“可用率”数字都不能外推全库。
5. 30 条复核包与协议只作历史证据，不再产生新结论。

---

## 8 门禁

| 项 | 裁定 |
|---|---|
| 素材库质量准入 | **未通过**（真实可用率未测量） |
| 阈值 0.40 / 0.65 | 不调整、不冻结 |
| 生产重跑 | 不批准 |
| 日志来源 | 保持 `trial` |
| 新 exe | 继续阻塞（`task.updated_at` 缺列，独立事项） |
| 生产库 | 2,389 素材 / 2,351 active / 38 quarantine，未变更 |

---

## 9 第一层门禁（编号 13）

`scripts/materials_gate.py` 将上述结构检查与审计检查合并为阻塞式门禁：

| 库 | 结果 | 证据 |
|---|---|---|
| `rehearsal_p52`（当前代码） | **PASS**（0 阻塞） | `calibration/gate-p52-20261028.json` |
| `rehearsal_p51b`（P5.1） | **FAIL**：`structural:bare_count` | `calibration/gate-p51b-20261028.json` |

门禁通过只代表数据质量与系统正确性，不代表业务效用。第二层下游 A/B 实验**未启动**，真实可用率仍为“未测量”。详见 `docs/taskhub/materials/GATE.md`。

---

## 10 复现

```bash
# 已知争议
python -m pytest tests/test_materials_known_disputes.py -q

# 结构校验 / 漂移
python scripts/materials_validate.py --db <copy.db> --out <json>
python scripts/materials_drift.py --baseline <a.db> --candidate <b.db> --out <json>

# 回填幂等（副本）
MIO_TASKHUB_DB=<copy.db> python scripts/materials_backfill.py --days 30
```

产物：

- `calibration/validation-p52-20261028.json`（held-out）
- `calibration/validation-p52-census-20261028.json`（全量）
- `calibration/drift-p51b-vs-p52-20261028.json`
- `calibration/drift-p52-vs-p52b-20261028.json`
- `~/.mio_taskhub/preflight/p52_backfill_1.log` / `p52_backfill_2.log`
- `~/.mio_taskhub/preflight/p52_diff.json` / `p52_audit.json`
