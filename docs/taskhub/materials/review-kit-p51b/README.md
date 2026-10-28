# P5.1 独立复核包

## 这是什么

一次小规模**判定一致性实验**，不是人工替 AI 审完 30 条文档。目的是用外部人工标签检验：

- 规则是否足够明确；
- 独立标注是否支持当前类型/可用性逻辑；
- 哪些边界必须继续拆分。

30 条能暴露规则歧义，**不能证明全库质量达标**；即使一致率高，也不能直接冻结阈值。

## 包里有什么

| 文件 | 用途 |
|---|---|
| `review-pack.jsonl` | 30 条盲样本，只含 `sample_id`、`source_key`、`quote` |
| `RULES-20261028.md` | 标注规则 v1.1 |
| `REVIEW-PROTOCOL-P51.md` | 冻结协议 v1.0-frozen |
| `review-sheet.tsv` | 空白复核表（每位复核人一份） |
| `adjudication-sheet.tsv` | 裁决表（两位复核完成后填写） |
| `manifest.json` | 文件 hash 与冻结时间 |
| `REPORT-TEMPLATE.md` | 复核完成后生成报告 |

## 怎么用

1. 复核人 A、B 各自复制一份 `review-sheet.tsv`，重命名为 `review-sheet-<reviewer>.tsv`。
2. 开始前校验冻结包完整性：`python scripts/materials_review_kit.py --verify --kit docs/taskhub/materials/review-kit-p51b`。
3. 只读 `review-pack.jsonl`、`RULES`、`REVIEW-PROTOCOL-P51.md`；**不要**看 `labels-*.jsonl`、`analysis-*.json`、`independent-review-*.jsonl`。
4. 逐条填：`label_kind`、`label_usable`、`confidence`、`reason`、`reviewer_id`、`reviewed_at`、`round`。
5. 两位都完成后，再填写 `adjudication-sheet.tsv`；原始表不得修改。
6. 运行：

```bash
python scripts/materials_review_analysis.py \
  --kit docs/taskhub/materials/review-kit-p51b \
  --reviewer docs/taskhub/materials/review-kit-p51b/review-sheet-A.tsv \
  --reviewer docs/taskhub/materials/review-kit-p51b/review-sheet-B.tsv \
  --system-labels docs/taskhub/materials/calibration/labels-p51b-20261028.jsonl \
  --out docs/taskhub/materials/review-kit-p51b/analysis.json \
  --report docs/taskhub/materials/review-kit-p51b/REPORT.md
```

## 冻结

`manifest.json` 记录了 pack/rules/protocol 的 hash。第一位复核人开始后不得修改；确需修改必须作废本轮、重新抽样并记录原因。

## 当前状态

**等待真实人工复核。** 现有 `independent-review-p51b.jsonl` 是独立 agent 预复核，不是人工标签，不能用于质量准入。
