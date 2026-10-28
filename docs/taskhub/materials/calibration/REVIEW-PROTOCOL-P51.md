# P5.1 独立复核协议

## 目的

验证素材标注规则（U1-U7）能否稳定区分：

- 有独立意义的结论 / 结果 / 信号
- 无谓词短片段、纯列表项、裸产品名
- 混合缺陷+修复的长段

## 样本

- `review-pack-p51b-20261028.jsonl`：30 条，覆盖 decision、result、signal、problem、quarantine、短片段。
- `review-sheet-p51b.tsv`：空白标注表。
- 复核包只含 `sample_id`、`source_key`、`quote`；不含 `kind`、`status`、`quality`、已有标签。

## 复核步骤

1. 复核人只读 `RULES-20261028.md` 和 review pack；**不得**读 `labels-*.jsonl`、`analysis-*.json`、`overrides-*.json`、`regression-*.json`。
2. 对每条填写：
   - `label_kind`：signal/fact/result/problem/decision/lesson
   - `label_usable`：yes/no（按 U1-U7）
   - `disagreement_reason`：若与另一位复核人不同，写清依据哪条规则
   - `adjudication`：最终裁决及理由
   - `reviewer` / `reviewed_at`
3. 建议至少 2 名复核人独立填写；无法做到时明确记录“单复核人”，不得称为金标准。
4. 争议由第三人或规则维护者裁决；裁决必须引用 U1-U7 的具体条款。
5. 复核完成后，用 `scripts/materials_calibration.py` 计算与规则辅助标签的一致率，并单独报告。

## 可复现性

- 复核包与空白表随仓库提交。
- 复核完成后，将填写后的表另存为 `review-sheet-p51b-filled.tsv`，并记录复核人、日期、规则版本。
- 不得修改原始 `review-pack` 与 `review-sheet`。

## 当前状态

- 已由一名独立 agent 完成预复核：`independent-review-p51b.jsonl`（30 条）。
- **尚未完成独立人工复核**；因此 P5.1 的可用性指标仍不能作为金标准，阈值不得冻结。
