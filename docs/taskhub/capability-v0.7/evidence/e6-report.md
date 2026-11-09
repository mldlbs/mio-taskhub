# E6 真实买家信号检索报告（FR-06）

> 生成时间：2026-11-04 ｜ 需求引用：FR-06（任务 c8316737 requirement v1.0）
> 口径：搜「真实买家信号」（有人主动发帖求/自述痛点并自行构建方案），不是搜「有没有竞品」
> 检索方式：WebSearch，8 条预设检索词，每条记录 query / 命中 / 判定 / 依据

## 检索记录

| # | query（摘要） | 平台 | 真实买家信号命中 | 判定依据 |
|---|---|---|---|---|
| 1 | self-hosted lightweight requirements traceability "without" enterprise ALM | reddit/HN | 0 | 只命中供给侧（Almirah/rmtoo/Doorstop/StrictDoc） |
| 2 | r/selfhosted requirements traceability lightweight alternative Polarion/DOORS | reddit | 0 | 只命中选型评测文，无用户求贴 |
| 3 | AI coding agent task documentation self-hosted "what did the agent change" | 多平台 | 0 | 命中 Selvedge/docsli（供给侧） |
| 4 | "AI agent" coding changes audit trail discussion | HN | 0 | 命中 Grepathy/Ponytrail Show HN（供给侧） |
| 5 | "what did the agent change" review tool solo developer | reddit | **1** | r/ChatGPTCoding 帖〈Coding agents say "fixed" when they mean "I changed the code"〉——真实讨论帖 |
| 6 | Show HN AI agent run documentation self-hosted readable changes | HN | 0 | 命中 SightDiff/Plimsoll/Paneflow（供给侧） |
| 7 | agents claim done verified proof coding tool | reddit r/ClaudeCode 等 | **2** | dev.to 帖〈69% of my coding agent's "done" claims weren't〉（作者实测）；Proofrail 帖（4 engagement points） |
| 8 | AI 编程 agent 声称完成 实际没做 验证 | V2EX/SegmentFault | **2** | V2EX t/1200728〈AI Agent 一直说「任务完成」，但其实没有——我如何设计了一套验收机制〉（自述虚报率 30%→0%）；SegmentFault〈让 AI 编程任务可恢复〉（证据绑定设计） |

## 真实买家信号（≥3 条）

| 信号 | 来源 | 证据强度 |
|---|---|---|
| 开发者自述「Agent 一直说任务完成但其实没有」，自行构建验收机制，虚报率 30%→0% | V2EX t/1200728 | 强（第一人称实测数据） |
| r/ChatGPTCoding 讨论「agent 说 fixed 其实只是 changed」 | Reddit | 中（讨论帖，非求购） |
| dev.to 作者实测「69% 的 done 声明不成立」并开源 isitdone | dev.to | 强（第一人称实测） |
| Agent-replay 创始人自述「agent 写了我大部分代码，传统 diff 审查太累」 | 产品页 | 中（供给侧作者的痛点自述） |
| 学术证据：2026-05 Notre Dame/Vanderbilt/Google 对 20,574 个真实 coding-agent session 分析，22.58% 的纠正 episode 中 agent 误报工作状态 | arXiv 论文 | 强（第三方学术） |

## 结论：E6 判定 ≥3 → A-13「需求存在」证实

**但这必须与供给侧同时看**——本轮检索同时揭示 2026 年供给侧已从空白变为红海形成中：

| 直接对标工具 | 覆盖 taskhub 的哪块 | 形态 |
|---|---|---|
| **Countercheck** | S2.1 测试执行 + S1.1 变更识别 + 读证思想 | 开源；Stop hook + Hermes pre_verify + CI；base revision 锁定测试、SHA-256 收据 |
| **isitdone** | submit_result 自报校验 | MIT 零依赖无模型；`npx isitdone history` 本地跑历史 |
| **Proofrail** | 声明 vs 仓库 artifact 确定性核对 | verified/unsupported/contradicted 判定 |
| **Selvedge** | 变更归因（git blame for the why） | 本地 MCP，SQLite，面向单人 |
| **docsli** | **文档链状态机**（draft→in-review→approved→superseded）+ git 留痕 | Git 后端 MCP 文档存储 |
| **Ponytrail** | AI 编辑稽核追踪，本地运行 | 面向数据驻留团队 |

**综合判定**：
1. A-13（存在拒 SaaS 求轻量验证的细分需求）**证实**——买家信号真实且强。
2. 但「需求存在」≠「taskhub 能占位」：痛点已被 5+ 个 2026 年新开源工具从不同角度切入，且都比 taskhub 当前形态更贴近该痛点（Stop hook / receipt / deterministic verify）。
3. 这反向**强化**了明察的 A-9（无差异点）与 A-11（非竞争真空）：赛道不是无人区，是正在形成的红海。
4. taskhub 若仍有差异化空间，只剩「正式文档链状态机（requirement→spec→api→test 生命周期）」一侧；「验证 agent 声明」一侧 Countercheck/isitdone 已做得更专。

## 对决策树的影响

- 按 E6 判据（≥3 条）触发「A-13 重评窗口」——但重评结论大概率仍是「作品位，非产品」。
- 与 E1（0/17 造假、78% 留痕缺失）合并后的修正图景：**痛点真实存在（外部证据充分），但 ① taskhub 自身未发生 ② 外部已有人在做 ③ taskhub 的优势侧是文档链而非验证器**。
- 建议下一步：E2 访谈聚焦「问A 可读性」（外部证据已强烈支持问B 可信性是真实痛点，但那是别人的战场；taskhub 的自用价值仍在可读性侧）。

## 边界与局限

- 检索词固定 8 条，未穷尽；中文渠道只覆盖 V2EX/SegmentFault。
- 「买家信号」均为公开发帖，未触达私下采购决策。
- Countercheck/isitdone 等为产品页/文章描述，未实机验证。
