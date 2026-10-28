# P5.1 复核人交接说明

> **状态：已取消（2026-10-28，编号 12）。** 双人人工标注不再进行，本文件仅作历史留痕；复核包与协议保留在 `../review-kit-p51b/`，不作为质量门禁。

## 这次复核是什么

一次 30 条样本的**判定一致性实验**，用来检验标注规则是否足够明确、两名复核人能否独立复现同一判断。它不是“替 AI 审完素材库”，也不直接决定阈值或生产。

## 你拿到的文件

| 文件 | 用途 |
|---|---|
| `review-sheet-A.tsv` 或 `review-sheet-B.tsv` | 你的独立填写表（只填自己那一份） |
| `../review-kit-p51b/review-pack.jsonl` | 30 条盲样本，只含 `sample_id` / `source_key` / `quote` |
| `../review-kit-p51b/RULES-20261028.md` | 标注规则 v1.1 |
| `../review-kit-p51b/REVIEW-PROTOCOL-P51.md` | 冻结协议 v1.0-frozen |
| `handout-manifest.json` | 发放时的文件哈希，供事后核对 |

开始前可校验冻结包：

```bash
python scripts/materials_review_kit.py --verify --kit docs/taskhub/materials/review-kit-p51b
```

## 不能看什么

- 不要读 `labels-*.jsonl`、`analysis-*.json`、`independent-review-*.jsonl`、`overrides-*.json`、`regression-*.json`。
- 不要询问系统标签，也不要与另一位复核人讨论样本。
- 标注期间不修改规则、协议或样本。

## 怎么填

只填这 6 列，**不要改** `sample_id` / `source_key` / `quote`，不要增删行、不要改列名：

| 列 | 取值 | 说明 |
|---|---|---|
| `label_kind` | signal / fact / result / problem / decision / lesson | 按协议 §2.1 |
| `label_usable` | yes / no | 必须同时满足 U1–U7，按协议 §2.2 |
| `confidence` | high / medium / low | high=规则直接覆盖；medium=边界；low=规则未覆盖 |
| `reason` | 一句话 | 引用 U1–U7 或类型边界；判 no 必须写清缺哪条 |
| `reviewer_id` | `A` 或 `B` | 与你的表一致，不要共用同一代号 |
| `reviewed_at` | ISO 时间，如 `2026-10-28T15:30:00+08:00` | |
| `round` | `1` | 本轮为第一轮独立标注 |

注意：

- 含换行的原文在表里写成字面 `\n`，保持不变。
- 用文本编辑器或表格软件均可，但保存后必须是 **UTF-8 TSV**，列数不变。
- 不确定时按规则判，并在 `reason` 写明两种解释；不要为了“看起来一致”而改判。

## 提交

- 填完直接把 `review-sheet-A.tsv` / `review-sheet-B.tsv` 放回 `docs/taskhub/materials/review-handout-p51b/`。
- 交给分析前不要覆盖、不要“顺手整理”原表；分析脚本会记录返回文件的 sha256。
- 发放清单同时记录 raw 与 LF-normalized hash，Git 检出造成的 CRLF 差异不影响核对。
- 两份表都到齐后，才运行分析并生成分歧清单；裁决另存 `adjudication-sheet.tsv`，不回写原表。

## 时间

约 20–30 分钟。判 no 的样本请务必写理由，这是本轮最有价值的产出。
