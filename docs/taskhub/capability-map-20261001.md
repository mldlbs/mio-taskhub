# mio-taskhub 能力资产盘点（Capability Map）

| 项 | 值 |
|---|---|
| 日期 | 2026-10-01 |
| 动因 | 第五轮整体评估发现"能力过剩后的价值密度治理"；技术负责人要求先做**能力收敛审计**（不改代码） |
| 方法 | 对每条链路核 **入口（API/UI/MCP）× 数据 × 消费端**，给三选一裁决：**接线 / 降级 / 删除** |
| 原则 | "表 0 行" ≠ "死代码"——必须区分：**真死**、**接线未用**、**条件性为空** |

---

## 0. 先说结论：第五轮"系统性建而不用"**被高估了**

第五轮初判 P1-A"多条链路建了不接消费（含 SLO/dep）"——**经本轮逐条核实，部分不成立**：

| 第五轮说法 | 核实结果 |
|---|---|
| ~~"SLO 采集无消费"~~ | **错**：`ObservabilityView.jsx` 调 `api.sloHistory(24)` 渲染 SLO 可用性/错误预算卡片；快照仍在采集（最新=now） |
| ~~"dep snapshot 缺决策使用"~~ | **部分错**：经由 metrics/report 消费，且过期自动清理（保留 2016/按时间窗） |
| "alertrule 0" | **成立但性质是"接线未用/缺运营入口"**（非死代码） |
| "plan 0" | **成立，且是真孤儿表**（`planner` 用的是内存 `NightPlan` + 文件，不写此表） |
| "taskreview 0" | **成立但性质是"接线未用"**（API+前端齐全，只是无人提交） |

**修正后的真实问题**：不是"大面积建而不用"，而是**少量链路"有入口无使用"或"真孤儿"**。范围比第五轮小得多。

---

## 1. 能力地图（按裁决三分）

### ✅ 活跃（Keep）——有数据且有消费
| 能力 | 入口 | 数据 | 消费端 | 证据 |
|---|---|---|---|---|
| task lifecycle | API/UI/MCP | 298 task | 看板/阶段 | 活跃 |
| claim/run | API/MCP/idle_worker | 137 run | 状态机 | 活跃 |
| **真实 agent 消费** | idle_worker | run exit0 | COMPLETED | 2/2 |
| evidence（ReadEvidence） | claim/submit 门控 | 33 | submit 前置校验 | 活跃 |
| docs chain | API/UI(DocPanel) | 45 task 挂 doc | 质量门/棘轮 | 活跃 |
| idea funnel | API/UI/MCP | 41 idea→36 拆解 | 拆解/讨论 | 活跃 |
| discussion | API/UI/MCP | 53（48 有结论） | 闭环 | 活跃 |
| remediation/insight 消费环 | evaluator | 7 insight | 派生 task（4） | **本轮打通** |
| **built-in alert**（AlertManager） | 进程内 | alertaudit 31（今日 fire/resolve） | 告警状态 | **活跃** |
| **SLO 快照** | 采集循环 | 2016 | **ObservabilityView + report** | **活跃** |
| **dep 快照** | DepMetricsPersist | 20109 | metrics/report | 活跃 |
| event log | emit_event | 1547 | WS/看板 | 活跃 |
| scheduled/cron | API/UI | 111 执行 | 看板 | 活跃 |
| 空闲计划 + 消费 guard | API/UI | — | 拉起 worker | 活跃（guard 生产生效） |
| 自更新 | update service | — | 更新流程 | 实现（未生产验证） |
| templates / roleprompt / ratchet | API | 8/5/29 | 创建任务/提示词/门控 | 活跃 |

### 🟡 接线未用 / 条件性为空（Keep + Activate 或 Freeze）
| 能力 | 入口 | 数据 | 消费端 | 裁决 |
|---|---|---|---|---|
| **custom alertrule** | API ✅ / UI ❌ | 0 | CustomAlertEvaluator（已 wire） | **接线**：补前端运营入口；否则规则永远无人建 |
| **taskreview** | API ✅ / UI ✅(`ReviewPanel`) | 0 | 任务评审流 | **接线 or 降级**：入口齐全但无人用 → 需确认是否有真实评审场景 |
| **appconfig** | 内部(role_prompts) | 0 | risk_vocab（env 优先） | **降级**：条件性空（env 覆盖时为 0），保留 |
| **ideauserpref** | API ✅(dismiss) | 0 | next-action dismiss | **保留**：逻辑活跃，只是尚未有人 dismiss |

### 🔴 真孤儿（Remove 候选）
| 能力 | 入口 | 数据 | 消费端 | 裁决 |
|---|---|---|---|---|
| **`Plan` 表**（models.py:339） | ❌ 无写入口 | 0 | ❌ 无读 | **删除候选**：`planner` 用内存 `NightPlan`+文件，此 DB 表纯孤儿 |
| **outboxevent** | adr.py 写路径存在 | 0 | git_sync worker 存在 | **保留**（非孤儿，是"未触发"；见 event-consistency-model.md） |

---

## 2. 裁决矩阵（技术负责人要求格式）

| 链路 | 当前状态 | 问题 | 决策 |
|---|---|---|---|
| alertrule | 0 | **能力存在，缺运营入口（无前端）** | **接线**（补 UI）或明确场景 |
| plan（表） | 0 | **真孤儿**（被 NightPlan+文件替代） | **删除**（模型层） |
| taskreview | 0 | 入口齐全但无人用 | **接线 or 降级**（需确认场景） |
| SLO | 2016 | ~~无消费~~ **实为有消费**（UI+report） | **无需动** |
| dep snapshot | 20109 | ~~缺决策使用~~ 实为有 metrics/report 消费 | **无需动** |
| appconfig | 0 | 条件性空 | **降级**（保留） |
| ideauserpref | 0 | 逻辑活跃未触发 | **保留** |

---

## 3. 修正后的排序（替换第五轮的 P1-A）

| 序 | 项 | 性质 | 动作 |
|---|---|---|---|
| 1 | **custom alertrule 缺前端入口** | 接线未用 | 补运营入口（接线） |
| 2 | **`Plan` 表孤儿** | 真孤儿 | 删除（或接线到 night plan 存档） |
| 3 | **taskreview 未用** | 接线未用 | 确认场景 → 接线/降级 |
| 4 | observability 测试盲区（2197 行 2 测试） | 可信度 | 补测 |
| 5 | 消费率 32.6% | 价值兑现 | 持续消费 |

**对比第五轮**：把"系统性建而不用"**收敛为 3 条具体链路**；SLO/dep 的"无消费"**撤销**（经核实有消费）。

---

## 4. 系统现在**真正提供**的稳定能力（回答"值不值得维护"）

```
workflow         ├─ task lifecycle ......... ✅ 活跃（298 task）
                 ├─ claim/run .............. ✅ 活跃（137 run，并发验证）
                 └─ evidence ............... ✅ 活跃（33，差异化）

orchestration    ├─ 真实 agent 消费 ........ ✅ 活跃（opencode，2/2）
                 ├─ scheduled/idle plan .... ✅ 活跃（guard 生效）
                 └─ 文档链 + 质量门 ........ ✅ 活跃（45 挂 doc）

ideas            ├─ idea funnel ........... ✅ 活跃（41→36）
                 ├─ discussion ............ ✅ 活跃（53/48 结论）
                 └─ insight→task 消费环 ... ✅ 活跃（7→4，本轮打通）

observability    ├─ event log ............. ✅ 活跃（1547）
                 ├─ SLO/dep 采集+消费 ..... ✅ 活跃（2016/20109）
                 ├─ built-in alert ........ ✅ 活跃（alertaudit 31）
                 └─ custom alertrule ...... 🟡 接线未用（缺 UI）
                 └─ remediation ........... ✅ 活跃

planning         ├─ plan 表 ............... 🔴 真孤儿
                 └─ taskreview ............ 🟡 接线未用

infra            ├─ 自更新 ................ ✅ 实现（未生产验证）
                 └─ security（默认/SSRF/XSS/门控）✅
                 └─ MCP（41 工具 + 风险门控）✅
```

**稳定能力计**：约 **14 条活跃** / 2 条接线未用 / 1 条真孤儿。→ 系统**不是"靠少数链路撑，其余空转"**，而是"绝大多数能力真实在用，仅少量待收敛"。

---

## 5. 对"值不值得维护"的回答

- **值得**：14 条活跃能力有真实数据+消费端，非空壳；
- **代价**：有 2-3 条"接线未用/孤儿"带来**认知与维护成本**，应裁决收敛；
- **第五轮教训修正**：判断"空置"必须核到**消费端与入口**，不能只看表行数（SLO/dep 险些被误判为"无消费"）。

---

## 6. 后续（本轮不改代码）

| 阶段 | 内容 |
|---|---|
| 一（现在） | 本盘点 → 对 3 条链路出**杀/接线**决定（需技术负责人拍板） |
| 二 | observability 补测（snapshot→consumer、alert evaluator、SLO→insight），确认已有能力**可信** |
| 三 | 长期工程：事务边界、API 一致性、测试覆盖 |


---

## 7. 裁决结果（2026-10-01 技术负责人拍板）

| 项 | 决定 | 理由 | 状态 |
|---|---|---|---|
| **Plan 表** | **删除** | 真孤儿（NightPlan+文件已替代） | ✅ 已执行（task 1213d8ad，提交 223dea4；生产库 plan 表 1→0） |
| **custom alertrule** | **保留 API，不补 UI** | built-in alert 已覆盖主闭环；补 UI 引入新运营面/测试成本，无真实需求证据 | ✅ 标记 experimental/internal，待明确场景 |
| **taskreview** | **降级保留，不接线** | ReviewPanel/API 低成本留存；无真实 review 行为；若不改变调度/质量/洞察/metrics 则仅是记录表 | ✅ 冻结 |

### 执行说明
- **Plan 表**：全库引用扫描确认零引用 → 删模型 + 迁移 DROP（幂等）+ 防回潮测试；全量 1081 passed，生产库实测表已移除。
- **alertrule**：保留 `/alert-rules` API 与 CustomAlertEvaluator；不新增前端入口。等出现"谁创建、创建什么规则"的明确答案再开放。
- **taskreview**：保持冻结，不主动接线。

---

## 8. 下一阶段：从"能力资产"转向"**价值证明**"

能力资产盘点完成（从"功能清单"→"能力资产"）。下一步**不是继续清理，而是度量价值闭环是否产生增量价值**。

### 已确认的真实闭环链（事实）
```
Idea → Task → Agent → Evidence → Insight → Remediation → Task
```

### 待测指标（价值闭环度量）
| 指标 | 回答的问题 |
|---|---|
| insight → task 转化率 | 洞察是否可执行 |
| task → completed | 执行能力 |
| completed → evidence | 结果可信度 |
| evidence → 新 insight | 是否自增强 |
| agent 重试率 | 任务质量 |
| 人工介入次数 | 自动化程度 |

**方向**：不再扩功能，转向"证明已有闭环的增量价值"。
