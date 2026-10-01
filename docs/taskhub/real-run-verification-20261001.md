# 真实运行验证报告（转段第一步）

| 项 | 值 |
|---|---|
| 日期 | 2026-10-01 |
| 前置 | 冻结节点（docs/taskhub/system-assessment-20260930-round3.md §15） |
| 目的 | 验证"已有机制在真实 Agent 持续消费下能否产生可观测业务价值" |
| 状态 | **首个真实消费闭环已跑通**（非"消费率已达标"——见 §边界） |

## 1. 前置：解决 Agent 可用性

| agent | 结论 | 依据（实测） |
|---|---|---|
| opencode | ✅ **可用** | npm 装 `opencode-ai@1.18.34`，`opencode run` 非交互返回 |
| codex | ❌ 不可用 | `403 INSUFFICIENT_BALANCE` |
| hermes | ⚠️ 过慢 | 极简 prompt >3.5min |

**opencode 安装关键坑（可复用）**：
- `curl -fsSL https://opencode.ai/install | bash` 不适用 Windows（无 bash）；
- `npm install -g opencode-ai` 在本机只装了 **479 字节占位 exe**（"postinstall script was not run"）——根因：**该 npm 启用 `allowScripts` 供应链防护，默认拦截 postinstall**；
- 正解：`npm install -g --allow-scripts=opencode-ai opencode-ai` → 得 180MB 真二进制（PE 0x8664 x64）。

## 2. 配置空闲计划

```
PUT /api/v1/nightrun/config
{ enabled: true, window_start: "00:00", window_end: "23:59",
  agents: [{ agent: "idle-worker-opencode", command:
    "python idle_worker.py idle-worker-opencode --project {project} --cli-prefix \"opencode run\" --once --max 3",
    cwd: "<安装目录 dist/mio-taskhub>" }],
  projects: ["agent-dev"] }
```

## 3. 真实消费链路（实测）

```
idle_worker 启动 → register → claim(task=db05763c) → opencode run "<prompt>"
  → 提交 result → run FINISHED exit_code=0
```

**证据（事实）**：
- idle_worker 日志：`claimed task=db05763c run=ac7f64de`；`exec(argv): ['D:\\node_global\\opencode.cmd', 'run', ...]`（**`.ps1→.cmd` 解析生效**——上轮修复在真实路径上验证）；`submitted run=ac7f64de success=True`。
- run `ac7f64de`：`FINISHED / exit_code=0 / progress=100`，result 为 opencode **真实产出的任务看板 markdown**（非占位）。
- task `db05763c`（`[insight] taskhub_task_failure_rate`）：**QUEUED → COMPLETED**。

第二轮（`a4d6b5ab` → run `4ae6740c`）同样 **success=True → COMPLETED**。

## 4. 转段四指标（当前样本）

| 指标 | 冻结基线 | 当前 | 判定 |
|---|---|---|---|
| **消费率**（有 run/task） | 32.5%（95/292） | **97/296 = 32.8%** | 单点微升；**尚未达 >60%** |
| **今日真实消费成功率** | — | **2/2 = 100%** | 首个正样本 |
| **洞察跟进闭环** | 2 任务未处理 | **2 任务 COMPLETED**（且新派生 2 个 `[insight]`，环持续运转） | **闭环末端首次被真实处理 ✓** |
| **heartbeat_timeout** | 24（历史） | 仍为历史（**无修复后新样本**） | 未验证 |
| 完成率 | 67/292 | 71/296 | 微升 |

## 5. 关键发现（本轮真实运行暴露）

1. **闭环是"活"的**：处理完 2 个 `[insight]` 后，评估又派生 2 个新的（330dd670/71710851）——**洞察消费环持续运转**（metric→insight→task→处理→新 metric）。这是"价值闭环"从"断裂"到"运转"的实证。
2. **`.ps1→.cmd` 修复在真实路径被验证**（否则 opencode.exe 无法被 idle_worker 执行）。
3. **消费率提升需要持续/并发消费**：单次 `--max` 消费 1-2 个，对 296 总量的占比提升慢；**真正提升需空闲计划持续在窗口内轮询**。

## 6. 边界（诚实声明）

- 本报告证明的是"**真实消费闭环跑通 + 洞察闭环末端闭合**"（**事实**）；
- **不代表"消费率已改善到目标"**——97/296 仅比基线 +2；
- heartbeat_timeout 修复效果**仍无新样本**（需真实长任务）；
- 样本量小（2 次成功 run），**统计意义有限**，需持续运行积累。

## 7. 下一步（仍在冻结纪律内，仅配置/运行，不改功能代码）

1. 让空闲计划**持续在窗口内消费**（已 enabled=true、window 全天），积累样本；
2. 观察消费率是否随持续消费上升；
3. 收集 heartbeat_timeout 修复后的**新样本**（真实长任务）；
4. 达样本量后，重出四指标对比。
