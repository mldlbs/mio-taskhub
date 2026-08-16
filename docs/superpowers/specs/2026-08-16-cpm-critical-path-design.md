# CPM 关键路径 + 跨列依赖连线

日期：2026-08-16
状态：已确认（brainstorming 结论）

## 背景

编排视图已有 TopoView（Kahn 分层 + depth/indegree/outdegree）与 FlowView（单列内依赖连线 + 拖拽换阶段）。本 spec 落地两个剩余增强：

1. **CPM 关键路径**：在 TopoView 中按任务时长计算关键路径并高亮。
2. **跨列依赖连线**：在 FlowView 画布层绘制跨 stage 的依赖连线。

二者共享"依赖图"基础，均为前端增强（后端无改动）。

## A. CPM 关键路径（TopoView 增强）

### A1. 纯函数 `web/src/cpm.js`

新文件，导出 `computeCPM(tasks)`：

```javascript
// tasks: [{id, est_duration_min, depends_on, state, stage}]
// 返回 { total, es, ef, ls, lf, float, critical }
```

**算法**（est_duration_min 为权重）：

```
正向传递（earliest）:
  ES[node] = 0（无依赖）或 max(EF[deps])
  EF[node] = ES[node] + dur[node]
总工期 total = max(EF)（所有任务）

反向传递（latest）:
  LF[node] = total（无后继）或 min(LS[succ])
  LS[node] = LF[node] - dur[node]

浮动: float[node] = LS[node] - ES[node]
关键路径: float == 0 且非终态
```

**已完成/正在执行的任务**：`dur = 0`（done/cancelled/implementing/review 不再阻塞后续），但上游仍正常参与计算。这样关键路径反映"剩余工作"的最长链。

- `state == "completed"` 或 `stage == "done"` / `stage == "cancelled"` → `dur = 0`
- 悬挂依赖（id 不在 tasks）忽略。

### A2. TopoView 使用

- 顶部显示「总工期 Xh · 关键路径 N 任务」（`total` 四舍五入到小时）。
- 关键路径节点加 `is-critical` class（accent 边框 + 弱发光）。
- 节点 stats 区显示 float：`float === 0 ? '关键' : '浮动 Xh'`（替换或补充现有 `↓out ↑in`）。
- hover 高亮逻辑不变。

### A3. 测试

- 纯函数手测：写一个临时 Node 脚本验证 `computeCPM`（无 Jest，项目前端无单测）。
- Playwright：TopoView 显示总工期 + 关键路径高亮。
- 后端 228 测试保持全绿（无后端改动）。

## B. 跨列依赖连线（FlowView 增强）

### B1. 全局 SVG overlay

- 保留现有单列内连线（`flow__edges` SVG 在展开面板内）。
- 新增全局 SVG：`flow__canvas` 层绝对定位覆盖整个画布，绘制跨列依赖线。
- **锚点**：源列节点块（`flow-node` 按钮）中心 → 目标列节点块中心。
- **范围控制（防蜘蛛网）**：只画与"当前展开列"相关的跨列边：
  - 其他列 → 当前展开列（前置依赖来自其他列）
  - 当前展开列 → 其他列（本列任务是其他列的依赖）
  - 不画两个都非展开列之间的边。
- **重绘**：复用 observer 模式（ResizeObserver + MutationObserver + rAF），在展开切换/窗口缩放/任务变更时重算 `getBoundingClientRect`。
- **hover**：hover 卡片 → 高亮其所有连线（列内 + 跨列，统一 `is-active` class）。

### B2. 样式

```css
.flow__global-edges { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 0; }
.flow__global-edge { stroke: var(--ink-dim, #6b7280); stroke-width: 1; stroke-dasharray: 4 3; opacity: 0.35; }
.flow__global-edge.is-active { stroke: var(--accent, #3ddc97); stroke-width: 2; opacity: 1; stroke-dasharray: none; }
```

跨列线用虚线（区别于列内实线）。

## 验证

- `npm run build` 通过。
- Playwright：
  - TopoView 有关键路径任务时显示总工期 + 高亮节点。
  - FlowView 展开有跨列依赖的列时，画布出现虚线跨列连线；hover 卡片高亮。
- 后端 `python -m pytest tests/ -q` 保持 228 全绿。

## 不做的事（YAGNI）

- 不做甘特图视图（TopoView 高亮已够）。
- 不做 ES/EF/LS/LF 全量展示（节点 float 已够）。
- 不做资源均衡/多关键路径枚举（单条关键路径已够）。
- 不做后端 CPM（数据量小，前端直接算）。
