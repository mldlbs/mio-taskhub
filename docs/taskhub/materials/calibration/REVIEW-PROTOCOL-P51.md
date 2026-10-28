# P5.1 独立人工复核协议（v1.0-frozen）

## 0 冻结声明

| 项 | 值 |
|---|---|
| 协议版本 | **v1.0-frozen** |
| 冻结时间 | 2026-10-28 |
| 适用样本 | `review-kit-p51b/review-pack.jsonl`（30 条） |
| 冻结规则 | 本文件 + `RULES-20261028.md` + 空白复核表 |
| 变更规则 | 第一位复核人开始标注后**不得修改**；确需修改必须作废本轮、重新抽样并记录原因 |

本复核的目的不是“让人替 AI 审完 30 条文档”，而是建立一次**小规模判定一致性实验**，用外部人工标签检验：

- 规则是否足够明确；
- 独立标注是否支持当前类型/可用性逻辑；
- 哪些边界必须继续拆分。

30 条只能暴露规则歧义，**不能证明全库质量达标**；即使一致率高，也不得直接冻结阈值。

---

## 1 盲法

1. 复核人只允许阅读：
   - `review-kit-p51b/README.md`
   - `review-kit-p51b/RULES-20261028.md`
   - `review-kit-p51b/review-pack.jsonl`
   - 本协议
2. **不得**阅读 `labels-*.jsonl`、`analysis-*.json`、`independent-review-*.jsonl`、`overrides-*.json`、`regression-*.json`，也不得询问系统标签或前一位复核人的结论。
3. 两位复核人独立完成后，才允许交换结果；交换后只讨论分歧，不修改原始表。
4. 原始表必须保留，裁决结果写入单独的 `adjudication-sheet.tsv`，不得覆盖原始标注。

---

## 2 标签定义

### 2.1 类型归属（`label_kind`）

| 类型 | 定义 | 关键边界 |
|---|---|---|
| `signal` | 外部世界发生的具体事件、发布、事故、政策、价格、研究结论或技术主张 | 必须有主语+谓词/事件；问句、纯观点、裸产品名/版本号不算 |
| `fact` | 对系统当前状态、行为、配置或既有事实的描述 | 不是“做成了什么” |
| `result` | 一次任务/实验实际做成了什么（测试、构建、部署、验证结果） | 测试计数/构建成功归 result |
| `problem` | 一次失败、错误、超时、缺陷或根因 | 泛化状态（agent offline/heartbeat timeout）本身不算可用问题 |
| `decision` | 已确认的需求、评审结论、讨论结论或选择 | **doc.review 中的测试计数/构建结果归 result，不归 decision** |
| `lesson` | 可复用的注意事项、坑或操作建议 | 不是一次性结果 |

`doc.review` 的归属规则：

- 验收/批准/驳回/关闭/裁决 → `decision`（驳回同时含失败证据可 `problem`）
- “1468 passed / build ok / 3 例全绿” → `result`
- 纯缺陷描述/根因 → `problem`

### 2.2 可用性（`label_usable`）

`yes` 必须同时满足：

| 编号 | 条件 |
|---|---|
| U1 | **自包含**：有主语 + 谓词/结论，脱离原文能读懂在说什么 |
| U2 | **原子**：≤240 字、≤2 句、≤2 个编号项 |
| U3 | **有主张**：不是纯名词短语、纯标识符、纯列表项 |
| U4 | **非样板**：不是导航/登录/订阅/评论区 |
| U5 | **非截断**：不是半截堆栈/路径/工具输出 |
| U6 | **非泛化状态**：`agent offline`、`heartbeat timeout`、`never started` 单独出现不算 |
| U7 | **非近重复**：近重复只保留信息更全的一条 |

边界细则（本轮冻结）：

| 场景 | 判定 |
|---|---|
| 短结论（如“平拓扑”“裁决”） | 无谓词/对象 → no |
| 短测试结果（“67 passed”） | 只有计数、无范围/对象 → no；“全量 1468 passed”有范围+结论 → yes |
| 列表项残片（“AC-1、AC-2”“②可选恢复…”） | 无独立谓词 → no；有完整谓词+对象 → yes |
| `doc.review` 测试计数 | 类型归 result；有范围+结论可 yes |
| 名词短语型 signal（“OpenSSH 10.6”“Google Playground”） | 无事件/谓词 → no |
| 发布/事件型 signal（“Picard 3.0 Released”“Example.com Just Launched…”） | 有发布动词/事件 → yes |
| 科学标题（“The ovary doesn't retire after menopause”） | 有明确主张 → yes |
| 问句/观点（“Ask HN…”“Why we're watching…”） | no |
| 混合缺陷+修复长段 | 按 U2 原子性拆判；未拆分的整段 no |

### 2.3 置信度

| 值 | 含义 |
|---|---|
| `high` | 规则直接覆盖，无需上下文 |
| `medium` | 边界案例，依赖对规则的解释 |
| `low` | 规则未覆盖或存在两种合理解释 |

---

## 3 复核表

`review-sheet.tsv` 列：

| 列 | 说明 |
|---|---|
| `sample_id` | 包内编号 |
| `source_key` | 来源（判断上下文） |
| `quote` | 原文片段 |
| `label_kind` | 六类之一 |
| `label_usable` | yes / no |
| `confidence` | high / medium / low |
| `reason` | 一句话依据（引用 U1-U7 或类型边界） |
| `reviewer_id` | 复核人代号 |
| `reviewed_at` | ISO 时间 |
| `round` | 1 / 2（独立轮次） |

`adjudication-sheet.tsv` 列：

| 列 | 说明 |
|---|---|
| `sample_id` | |
| `reviewer_a_kind` / `reviewer_a_usable` | 原始 A |
| `reviewer_b_kind` / `reviewer_b_usable` | 原始 B |
| `agreement` | kind+usable 是否一致 |
| `adjudicated_kind` / `adjudicated_usable` | 裁决结果 |
| `adjudicator` / `reason` / `at` | 裁决留痕 |

---

## 4 分析口径

收到至少 2 份独立表后运行 `scripts/materials_review_analysis.py`：

- 类型一致率、可用性一致率；
- Cohen's κ（两名复核人）；
- 类型混淆矩阵；
- 逐条分歧与理由；
- 与系统标签的对照（仅在分析阶段读取，不影响盲标）。

**不追求高一致率**：分歧是规则需要澄清的证据。裁决后的标签另存，不覆盖原始标注。

---

## 5 结论门槛

| 结论 | 需要 |
|---|---|
| 规则足够明确 | 关键边界（doc.review 测试计数、短结论、名词短语 signal）分歧有明确裁决理由 |
| 可扩大样本 | 本轮分歧原因可归类，且规则修订后能形成新版本 |
| 可讨论阈值 | 独立人工一致性达到可接受水平，且样本覆盖关键层 |
| 可生产重跑 | 上述全部通过，另行批准 |

本轮 **不** 因一致率高低直接冻结阈值。
