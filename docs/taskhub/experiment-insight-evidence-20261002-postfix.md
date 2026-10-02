# 真实样本实验报告：Insight → Evidence 闭环（执行侧修复后）

| 项 | 值 |
|---|---|
| 日期 | 2026-10-02 |
| 前置 | 执行侧修复 task e1199159（idle_worker prompt 同步 required_reads，提交 f7d46b5） |
| 目的 | 验证 `[insight] Task → claim → read_document → ReadEvidence → submit → COMPLETED` 在真实 Agent 下成立（**执行侧修复后**） |
| 协议 | 纯验证：不改代码；门控/ReadEvidence 语义不变；**失败样本不剔除**；cohort 仅含修复后生成的任务 |
| Agent | opencode CLI（v1.18.34），经 idle_worker |

---

## 1. 结果汇总

### 成功完整闭环（n=5）✅
| # | task_id | run_id | claim | read_document | ReadEvidence | submit | 最终状态 | exit_code |
|---|---|---|---|---|---|---|---|---|
| 1 | d049aa0a | 9d8957e3 | ✅ | ✅ | **2**（spec+requirement） | ✅ 通过 | **COMPLETED** | 0 |
| 2 | c1ccca2c | 0e20c2a1 | ✅ | ✅ | **2** | ✅ 通过 | **COMPLETED** | 0 |
| 3 | 4652def8 | 78925d20 | ✅ | ✅ | **2** | ✅ 通过 | **COMPLETED** | 0 |
| 4 | eddb4814 | 3f77bbad | ✅ | ✅ | **2** | ✅ 通过 | **COMPLETED** | 0 |
| 5 | 2a8f4de3 | f4d9b620 | ✅ | ✅ | **2** | ✅ 通过 | **COMPLETED** | 0 |

### 失败样本（保留）
| task_id | run_id | 结果 | 分类 |
|---|---|---|---|
| c1ccca2c | cd9e4840 | `never started (no heartbeat)`，exit=1，ev=0 | **agent 未启动即被回收**（与门控无关） |

---

## 2. 对照实验（修复前后）

| 指标 | 修复前（n=2） | 修复后（n=5） |
|---|---|---|
| claim | 2/2 | 5/5 |
| read_document 调用 | **0/2** | **5/5** |
| ReadEvidence | **0** | **10**（每样本 2） |
| submit 通过 | **0/2**（全 422） | **5/5** |
| COMPLETED | **0/2** | **5/5** |
| **完整闭环率** | **0%** | **100%（5/5）** |

**唯一变量**：`idle_worker.build_prompt` 从"不提 required_reads"改为"明确列出 required_reads + 执行顺序 + 未读422警示"。**未改任何门控**。

---

## 3. 结论（对应协议 A/B/C）

**判定 = A**：
> **`Insight → Task → Agent → read_document → ReadEvidence → submit → COMPLETED` 闭环在真实 Agent 下成立（5/5）。**

- 之前 n=2 的 0% 由 **执行侧协议缺失** 造成（agent 不知要读）；
- 修复执行侧 prompt 后，**同一门控、同一 agent** 达成 5/5；
- 这**反证了门控本身正确**：agent 读了就通过、不读就 422。

---

## 4. 诚实声明与边界

1. **n=5**，达成协议目标，但**样本仍小**；
2. **存在 1 个失败样本**（`cd9e4840`=`never_started`）——属 **agent 启动/心跳问题**（非门控、非阅读协议），**已保留**；
3. 失败样本的存在说明 **agent 执行有方差**（同 prompt，#2 首跑失败、重跑成功）；
4. 所有成功样本的 ReadEvidence 均为 **2 条（spec+requirement）**，与 `required_reads` 精确对应——**证明确实是按门控读的，非偶然**；
5. 未手工补 evidence；未为成功率改门控/prompt。

---

## 5. 意义

这次实验完成了从"**控制面正确**"到"**控制面 + 执行面协议同步**"的跨越：

```
修复前:  TaskHub 门控正确  +  Agent 不知道要读   → 0% 闭环
修复后:  TaskHub 门控正确  +  Agent 被明确告知    → 100% 闭环 (n=5)
```

**核心教训**：**控制面的约束必须在执行面显式传达**——claim 返回 `required_reads` 只是"服务端知道"，agent 不会读心术；执行侧 prompt 必须把协议翻译给 agent。

---

## 6. 下一步（不在本轮）

- 扩大样本（n>20）验证稳定性；
- 单独处理 `never_started`（agent 启动）这一类失败——**属执行/生命周期问题**；
- 之后才回到 **价值闭环整体度量**（End-to-End），并首次可望 >0%。
