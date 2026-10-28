# 功能删减清单（证据版）

> 生成时间：2026-10-28
> 前提：`docs/taskhub/frozen-scope-20261021.md` 现行策略是「冻结 = 停投入、停调度，代码不删」。本清单是对该策略的**修订提案**，需确认后才执行。

## 一、总览

| 功能 | 后端 | 前端 | 最后改动 | 用量证据 | 测试引用 | 建议 |
|---|---|---|---|---|---|---|
| 想法 / 评审 | 2522 行 / 35 端点 | IdeasView 1695 行 + IdeaSearch 347 | 2026-10-27 | 53 条想法，**1 条采纳**；评审任务 1526 条（1470 是 10-19 风暴）；调度已默认关 | 22 个测试文件 | **下线 UI**，后端冻结 |
| 观测台（SLO/tracing/Prometheus） | 3323 行 / 12 端点 | ObservabilityView 250 | 2026-10-26 | 无任何使用证据；决策已判「单用户无人看指标」 | 15 个测试文件 | **下线 UI**，后端冻结 |
| 记忆网关 / 记忆观测 | 798 行 / 7 端点 | MemoryView 285 + MemoryObservatoryView 388 | 2026-10-05 | 无使用证据；冻结时缺「省了什么时间」的证明 | 6 个测试文件 | **下线 UI**，后端冻结 |
| 素材面板 / 价值发现 | 566 行 / 9 端点 | MaterialPanelView 385 | 2026-10-14 | 冻结项，无使用证据 | 1 个测试文件 | **保留**（作者决定，2026-10-28） |
| 定时任务 | 1524 行 / 14 端点 | ScheduledJobsView 440 | 2026-10-19 | 累计 112 次，**最后一次 2026-10-08**（20 天前） | 5 个测试文件 | 可下线 UI，需先确认与「空闲计划」的依赖 |
| 计划 / 夜间 | 336 行 / 9 端点 | PlanView 637 | 2026-10-05 | 依赖 `~/.mio_taskhub/night_plans/latest.json` | 2 个测试文件 | **保留**（核心排期视图） |
| 模板 | 273 行 / 9 端点 | TemplatesView 241 + TemplatePicker 246 | 2026-10-05 | 8 条模板在用（建任务入口） | 5 个测试文件 | **保留** |
| Mio 运行时 | 1837 行 / 15 端点 | MioRuntimeView 285 | 2026-10-27 | 本周新增经验确认、噪声排除，仍在用 | — | **保留** |
| 文档链 | 2429 行 / 11 端点 | DocPanel 457 | 2026-10-27 | TH-01 全程在用（requirement→plan→review） | 多 | **保留** |
| 门禁 / 完成 | 973 行 / 2 端点 | — | 2026-10-27 | 每次提交都在跑 | 多 | **保留** |
| 分解 / 层级 | 1014 行 / 2 端点 | — | 2026-10-26 | 少量使用（P2a 子任务） | 多 | **保留** |

## 二、删减后的导航变化

现状 **13 项** → 建议 **9 项**：

```
保留：工作流 · 列表 · 空闲计划 · 拓扑 · 甘特 · 素材面板 · 模板 · 统计 · Mio 运行时
隐藏：想法 · 定时 · 观测台 · 记忆              （路由保留，URL 仍可进）
```

## 三、三档做法（风险递增）

### 阶段 1：只减 UI 入口（推荐先做，可回滚）

- 改 `web/src/components/Rail.jsx`：4 个视图移出导航；
- 路由与组件保留，`#/ideas` 等仍可访问；
- 预期收益：导航 13→9，首屏不再加载 4 个大组件（IdeasView 1695 行 + 其余约 1100 行）。

### 阶段 2：真删后端（需同步改决策与宣称）

要一起删的东西：`api/ideas*.py`、`api/observability.py` + `observability/`、`api/memory.py` + `memory_store.py` + `memory_observatory.py`、`api/material_panel.py` / `value_discovery.py` / `insights.py`、`api/scheduled_jobs.py` + `scheduling/`。

**牵连点（必须先解）**：
1. `mio_taskhub/db.py` 直接 import `observability.dep_metrics.DepMetrics` —— 删观测台要先摘掉这条依赖；
2. `scheduling/` 被 `background.py` 用来跑 heartbeat sweep 与 MioDigestJob/ContractJob/MioOutcomeJob —— **不能整块删**，只能删 scheduled-jobs 专属部分；
3. 记忆模块被 README §5.6 宣称、被 `mio_bridge` 无直接依赖，但 `memory_store` 可能被 policy 占位逻辑引用，需先查；
4. 想法子系统被 MCP 工具（`taskhub_review_idea`、`taskhub_add_idea`）暴露，删端点会让已配置的 agent 报错——需同时改 MCP 工具表与 `AGENTS.md`。

### 阶段 3：清死代码（不动产品面）

- 只清确定无人调用的函数与重复工具；
- 明确**不碰** `advance` vs `move`（文档已论证合并会改坏状态机）。

## 四、我的建议

先做**阶段 1**，跑一周看是否真的不点。理由是：

- 删 UI 是**可逆**的（改一行数组就回来），删后端是**不可逆**（只能翻 git）；
- 现在的证据只证明「没用过」，没证明「永远不需要」——冻结决策当初也是这么想的；
- 阶段 1 就能拿走大部分收益（导航清爽 + 首屏少加载 3000+ 行组件），而风险接近零。

若一周后确认不用，再执行阶段 2，并按上面的「牵连点」逐条拆依赖。
