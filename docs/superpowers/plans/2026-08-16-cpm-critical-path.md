# CPM 关键路径 + 跨列依赖连线 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 TopoView 高亮单条关键路径（CPM）+ 在 FlowView 绘制跨列依赖虚线连线。

**Architecture:** 新建纯函数 `web/src/cpm.js`（computeCPM，不依赖 React/DOM）算 ES/EF/LS/LF/float/单条关键路径/并行度；TopoView 集成信息栏 + 关键路径高亮。FlowView 画布加全局 SVG overlay 绘制与当前展开列相关的跨列依赖虚线，坐标用容器 rect + scroll 统一换算，observer 驱动重绘。

**Tech Stack:** React 18 / Vite / Node（验证脚本）

---

### Task 1: `web/src/cpm.js` 纯函数 + 验证

**Files:**
- Create: `web/src/cpm.js`
- Create: `web/scripts/verify-cpm.mjs`（临时验证脚本，验证后可保留）

- [ ] **Step 1: 写实现 `web/src/cpm.js`**

```javascript
// web/src/cpm.js
// 独立纯函数：关键路径法（CPM）。不依赖 React/DOM，TopoView/FlowView/Dashboard 可复用。
export const DEFAULT_DURATION = 60

// 任务时长（分钟）：已完成=0，进行中按剩余时间退化，其余用 est_duration
export function taskDuration(task) {
  const stage = task.stage
  const state = task.state
  const est = Number(task.est_duration_min) || DEFAULT_DURATION
  if (state === 'completed' || stage === 'done' || stage === 'cancelled') return 0
  if (stage === 'implementing') return Math.round(est * 0.5)
  if (stage === 'review') return Math.round(est * 0.25)
  return est
}

export function computeCPM(tasks) {
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
  const dur = {}
  tasks.forEach(t => { dur[t.id] = taskDuration(t) })

  // 拓扑序（Kahn）
  const deps = Object.fromEntries(tasks.map(t => [t.id, (t.depends_on || []).filter(d => byId[d])]))
  const succ = {}
  tasks.forEach(t => { succ[t.id] = [] })
  const indegree = {}
  tasks.forEach(t => { indegree[t.id] = 0 })
  tasks.forEach(t => {
    deps[t.id].forEach(d => { indegree[t.id] += 1; succ[d].push(t.id) })
  })
  const order = []
  const queue = tasks.filter(t => indegree[t.id] === 0).map(t => t.id)
  const seen = new Set(queue)
  while (queue.length) {
    const id = queue.shift()
    order.push(id)
    succ[id].forEach(c => {
      indegree[c] -= 1
      if (indegree[c] === 0 && !seen.has(c)) { seen.add(c); queue.push(c) }
    })
  }

  // 正向：ES / EF
  const es = {}, ef = {}
  tasks.forEach(t => { es[t.id] = 0; ef[t.id] = 0 })
  order.forEach(id => {
    const s = deps[id].length ? Math.max(...deps[id].map(d => ef[d])) : 0
    es[id] = s
    ef[id] = s + dur[id]
  })
  const total = tasks.length ? Math.max(...tasks.map(t => ef[t.id])) : 0

  // 反向：LF / LS
  const ls = {}, lf = {}
  tasks.forEach(t => { lf[t.id] = total; ls[t.id] = total - dur[t.id] })
  ;[...order].reverse().forEach(id => {
    const l = succ[id].length ? Math.min(...succ[id].map(c => ls[c])) : total
    lf[id] = l
    ls[id] = l - dur[id]
  })

  // 浮动
  const float = {}
  tasks.forEach(t => { float[t.id] = ls[t.id] - es[t.id] })

  // 单条关键路径：从 EF==total 的终节点回溯，逐级取 EF 最大的前驱
  const terminals = tasks.filter(t => ef[t.id] === total)
  const start = terminals.length ? terminals[0] : (tasks[0] ? tasks[0].id : null)
  const criticalPath = []
  let cur = start
  while (cur) {
    criticalPath.push(cur)
    const preds = deps[cur]
    if (!preds.length) break
    cur = preds.reduce((a, b) => (ef[b] > ef[a] ? b : a))
  }
  const critical = {}
  tasks.forEach(t => { critical[t.id] = criticalPath.includes(t.id) })

  // 并行度：max 拓扑层宽（用 EF 分层：层 = 依赖链深度）
  const layer = {}
  tasks.forEach(t => { layer[t.id] = 0 })
  order.forEach(id => {
    deps[id].forEach(d => { layer[id] = Math.max(layer[id], layer[d] + 1) })
  })
  const layerWidth = {}
  tasks.forEach(t => { layerWidth[layer[t.id]] = (layerWidth[layer[t.id]] || 0) + 1 })
  const parallel = Object.keys(layerWidth).length
    ? Math.max(...Object.values(layerWidth))
    : 0

  return {
    total,
    es, ef, ls, lf, float,
    criticalPath,
    critical,
    parallel,
  }
}
```

- [ ] **Step 2: 写验证脚本 `web/scripts/verify-cpm.mjs`**

```javascript
// node scripts/verify-cpm.mjs
import { computeCPM } from '../src/cpm.js'

const tasks = [
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'b', est_duration_min: 60,  depends_on: ['a'], state: 'queued', stage: 'ready' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'd', est_duration_min: 30,  depends_on: ['b', 'c'], state: 'queued', stage: 'ready' },
]
// 依赖: a→b→d, c→d。a+b=180, c=180 → a→b→d=210 是关键路径
const r = computeCPM(tasks)
console.assert(r.total === 210, `total=${r.total} expected 210`)
console.assert(r.criticalPath.join(',') === 'a,b,d', `path=${r.criticalPath}`)
console.assert(r.critical.a && r.critical.b && r.critical.d && !r.critical.c, 'critical flags')
console.assert(r.float.c === 30, `float.c=${r.float.c}`)

// 已完成任务不阻塞
const done = [
  { id: 'x', est_duration_min: 999, depends_on: [], state: 'completed', stage: 'done' },
  { id: 'y', est_duration_min: 60, depends_on: ['x'], state: 'queued', stage: 'ready' },
]
const r2 = computeCPM(done)
console.assert(r2.total === 60, `done total=${r2.total}`)

// 无 est_duration 用默认值
const noEst = [{ id: 'n', depends_on: [], state: 'queued', stage: 'ready' }]
const r3 = computeCPM(noEst)
console.assert(r3.total === 60, `noEst total=${r3.total}`)

// 悬挂依赖忽略
const dangling = [
  { id: 'p', est_duration_min: 30, depends_on: ['ghost'], state: 'queued', stage: 'ready' },
]
const r4 = computeCPM(dangling)
console.assert(r4.total === 30, `dangling total=${r4.total}`)

console.log('CPM verify OK:', JSON.stringify({ total: r.total, path: r.criticalPath, parallel: r.parallel }))
```

- [ ] **Step 3: 运行验证**

Run: `node web/scripts/verify-cpm.mjs`
Expected: `CPM verify OK: {"total":210,"path":["a","b","d"],"parallel":2}`（console.assert 全通过则脚本正常结束）

- [ ] **Step 4: 全量回归（后端）**

Run: `python -m pytest tests/ -q` — Expected PASS（228，无后端改动）

- [ ] **Step 5: Commit**

```bash
git add web/src/cpm.js web/scripts/verify-cpm.mjs
git commit -m "feat: cpm.js 纯函数（ES/EF/LS/LF/float/单条关键路径/并行度）"
```

### Task 2: TopoView 集成 CPM（信息栏 + 关键路径高亮 + float）

**Files:**
- Modify: `web/src/components/TopoView.jsx`
- Modify: `web/src/index.css`

- [ ] **Step 1: Update TopoView.jsx**

Read `web/src/components/TopoView.jsx` fully (110 lines). Add CPM import and integration:

```jsx
import { useMemo, useState } from 'react'
import { prio, fmtDur } from '../constants'
import { computeCPM } from '../cpm'
```

In the component, compute CPM:

```jsx
  const cpm = useMemo(() => computeCPM(tasks), [tasks])
```

Add a metrics bar after the head:

```jsx
      <div className="topo__head">
        <h2 className="topo__title">依赖拓扑</h2>
        <span className="topo__count">{tasks.length} 个任务 · {layers.length} 层</span>
      </div>
      <div className="topo__metrics">
        <span>总工期 {fmtDur(cpm.total)}</span>
        <span>关键路径 {cpm.criticalPath.length} 任务</span>
        <span>并行度 {cpm.parallel}</span>
        <span>阻塞 {tasks.filter(isBlocked).length}</span>
      </div>
```

Note: `isBlocked` is defined in the component (line 52). `fmtDur(0)` returns "0m" — for total=0 show "0m".

Add critical highlight to nodes. In the node render, compute `crit` and add class + float display:

```jsx
              const crit = cpm.critical[t.id]
              const flt = cpm.float[t.id]
```
```jsx
                <button key={t.id} className={`topo-node topo-node--${tone}${hl ? ' is-hovered' : ''}${crit ? ' is-critical' : ''}`}
```
```jsx
                  <div className="topo-node__stats">
                    <span>{fmtDur(t.est_duration_min)}</span>
                    <span>{blocked ? '阻塞' : (crit ? '关键' : `浮动 ${fmtDur(flt)}`)}</span>
                  </div>
```

- [ ] **Step 2: Add CSS**

Append to `web/src/index.css`:

```css
.topo__metrics { display: flex; gap: 16px; font-size: 12px; color: var(--ink-dim, #6b7280); flex-wrap: wrap; }
.topo__metrics span { background: var(--bg-soft, #181b20); border: 1px solid var(--border, #2a2e35); border-radius: 6px; padding: 4px 10px; }
.topo-node.is-critical { border-color: var(--accent, #3ddc97); box-shadow: 0 0 0 1px var(--accent, #3ddc97), 0 0 12px rgba(61,220,151,0.25); }
```

- [ ] **Step 3: Build + verify**

Run: `cd web && npm run build` — exit 0
Run: `node web/scripts/verify-cpm.mjs` — still passes
Run: `python -m pytest tests/ -q` — PASS

- [ ] **Step 4: Commit**

```bash
git add web/src/components/TopoView.jsx web/src/index.css
git commit -m "feat(web): TopoView 关键路径高亮 + 总工期/并行度/阻塞信息栏"
```

### Task 3: FlowView 跨列依赖连线（全局 SVG）

**Files:**
- Modify: `web/src/components/FlowView.jsx`
- Modify: `web/src/index.css`

- [ ] **Step 1: Add global cross-column edges**

Read `web/src/components/FlowView.jsx` fully (currently ~280 lines with drag + same-column edges). Add a global SVG overlay for cross-column dependencies.

Add state and ref:
```jsx
  const [globalEdges, setGlobalEdges] = useState([])
  const canvasRef = useRef(null)
```

Compute global edges — only edges touching the expanded column. Add a compute function and observer effect:

```jsx
  const computeGlobalEdges = useCallback(() => {
    if (expanded === null) { setGlobalEdges([]); return }
    if (!canvasRef.current) return
    const container = canvasRef.current
    const containerRect = container.getBoundingClientRect()
    const srcRect = container.querySelector('.flow-node')
    const edges = []
    // 列锚点：每列 flow__step 的中心
    const steps = container.querySelectorAll('.flow__step')
    const colCenters = {}
    steps.forEach(st => {
      const colId = st.getAttribute('data-col')
      if (!colId) return
      const r = st.querySelector('.flow-node')?.getBoundingClientRect()
      if (r) colCenters[colId] = {
        x: r.left - containerRect.left + container.scrollLeft + r.width / 2,
        y: r.top - containerRect.top + container.scrollTop + r.height / 2,
      }
    })
    flowTasks.forEach(t => {
      ;(t.depends_on || []).forEach(depId => {
        const dep = flowTasks.find(x => x.id === depId)
        if (!dep || dep.stage === t.stage) return  // 只画跨列
        const from = colCenters[dep.stage]
        const to = colCenters[t.stage]
        if (!from || !to) return
        const touchesExpanded = t.stage === expanded || dep.stage === expanded
        if (!touchesExpanded) return  // 只画与当前展开列相关的跨列边
        edges.push({ id: `${depId}->${t.id}`, from, to })
      })
    })
    setGlobalEdges(edges)
  }, [flowTasks, expanded])

  useEffect(() => {
    computeGlobalEdges()
    if (!canvasRef.current) return
    const ro = new ResizeObserver(() => requestAnimationFrame(computeGlobalEdges))
    ro.observe(canvasRef.current)
    const mo = new MutationObserver(() => requestAnimationFrame(computeGlobalEdges))
    mo.observe(canvasRef.current, { childList: true, subtree: true })
    return () => { ro.disconnect(); mo.disconnect() }
  }, [computeGlobalEdges, expanded])
```

Note: `flowTasks` is already memoized (from Task 6 of the prior feature). Add `data-col={s.id}` to each `flow__step` div (in the STAGES.map). Add `ref={canvasRef}` and `data-col` to the canvas:

```jsx
    <div className="flow" ref={canvasRef}>
```

And in the STAGES.map:
```jsx
            <div key={s.id} data-col={s.id} className="flow__step"
```

Add the global SVG (before or after the nodes, inside `.flow__canvas`):

```jsx
            <svg className="flow__global-edges" aria-hidden="true">
              {globalEdges.map(e => (
                <line key={e.id} x1={e.from.x} y1={e.from.y} x2={e.to.x} y2={e.to.y}
                      className={`flow__global-edge${hoveredId && (e.id.startsWith(hoveredId) || e.id.endsWith('->' + hoveredId)) ? ' is-active' : ''}`} />
              ))}
            </svg>
```

Hover: reuse `hoveredId` state (already exists for same-column edges). The `is-active` check uses the edge id `depId->taskId`.

- [ ] **Step 2: Add CSS**

Append to `web/src/index.css`:

```css
.flow { position: relative; }
.flow__global-edges { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 0; overflow: visible; }
.flow__global-edge { stroke: var(--ink-dim, #6b7280); stroke-width: 1; stroke-dasharray: 4 3; opacity: 0.35; }
.flow__global-edge.is-active { stroke: var(--accent, #3ddc97); stroke-width: 2; opacity: 1; stroke-dasharray: none; }
```

Check existing `.flow` position — if it already has `position: relative`, skip adding it.

- [ ] **Step 3: Build + verify**

Run: `cd web && npm run build` — exit 0
Run: `python -m pytest tests/ -q` — PASS

- [ ] **Step 4: Playwright 验证（可选）**

用 hub 48620 打开 FlowView，展开有跨列依赖的列，确认画布有 `flow__global-edge` 元素。若生产库无跨列依赖任务，可先用 API 造一个测试依赖再验证。

- [ ] **Step 5: Commit**

```bash
git add web/src/components/FlowView.jsx web/src/index.css
git commit -m "feat(web): FlowView 跨列依赖连线（全局 SVG + 坐标统一 + hover 高亮）"
```

### Task 4: 回归 + 文档 + 记忆

- [ ] **Step 1: Full backend regression**

Run: `python -m pytest tests/ -q` — Expected PASS（228）

- [ ] **Step 2: Frontend build**

Run: `cd web && npm run build` — exit 0
Run: `node web/scripts/verify-cpm.mjs` — PASS

- [ ] **Step 3: Playwright 验证**

- TopoView：有依赖任务时显示信息栏（总工期/关键路径/并行度/阻塞）+ 关键路径节点高亮
- FlowView：跨列依赖虚线渲染 + hover 高亮（需先造跨列依赖任务）

- [ ] **Step 4: Update docs**

`docs/memory-workbench-p0-2026-08-15.md` 追加：CPM 关键路径 + 跨列依赖连线完成。

- [ ] **Step 5: Commit**

```bash
git add docs/memory-workbench-p0-2026-08-15.md
git commit -m "docs: CPM 关键路径 + 跨列依赖连线说明"
```
