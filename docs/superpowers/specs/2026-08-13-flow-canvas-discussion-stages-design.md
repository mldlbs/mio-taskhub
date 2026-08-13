# 画布式流程视图与讨论阶段标注

日期：2026-08-13
状态：已确认

## 背景

mio-taskhub 已实现研发流程生命周期（stage 推进轴），Web UI 用 7 列泳道展示。用户希望演进为**画布式**节点流，更贴近 superpowers 真实研发流程形态；同时**每个节点要能体现发酵过程**——头脑风暴/对话/讨论记录按阶段归类呈现。

经澄清：
- 画布形态：**横向节点流**，8 个阶段节点，节点大小反映任务数，点节点看该阶段任务列表，任务卡可拖到下一节点推进
- 回溯箭头：**展示真实回溯路径**（设计发现需求不明→回溯需求理解；审查不过→回溯计划/设计）
- 讨论归属：**任务级 + 阶段标注**（Discussion 记录新增 stage 字段，标注讨论发生在哪个阶段，按阶段归类展示）
- 交互：任务卡加讨论标记 + 点开详情抽屉突出讨论记录

## 方案

基于现有 FlowView 演进：新增 Discussion.stage 字段（数据层小改），FlowView 重构为横向节点流画布（含回溯箭头），详情抽屉讨论按阶段分组。

## 数据层（models.py / api）

### Discussion 新增 stage 字段

```python
class Discussion(SQLModel, table=True):
    ...
    stage: str = "brainstorming"   # 讨论发生的研发阶段
```

### API

- `POST /tasks/{id}/discussions` 接受可选 body 参数 `stage`（默认 brainstorming）
- `GET /tasks/{id}/discussions` 响应讨论记录含 `stage` 字段
- 可选：`GET /tasks/{id}/discussions?stage=xxx` 按阶段过滤

### MCP

- `taskhub_add_discussion` 增加可选参数 `stage`（默认 brainstorming）

## 画布视图（FlowView 重构）

**横向节点流**：

```
[需求理解] ──→ [设计] ──→ [计划] ──→ [待执行] ──→ [执行中] ──→ [审查] ──→ [完成]
     │  ↑回溯      │  ↑回溯                                       │  ↑回溯
     └─────────────┴──────────────...─────────────...──────────────┘
```

- 节点为圆角卡片，横向排列，SVG 或 CSS 箭头连接
- **节点大小反映任务数**（count 越多节点越大，使用 scale 或 padding 变化）
- 节点显示：阶段名 + 任务数徽标 + 该阶段讨论数徽标（💬）
- **回溯箭头**：虚线弧形箭头标注真实回溯路径：
  - design → brainstorming（设计时需求不明）
  - planning → design（计划时发现设计不足）
  - review → planning（审查不过需改计划）
  - review → design（审查不过需改设计）
  - review → ready（审查不过直接重做）
- 点击节点 → 下方展开该阶段任务列表（内嵌卡片，复用现有 flow-card）
- 任务卡可**拖到下一节点**推进（弹窗填产出物，同现状）
- cancelled 任务不显示在节点流（单独过滤或隐藏）

## 详情抽屉讨论按阶段分组

- 任务卡加 💬 标记（带讨论数量）
- 打开详情抽屉：讨论记录区**按 stage 分组**展示：
  - 「需求理解」组：stage=brainstorming 的讨论
  - 「设计评审」组：stage=design
  - 「计划评审」组：stage=planning
  - 「审查验收」组：stage=review
  - 其他：stage=其他
- 每组显示讨论 topic/summary/conclusions，可展开完整对话

## 测试

- Discussion stage 字段 CRUD、API 传参默认值、按 stage 过滤
- MCP add_discussion 带 stage
- 画布渲染（前端构建 + 浏览器验证节点/箭头/拖拽）

## 不做的事（YAGNI）

- 不做节点自由拖拽摆放（固定横向布局）
- 不做阶段讨论独立表（仍挂任务下）
- 不做画布缩放/平移
