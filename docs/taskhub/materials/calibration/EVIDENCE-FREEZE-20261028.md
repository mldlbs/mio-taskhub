# P5.1 复核证据冻结（2026-10-28）

## 状态

| 项 | 值 |
|---|---|
| 决定 | 编号 12：取消双人人工标注，质量验证转全自动 |
| 本目录 | 历史证据归档，**不再用于质量门禁** |
| 人工复核 | **已取消**，未产生任何人工资格标签 |
| Agent 预复核 | 仅工具预检，**不是金标准**，不得用于阈值或生产决策 |
| 冻结包 | 未修改；`scripts/materials_review_kit.py --verify` 仍应通过 |

## 清单与哈希

| 文件 | sha256 | 用途 |
|---|---|---|
| `review-kit-p51b/review-pack.jsonl` | `ae613e2b7e1ef0c30e3180caa204ffeb764eed531706b8fbc42862f1cf87e184` | 30 条盲包 |
| `review-kit-p51b/REVIEW-PROTOCOL-P51.md` | `7fa420988cd8147f3f142dc7a7c24f0c04c990a2e6b3bd9422e8ae9a181e6bad` | 协议 v1.0-frozen |
| `calibration/RULES-20261028.md` | `a694603322e18a9912e4dfbfdb0dc7d990be8cbc8ccc4eefdac934a786cc6124` | 标注规则 v1.1 |
| `calibration/independent-review-p51b.jsonl` | `b6a6ca97dc83dc7bdc7867fa193988206059b1859d34426cf3a5b0e55036ca8a` | Agent 30 条预复核 |
| `calibration/independent-review-p5.jsonl` | `7f469ff3743e6e00e62bd68bacd4a106daaf302f2e07b91991c24e7c7e46b109` | Agent P5 预复核 |
| `review-handout-p51b/HANDOFF.md` | `aaa9d579d686b070f89fe278857edfbd0a486f4d41b503c9aba9e77d44fb86d6` | 交接说明（已加取消标注，留痕） |

## 允许与禁止

允许：

- 作为历史材料引用，说明当时的口径与分歧；
- 用其中的争议样本构造确定性测试（已转为 `tests/fixtures/materials/known_disputes.json`）。

禁止：

- 把 Agent 预复核的一致率（类型 80.0%、可用性 56.7%）当作人工一致率；
- 用这 30 条推断全库可用率；
- 因人工复核取消而降低生产门禁。

## 后续

质量验证改为三层：

1. 确定性测试（已知争议）；
2. 独立结构校验（held-out，不 import 提取器）；
3. 版本漂移 / 可复现 / 幂等。

报告：`calibration/validation-20261028.md`。
