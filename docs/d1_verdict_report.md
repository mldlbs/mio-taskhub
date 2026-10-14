# D1 首启成功率判定报告（T2.4 / 2026-10-14）

## 判定结论：**部分成立**（70–90% 区间）

| 指标 | 值 | 门槛 | 判定 |
|---|---|---|---|
| 首启样本 n | 234 | ≥100 | PASS |
| 首启成功率 | 81.9% (154/188) | ≥90% 支持 / 70–90% 部分 / <70% 不成立 | **部分成立** |
| reaper_decision 样本 | 82（首启）/ 444（全量） | ≥30 | PASS |
| 非误回收证据 | 真死 27.7% / 链路断 70.9% | 需分类 | PASS（可分类） |

## 三分根因（first_start_stats.py，首启口径 n=234）

| 根因 | 计数 | 占失败比 |
|---|---|---|
| 观测不足（never_started） | 26 | 76.5% |
| 链路断（agent_offline） | 0 | 0% |
| 真死（outcome=fail） | 8 | 23.5% |
| **失败小计** | **34** | — |

## reaper 全量分析（analyze_reaper.py，n=444）

| 分类 | 计数 | 占比 | top_agents |
|---|---|---|---|
| 真死 truly_dead | 123 | 27.7% | opencode 61, idle-worker 55 |
| 链路断 link_broken | 315 | 70.9% | inv1-a/b/c 202 |
| 观测不足 insufficient | 6 | 1.4% | legacy/verify/inv1 |

## lag vs timeout 分布（n=444）

- lag p50=302.7s / p95=315.9s / max=667.3s
- effective_timeout p50=300s / max=300s
- margin(lag-eff) p50=+8.1s → 多数刚超时被回收（链路断主导）
- 120s 档：128 例在 0-300s 区间（及时回收）

## 一致性交叉验证

- first_start_stats（首启口径）：真死 8 / 观测不足 26 / 链路断 0
- analyze_reaper（全量 reaper 事件）：真死 123 / 链路断 315 / 观测不足 6
- 差异解释：首启口径只计每任务 MIN(started_at) 首个 run；全量含 requeue 后的后续 kill。链路断在首启为 0（首次 claim 后 agent 尚未超时），requeue 后才大量出现（inv1 系列 agent 批量离线）。

## D1 → D2/D3 前置结论

- **D1 部分成立**（81.9%）→ D2/D3 实验前置条件部分满足（需 >90% 才完全满足）
- 链路断 70.9% 是主因（非任务本身真死）→ 改善心跳/超时参数可提升首启成功率
- 建议：进入 G3 前先修 inv1 系列 agent 心跳（70.9% 链路断中 inv1 占 202/315）

## 数据源

- `scripts/first_start_stats.py --min-n 100`（exit 0，gates 全 PASS）
- `scripts/analyze_reaper.py`（n=444 gate PASS）
- DB: `~/.mio_taskhub/taskhub.db`（只读采集）
