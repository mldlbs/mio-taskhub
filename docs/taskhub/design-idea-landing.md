# 🎯 想法落地闭环 — 设计稿 v1.4

> 把想法收敛为「可验证、可执行、可验收」的任务闭环；Agent 开会降级为关键节点的结构化评审，不做事无休止的闲聊。

> **v1.4 变更（task fe156b05，用户裁决 2026-09-29）**：下一步动作**永不为空**——新增第 6 级规则 `review_items_unconverted`（已关闭评审存在未转任务的行动项 → 提示一键转任务）与第 7 级兜底 `stage_default`（按想法阶段恒给一条常规推进建议）；默认优先级序扩为 7 级，MIO_NEXT_ACTION_ORDER 覆盖语义不变；dismiss 机制对新规则照常生效。
>
> **v1.3 变更（P3 包 D，讨论 3c3b4c90）**：任务图升级**多层上下游闭包** + 环路径 `cycles`（降级行为不回退）+ 节点上限 100；复盘区空壳 → **真实聚合**（Run 成败 + P2 结构化评审记录）；`idea_assumption_link` 关联表落地（**双写兼容**，P1 端点签名不变）；**成功标准结构化裁决关闭**——P3 不做；验收清单 +3。
>
> **v1.2 变更**：下一步动作**默认优先级序**与 dismiss **条件变化即复活**规则；行动项转任务**幂等**；Agent prompt 配置定为**数据库+缓存**并**创建时快照**；高风险判定落地为 **`tags` 字段（P0 加）∩ 可配置词表**；驾驶舱任务图 P0 渲染规则；`/cockpit` 降级细化到**区块级**；验收清单 +4；遗留开放项清零。
>
> **v1.1 变更**：并入评审 8 条意见 — 真源策略、`out_of_scope`、降级契约、回写独立端点、门控最小条目、行动项回写任务图、阶段化模式推荐、下一步动作置顶；3 个待定问题裁决。

---

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.4 |
| 状态 | approved |
| 任务 | 2fe56e6a（P0）/ 8442e38d（P1）/ 286dd112（P2）/ d4bdf530（P3）/ fe156b05（v1.4 下一步动作永不为空） |
| 需求 | docs/taskhub/requirement-idea-landing-p0.md（FR-1~FR-10）、-p1（FR-11~17）、-p2（FR-18~25）、-p3（FR-26~29） |
| 评审 | 两轮（v1.1 并入 8 条，v1.2 并入 6 项 + 2 项裁决）；v1.3 P3 包 D 讨论 3c3b4c90；v1.4 用户裁决（下一步动作永不为空） |
| 更新日期 | 2026-09-29 |

---

## 🧭 模块职责与边界

> **一句话**：为想法（Idea）提供结构化表达与「下一步动作」推进能力；P1 假设分数透传、P2 结构化评审与行动项、P3 完整任务拓扑与复盘（v1.3 已交付）。下表边界为 P0 原始范围，供历史对照。

| 边界内（P0） | 边界外 |
|---|---|
| Idea 8 字段 + 版本化 diff | 假设关联表、实时分数 |
| `/cockpit` 聚合与区块级降级 | 结构化评审 mode/roles |
| 驾驶舱只读分节 + 下一步动作 | 行动项转任务 |
| tags 高风险判定（常量词表） | prompt DB 配置 |
| 任务图一层下游简化渲染 | 完整拓扑/环布局/复盘区 |

---

## 🔩 详细设计

### 📁 变更文件清单

| 文件 | 改动 |
|---|---|
| `mio_taskhub/models.py` | `Idea` +8 字段；`IdeaUserPref`（dismiss 表） |
| `mio_taskhub/migrations.py` | 新列迁移 + dismiss 表 |
| `mio_taskhub/api/ideas.py` | POST/PATCH 白名单扩字段、diff 键 `assumptions[hid]` |
| `mio_taskhub/api/cockpit.py`（新） | `GET /ideas/{id}/cockpit`：并行聚合 + sections |
| `mio_taskhub/next_action.py`（新） | 优先级规则引擎 + dismiss/复活 |
| `web/src/components/IdeasView.jsx` | 驾驶舱分节渲染 + 下一步动作置顶 |
| `web/src/api.js` / `web/src/index.css` | cockpit 接口、区块样式 |
| `tests/test_idea_cockpit.py`、`tests/test_next_action.py`（新） | FR-3/4/6/7 用例 |

### ⚙️ 关键算法

**cockpit 聚合（FR-3/FR-4）**：

```text
并行 gather(idea区 1s, 任务区 1s, 审批区 1s, 复盘区 1s, mio假设区 3s, return_exceptions=True)
→ 每区独立捕获异常：失败 → sections[x].status=degraded + reason
→ 总耗时 >5s：丢弃未就绪区、标 degraded，已就绪照常返回
→ next_action = 规则引擎(idea, 任务, 审批, 讨论) 按 5 级序取首条命中 - dismiss 过滤（condition_snapshot 比对）
```

**下一步动作（FR-6/FR-7）**：

```text
候选规则按配置序生成 → 逐条计算 condition_snapshot（结构化布尔位）
→ 查服务端偏好表：命中未过期 dismiss 且 snapshot 相同 → 跳过；snapshot 变化 → 复活
→ 返回第一条存活规则
```

以下 📦 包 A / 包 B / 包 C 为分包详细设计（数据模型、契约、状态机），实现以本节 + 三包 + P0 实现注记附录为准。

---

## 🧠 设计立场

| 立场 | 说明 |
|---|---|
| 🚫 不做 | Agent 会议室、多 Agent 闲聊达成共识 |
| ✅ 做 | 目标→假设→任务→验证→审批→复盘 的单向收敛 |
| ⚡ 开会定位 | 仅评审节点的结构化工作流：有输入、角色、输出、决策、行动项 |
| 🧍 人类拍板 | 花钱、合规、上线等高风险点保留人工审批 |

> **核心结论**：想法落地缺的不是讨论，是结构化字段 + 串起来的数据 + 一屏可见且能推进的驾驶舱。

---

## 📊 现状盘点

| 闭环环节 | 现有能力 | 缺口 |
|---|---|---|
| 模糊→清晰 | Idea(title/description/status) + 评审 | 无目标/用户/成功标准字段 |
| 假设→验证 | Mio creativity 假设+发酵打分 | 假设与 Idea 是两套数据，未关联 |
| 方案→任务 | breakdown(依赖/截止/验收标准) + doc 链 | 齐 |
| 执行→闭环 | doc 质量门/棘轮/FR 追溯 + task_outcome | 无想法级复盘视图 |
| 评审 | ReviewPanel + 自由式 Discussion | 无角色、无固定输出物 |
| 总览 | 任务看板 / status | 无想法视角一屏、无「下一步」引导 |

---

## 🏗️ 方案总览

| 包 | 内容 | 依赖 |
|---|---|---|
| A | Idea 结构化字段（含 tags）+ 想法驾驶舱（含下一步动作） | 无 |
| B | 假设关联（Idea ⇄ Mio creativity，含降级契约） | A |
| C | 结构化评审（开会降级改造 + 行动项回写） | A |

> **最大风险**（评审共识）：「可空 + 双套数据」长期漂移 → 包 B 明确真源策略；「5 段模板为填而填」→ 门控只做结构校验 + 最小条目数，不假装校验质量。

---

## 📦 包 A — 结构化字段 + 驾驶舱

### 💾 数据模型

`Idea` 新增（全部可空，向后兼容旧数据）：

```python
goal: str = ""              # 模板：给【谁】解决【什么问题】，因为【为什么现在】
success_metric: str = ""    # 模板：【指标】从【现状】到【目标】，在【期限】内
constraints: str = ""       # 外部约束：时间/预算/人手/合规底线
out_of_scope: str = ""      # 明确不做什么，防范围蔓延
assumptions: list = Field(default=None, sa_column=Column(JSON))  # P0 录入缓存，见包 B 真源策略
risks: list = Field(default=None, sa_column=Column(JSON))        # [{text,level,mitigation}]
mvp_scope: str = ""         # MVP 范围
tags: list = Field(default=None, sa_column=Column(JSON))         # ["高风险","合规","用户数据"]，P0 先存
```

### 🔐 字段策略

| 项 | 策略 |
|---|---|
| JSON 存储 | P0 允许；**每次变更必须进 `IdeaChange` diff**（复用现有版本机制） |
| `assumptions` 真源 | P1 起降级为**展示缓存**，真源 = Mio hypothesis + 引用列表 |
| 长期演进 | 拆独立表 `idea_assumption_link(idea_id, hypothesis_id, status, note)`（P3 视用法启动） |
| 字段语义 | goal/success_metric 模板引导；前端轻提示「数字建议放成功标准」，**不拦截** |
| 防蔓延 | `out_of_scope`，驾驶舱与创建表单同级展示 |
| `tags` 时机（v1.2 改 1） | **P0 就加**：红队默认值依赖它，避免字段模型改两次；P0 只存，P2 启用于模式推荐 |
| 高风险判定（v1.2） | `idea.tags ∩ 可配置关键词表`（默认 `["高风险","合规","用户数据","花钱"]`）非空即高风险；**不做正文关键词匹配**（误报率高，未来可选加） |

### 🖥️ 驾驶舱（IdeasView 详情重构）

**置顶：⏭️ 下一步动作**（驾驶舱从「看板」变「推进器」）

v1.2 默认优先级序（可配置，按序取第一条命中的）：

| # | 条件 | 动作 | 理由 |
|---|---|---|---|
| 1 | 缺 goal / success_metric | 补全目标与成功标准 | 没有目标，后面都是空转 |
| 2 | doc 未批准（阻断项） | 去审批 | 卡住执行链 |
| 3 | 存在 blocked 任务 | 解除依赖 T3 | 卡住执行 |
| 4 | 未验证高风险假设 | 优先验证假设 H2 | 影响方向 |
| 5 | 评审会缺行动项 | 补行动项 | 影响闭环 |

> **顺序原则**：先补方向，再解阻断，再验证假设，最后补记录。

**dismiss 规则（v1.2 改）**：

| 项 | 规则 |
|---|---|
| 存储 | **服务端**（用户偏好表）：`{user, rule_id, dismissed_at, condition_snapshot}`——多人协作换设备不丢，且与复活规则配合 |
| 过期 | 7 天自然过期 |
| **复活** | `condition_snapshot` 变化（如「缺 goal」→「有 goal 缺 success_metric」）→ **立即复活**，不等 7 天 |

分节区块（自上而下）：

| 区块 | 数据来源 |
|---|---|
| ⏭️ 下一步动作 | 规则派生（优先级序 + dismiss） |
| 🎯 目标与成功标准 | goal / success_metric / constraints / out_of_scope |
| 🔬 关键假设与验证 | assumptions 缓存 + 包 B 实时分数 |
| 🗺️ MVP 范围 | mvp_scope |
| 📋 任务图 | 关联任务拓扑（渲染规则见下） |
| ⚠️ 风险与依赖 | risks + 任务 blocked |
| 🔔 审批点 | doc 状态 |
| 🕳️ 复盘 | task_outcome + 评审记录 |

**任务图 P0 渲染规则（v1.2）**：

| 场景 | 行为 |
|---|---|
| 范围 | 只显示直接关联任务 + **一层 depends_on** |
| 有环 | 检测到环 → 降级为任务列表 + 警告条，不做拓扑 |
| 数量 | 超过 20 个默认折叠，点开看全图 |

### 🌐 API 变更

| 端点 | 变更 |
|---|---|
| `POST /api/v1/ideas` | 接受新字段（含 tags） |
| `PATCH /api/v1/ideas/{id}` | 新字段进 diff + 版本历史 |
| `GET /api/v1/ideas/{id}/cockpit` | 聚合 + 区块级状态（见下） |

**`/cockpit` 响应结构（v1.2 改 2，降级细化到区块）**：

```json
{
  "sections": {
    "hypotheses": {"status": "degraded", "reason": "mio_timeout", "cached_at": "2026-09-27T04:00:00Z"},
    "tasks":      {"status": "ok"},
    "approvals":  {"status": "ok"}
  },
  "next_action": {"rule_id": "missing_goal", "...": "..."}
}
```

> 前端按 `sections[x].status` 只灰对应区块；**禁止整包 `degraded: true`**（前端不知道灰哪）。

---

## 📦 包 B — 假设关联

### 🔗 真源策略

> **决策**：Idea 侧 `hypotheses: [id]` 只存引用；分数/状态实时经 `mio.creativity_list` 拉取；`assumptions` 降级为本地录入缓存与展示兜底，**不双向同步分数**。

| 步骤 | 实现 |
|---|---|
| 导入 | 想法详情「从发酵假设导入」→ 勾选 active hypotheses |
| 展示 | 驾驶舱显示 novelty/feasibility/impact + 状态徽章 |
| 回写 | **人工确认后**回写，独立端点；不自动回写 |
| 断链 | hypothesis 已删除 → 灰显「已失效」+「解除关联」 |

### 📉 降级契约

| Mio 状态 | 驾驶舱表现 |
|---|---|
| ✅ 可用 | 分数三元组 + 状态徽章 |
| ⏱ 超时/报错 | 「分数暂不可用，最近同步 HH:MM」，**其他区块正常渲染** |
| 🚫 已删除 | 灰显「已失效」+ 解除关联 |
| 性能 | 跨服务调用**超时 3s + 结果缓存 5 分钟**；单区失败在 `sections[].status` 标 `degraded`，不 500 |

### ✍️ 回写契约

```http
PATCH /api/v1/ideas/{id}/assumptions/{hid}   # {status, note, confirmed_by}
```

> 独立端点只改单条，避免 JSON 整列表覆盖丢更新；走版本机制记 IdeaChange（diff 键 `assumptions[hid]`）。P3 拆表后签名不变。

---

## 📦 包 C — 结构化评审（开会改造）

### 🎛️ 讨论双模式

```python
mode: str = "free"   # free=自由讨论 / review=结构化评审
roles: list = ...    # 评审视角标签：["产品","技术","合规","红队"]
```

> **`roles` 定义**：评审视角标签，人类与 Agent 均可挂；人类角色作署名与责任标记。

### 🤖 Agent 角色 prompt 配置（v1.2 裁决）

| 项 | 规则 |
|---|---|
| 存储 | **数据库 + 缓存**（非纯文件）：需要版本历史、按 roles 快照、非开发人员可改 |
| 加载 | 启动加载 + 热更新（配置端点改后使缓存失效） |
| **快照** | 评审会**创建时**把 `roles + prompt 版本` 快照进 Discussion——回看记录可知当时用的哪版 |
| 进行中会话 | 用创建时快照，不受后续配置变更影响 |

### 📋 评审门控

close（`mode=review`）做**结构校验**（不假装校验质量）：

| 段 | 最小要求 |
|---|---|
| 风险清单 | ≥ 1 条 |
| 分歧点 | 有段即可（「无分歧」须写达成一致依据） |
| 建议 | 有段即可 |
| 决策选项 | **≥ 2 个** |
| 行动项 | ≥ 1 条，且结构化 |

```json
{"action_items": [{"id": "ai_1", "owner": "...", "action": "...", "due": "YYYY-MM-DD", "status": "pending", "task_id": null}]}
```

**行动项转任务（v1.2 幂等）**：

| 规则 | 实现 |
|---|---|
| 唯一 | `action_items[].id` 生成时唯一 |
| 写回 | 转换成功 → `task_id` 回写该条 |
| 防重复 | 已转条目按钮变「查看任务」，不再创建 |
| 事务 | 批量转走事务，部分失败整体回滚（或逐条标红可重试） |

### 🧭 角色能力约定

| 角色 | 关注点 |
|---|---|
| 产品 | 需求真伪、MVP 取舍 |
| 技术 | 可行性、成本、依赖 |
| 商业 | 获客/成本/收益 |
| 合规 | 隐私、法务、政策 |
| 红队 | 反用法、失败模式、最坏情况 |

### 🚦 模式推荐（free 不藏死）

| 想法阶段 | 入口默认 | 说明 |
|---|---|---|
| 模糊期（new/fermenting 且无 goal） | `free` | 头脑风暴仍有价值 |
| 方案评审（formed 或已有 goal） | `review` | 可切换 free |
| 高风险（`tags ∩ 词表` 非空） | `review` + **红队默认勾选** | 可取消——默认值优于强制 |

### 🌐 API / MCP 变更

| 接口 | 变更 |
|---|---|
| `POST /discussions` | `mode`/`roles`；`mode=review` 时 roles 必填；创建时快照 prompt 版本 |
| `POST /discussions/{id}/close` | 结构 + 最小条目校验，违规 422 |
| 行动项 | 读取 + `POST .../convert`（幂等）转任务 |
| `taskhub_open_discussion` (MCP) | 透传 `mode`/`roles` |
| 前端入口 | 模板下拉「讨论会 / 评审会（结构化）」，按阶段预选 |

---

## 🗓️ 实施分期

| 期 | 内容 | 增补 |
|---|---|---|
| P0 | 字段（**含 tags**）+ PATCH diff + 驾驶舱只读 + 下一步动作（优先级序 + 服务端 dismiss） | 相比 v1.0/v1.1 全部字段定型，后续不改模型 |
| P1 | 假设导入 + 分数展示 + 断链降级 | +超时 3s/缓存 5min；+回写独立端点；`/cockpit` 区块级 status |
| P2 | mode/roles + 门控 + **prompt 数据库配置/快照** | +最小条目数；+行动项幂等转任务 |
| P3 | 任务拓扑完整版 + 复盘 + 假设关联表 | 环检测/折叠在 P0 按简化规则先行 |

---

## ✅ 验收清单

- [x] 旧 idea 数据（NULL 新字段）全页面无异常
- [x] `PATCH` 新字段产生 IdeaChange diff（含 `assumptions[hid]` 键）
- [x] Mio 超时/报错时仅假设区灰显，其余区块正常渲染
- [x] hypothesis 已删除 → 灰显 + 可解除关联
- [x] 并发两次 `PATCH .../assumptions/{hid}`，两条都进 IdeaChange，无丢更新
- [x] 行动项转任务重复点击不产生重复任务（幂等）
- [x] `mode=review` 缺段或条目不足（风险<1、选项<2、行动项<1）→ 422
- [x] 行动项「一键转任务」生成含验收标准的任务
- [x] 「下一步动作」dismiss 后条件变化自动复活（服务端）
- [x] 优先级序生效：同时命中多条件只显示最高序一条
- [x] 高风险标签（tags ∩ 词表）→ 评审入口默认勾选红队（可取消）
- [x] 评审创建时快照 roles + prompt 版本，进行中会话不受配置热更新影响
- [x] 任务图：有环降级列表+警告；>20 折叠
- [x] 任务图（P3 FR-26）：多层上下游闭包 kind 正确；含环返回 `cycles` 环路径且降级不回退；>100 节点截断 `truncated=true`
- [x] 复盘区（P3 FR-27）：run 成败 summary + 最近明细 + 评审三计数；空数据 ok 全 0；单区异常仅本区 degraded
- [x] 假设关联表（P3 FR-28）：迁移幂等回填；import/PATCH 双写且 P1 端点响应逐键不变
- [x] `mode=free` 行为与现状完全一致（回归）
- [x] MCP `taskhub_open_discussion` 透传 mode/roles
- [x] 全量 pytest 绿

> 本清单 19 项于 2026-09-27 经 P4（task `eb248b6a`）逐项核对通过，证据与实跑记录见 [`acceptance-audit-p4.md`](acceptance-audit-p4.md)（19 行证据表 + 全量回归 924 passed, 1 skipped）。

---

## ✂️ 已裁决（全数关闭）

| 问题 | 裁决 |
|---|---|
| 成功标准结构化？ | P0 纯文本 + 模板提示；**P3 已裁决不做**（v1.3）——字段模型一次定型别回头改：结构化会改 Idea 模型 + 旧数据迁移 + goal 区渲染契约，收益（机器可读进度）不匹配 P3 主线；后续有进度追踪需求另立项 |
| 假设回写权？ | **人工确认后回写**，不自动 |
| 红队强制？ | 不强制；高风险**默认勾选**，可取消 |
| Agent prompt 存储（v1.2） | **数据库 + 缓存**，创建时快照版本 |
| dismiss 同步（v1.2） | **服务端用户偏好表**，不做 localStorage |
| 高风险判定（v1.2） | `tags ∩ 可配置词表`，不匹配正文 |

> 无遗留开放项，**v1.3 冻结（P0~P3 全分期落地完毕）**。

---

## 📦 P0 实现注记（冻结附录）

> 设计已冻结，以下为落地细节，非设计变更。

| # | 坑 | 定案 |
|---|---|---|
| 1 | 词表存哪 | P0 硬编码常量 + 环境变量覆盖；P2 随 prompt 迁 DB。**不用文件** |
| 2 | `condition_snapshot` 粒度 | 存结构化布尔位（如 `{"rule_id":"missing_goal","goal_present":false,"metric_present":false}`），**不存文本**——改错别字不应复活 |
| 3 | `/cockpit` 超时预算 | 每区块独立超时（Mio 3s / 其余 1s）+ `gather(return_exceptions=True)` 并行；单区失败只灰该区；总上限 5s，超时返回已就绪部分 |
| 4 | 「一层 depends_on」方向 | P0 显示**下游**（谁依赖我）——驾驶舱关心「卡住了谁」；上游放任务详情 |
| 5 | 转任务验收标准 | 不假装自动生成：弹窗让用户补一句，跳过则 `acceptance_criteria = action 原文 + 「待补全」` |

**最小交付顺序**（每步独立验收，④ 是体验拐点，⑥ 最易低估）：

```text
1. Idea 字段 + POST/PATCH + IdeaChange diff
2. GET /cockpit（先只聚合 idea 字段，其余区块空壳）
3. 驾驶舱只读分节渲染
4. 下一步动作（优先级序 + 服务端 dismiss + 复活）
5. tags 字段 + 高风险判定（常量词表）
6. 任务图 P0 规则（一层下游 + 环降级 + 折叠）
```

> **⚠️ 两条守则**：① 字段模型一次定型，别回头改；② 每区块独立降级，别让 Mio/任务查询拖垮整个驾驶舱。
