# 甘特图视图 + 多关键路径 + 资源感知

日期：2026-08-16
状态：已确认（brainstorming 结论）

## 背景

cpm.js（CPM 关键路径）+ TopoView 高亮 + FlowView 跨列连线已交付。本 spec 落地三个增强：

1. **甘特图视图**：基于 CPM ES/EF 的任务时间轴展示。
2. **多关键路径枚举**：全部 float≈0 的关键路径集合（不只一条）。
3. **资源感知**：并行度峰值区间 + 资源冲突标注。

三者均为前端增强（后端无改动），共享 cpm.js。

## A. cpm.js 扩展（纯函数）

保持向后兼容（`criticalPath`/`critical` 字段保留），新增：

### A1. 多关键路径 `allCriticalPaths`

```javascript
// 返回 [[id, id, ...], ...]：所有由 float 接近 0 的节点组成的 root→terminal 路径
allCriticalPaths: [['a','b','d'], ['c','d']]
```

- 判据：`float[id] <= EPSILON`（`EPSILON = 0.5` 分钟，容忍浮点误差）。
- 实现：DFS 遍历 float≈0 节点子图，收集每条从入度为 0 到出度为 0 的路径。
- `critical` 标志改为"在任一关键路径上"。

### A2. 并行度曲线 `parallelProfile`

```javascript
// 按时间区间统计并行任务数（每任务 ES→EF 区间覆盖计数）
parallelProfile: [{ start: 0, end: 60, count: 2 }, ...]
```

- 用扫描线：所有任务的 [ES, EF) 区间的 start/end 事件排序，逐段计数。
- 区间用分钟。

### A3. 资源冲突 `resourceConflicts`

```javascript
// 给定可用执行者数（默认 = 活跃 agent 数或配置阈值），并行数超阈值的区间
resourceConflicts: [{ start, end, count }]
```

- 阈值：默认 `maxParallel = 调度器可见的活跃 agent 数`（前端取 `target_agent_type` 去重数或传入）。
- 实现：从 parallelProfile 筛 `count > maxParallel` 的区间，合并相邻。

### 签名

```javascript
computeCPM(tasks, opts = {}) -> {
  total, es, ef, ls, lf, float,        // 原有
  criticalPath, critical, parallel,    // 原有（critical 改为"在任一关键路径上"）
  allCriticalPaths,                    // 新增
  parallelProfile,                     // 新增
  resourceConflicts,                   // 新增（opts.maxParallel 控制阈值）
}
```

## B. 甘特图视图（新组件 `GanttView.jsx`）

### B1. 数据

- 输入 `tasks`（listTasks 结果，含 est_duration_min/depends_on/stage/state/project/target_agent_type）。
- `computeCPM(tasks)` 得 ES/EF/float/critical/allCriticalPaths/parallelProfile/resourceConflicts。

### B2. 行组织（分组切换，`groupMode` 状态）

| 模式 | 行 |
|---|---|
| `task`（任务行） | 每任务一行 |
| `agent`（agent 泳道） | 按 `target_agent_type` 分组（或已分配 agent） |
| `project`（项目分组） | 按 `project` 字段分组 |

- 切换按钮组（task/agent/project）。

### B3. 绘制

- **时间轴**：x 轴 0 → `total`，刻度自适应（≤2h 按 30min，≤12h 按 2h，更大按天）。
- **任务条**：ES→EF 实心（宽度=时长），关键路径 `is-critical`（红色），普通中性。
- **浮动条**：EF→LS 的灰色虚线延伸（float 可视化）。
- **依赖箭头**：前驱条 EF → 本任务 ES（同 TopoView/FlowView 的虚线样式）。
- **并行度/冲突背景**：`parallelProfile` 画半透明背景条；`resourceConflicts` 区域加警示色标记。
- **交互**：点击任务条打开详情抽屉（`onOpen`）；hover 高亮上下游（复用 connectedIds 思路）。
- **入口**：Rail 加「甘特」（`view === 'gantt'`）。

### B4. 样式

```css
.gantt { padding: 24px; overflow: auto; }
.gantt__bar { ... }
.gantt__bar.is-critical { background: var(--danger, #ff5c5c); }
.gantt__float { background: repeating-linear-gradient(...); }
.gantt__conflict { background: rgba(255, 92, 92, 0.15); }
```

## C. TopoView 增强

- 关键路径显示切换：单条（默认）↔ 全部关键路径。
- 切换时 `critical[t.id]` 用 `allCriticalPaths` 并集（或单条）。
- 信息栏补「冲突 N 段」（`resourceConflicts.length`）。

## 验证

- `verify-cpm.mjs` 扩展：
  - `allCriticalPaths` 对 A→B→D + C→D 返回 `[['a','b','d'],['c','d']]`
  - `parallelProfile` 峰值正确
  - `resourceConflicts(maxParallel=1)` 标出冲突区间
- Playwright：
  - 甘特图渲染（任务条 + 关键路径红条 + 浮动条 + 分组切换）
  - TopoView 全部关键路径高亮
- 后端 `python -m pytest tests/ -q` 保持 228 全绿。

## 不做的事（YAGNI）

- 不做真实日期排期（任务无开始日期字段，用 CPM 相对时间）。
- 不做拖拽改排期（只读展示）。
- 不做资源均衡重排算法（只标注冲突，不自动调整）。
- 不做后端 CPM（数据量小，前端直接算）。
