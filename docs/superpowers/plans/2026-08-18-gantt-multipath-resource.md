# 甘特图视图 + 多关键路径 + 资源感知 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地甘特图视图、全部关键路径枚举、并行度/资源冲突感知三个前端增强（后端零改动）。

**Architecture:** 全部改动在 `web/` 前端，共享纯函数 `cpm.js`。`computeCPM` 扩展返回 `allCriticalPaths`（DFS 收集 float≈0 的 root→terminal 路径）、`parallelProfile`（扫描线统计 [ES,EF) 区间覆盖数）、`resourceConflicts`（超阈值区间合并）。新组件 `GanttView.jsx` 基于 CPM 结果绘制时间轴/任务条/浮动条/依赖箭头/分组切换。`TopoView` 增加"全部关键路径"切换。

**Tech Stack:** React (JSX, vite), 纯 JS 算法（无依赖）, CSS 变量（index.css）, node scripts/verify-cpm.mjs 作为纯函数测试载体, Playwright 做 UI 验证。

**验证基线:** 后端 `python -m pytest tests/ -q` 保持 245 全绿（无后端改动）；`node scripts/verify-cpm.mjs` 通过；`npm --prefix web run build` 通过。

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `web/src/cpm.js` | 修改 | 新增 EPSILON、allCriticalPaths、parallelProfile、resourceConflicts；critical 改为"任一关键路径上" |
| `web/scripts/verify-cpm.mjs` | 修改 | 新增三个函数的断言 |
| `web/src/components/GanttView.jsx` | 创建 | 甘特图渲染（时间轴/任务条/浮动条/依赖箭头/分组/背景） |
| `web/src/components/Rail.jsx` | 修改 | 加「甘特」入口（VIEWS 数组 + 图标） |
| `web/src/App.jsx` | 修改 | import GanttView；`view === 'gantt'` 分支 |
| `web/src/index.css` | 修改 | 追加 `.gantt` 样式块 |
| `web/src/components/TopoView.jsx` | 修改 | 全部关键路径切换 + 冲突 N 段信息 |
| `mio_taskhub` 后端 | 无改动 | — |

---

## Task 1: cpm.js 纯函数扩展

**Files:**
- Modify: `web/src/cpm.js`
- Modify: `web/scripts/verify-cpm.mjs`

- [ ] **Step 1: 扩展 verify-cpm.mjs，加失败断言**

在 `web/scripts/verify-cpm.mjs` 末尾（`console.log('CPM verify OK', ...)` 之前）追加：

```javascript
// 多关键路径: a→b→d 与 c→d 都是 float≈0 的关键路径
const r5 = computeCPM([
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'b', est_duration_min: 60,  depends_on: ['a'], state: 'queued', stage: 'ready' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'd', est_duration_min: 30,  depends_on: ['b', 'c'], state: 'queued', stage: 'ready' },
])
// a+b+d = 210, c+d = 210 → 两条关键路径
const paths5 = r5.allCriticalPaths.map(p => p.join(',')).sort()
console.assert(JSON.stringify(paths5) === JSON.stringify(['a,b,d', 'c,d']),
  `allCriticalPaths=${JSON.stringify(paths5)} expected ['a,b,d','c,d']`)
console.assert(r5.critical.a && r5.critical.b && r5.critical.c && r5.critical.d,
  'critical 应为任一关键路径上的节点')
console.assert(r5.criticalPath.length === 3, `单条 criticalPath=${r5.criticalPath}`)

// parallelProfile: a[0,120) c[0,180) 重叠 [0,120) count=2, [120,180) count=1(c), [180,210) count=1(d 在 180 起, 与 c 无重叠)
const r6 = computeCPM([
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'd', est_duration_min: 30,  depends_on: ['c'], state: 'queued', stage: 'ready' },
])
const seg6 = r6.parallelProfile.map(s => `${s.start}-${s.end}:${s.count}`)
console.assert(JSON.stringify(seg6) === JSON.stringify(['0-120:2', '120-180:1', '180-210:1']),
  `parallelProfile=${JSON.stringify(seg6)}`)
console.assert(r6.parallel === 2, `parallel=${r6.parallel}`)

// resourceConflicts: 无 target_agent_type 时默认无阈值 → 空
console.assert(r6.resourceConflicts.length === 0, `默认无 agent 信息 resourceConflicts=${r6.resourceConflicts.length}`)

// 显式 maxParallel=1 → [0,120) count=2 是唯一冲突段
const r7 = computeCPM([
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'd', est_duration_min: 30,  depends_on: ['c'], state: 'queued', stage: 'ready' },
], { maxParallel: 1 })
const seg7 = r7.resourceConflicts.map(s => `${s.start}-${s.end}:${s.count}`)
console.assert(JSON.stringify(seg7) === JSON.stringify(['0-120:2']), `resourceConflicts=${JSON.stringify(seg7)}`)

// target_agent_type 去重数作为默认阈值: 2 个 agent → [0,120) count=2 不冲突
const r8 = computeCPM([
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready', target_agent_type: 'claude' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready', target_agent_type: 'codex' },
  { id: 'd', est_duration_min: 30,  depends_on: ['c'], state: 'queued', stage: 'ready', target_agent_type: 'codex' },
])
console.assert(r8.resourceConflicts.length === 0, `默认 agent 阈值 resourceConflicts=${r8.resourceConflicts.length}`)
```

- [ ] **Step 2: 运行确认失败**

Run: `node scripts/verify-cpm.mjs`
Expected: `console.assert` 触发 AssertionError（`allCriticalPaths` undefined 或 `Cannot read properties of undefined`），退出码非 0。

- [ ] **Step 3: 实现 cpm.js 扩展**

在 `web/src/cpm.js` 顶部（`export const DEFAULT_DURATION = 60` 后）加：

```javascript
// 关键路径 float 容忍误差（分钟）
export const EPSILON = 0.5
```

将 `computeCPM` 签名改为 `export function computeCPM(tasks, opts = {})`，并在函数末尾、`parallel` 计算之后、`return` 之前追加（注意 `critical` 需重算，原单条路径逻辑保留给 `criticalPath`）：

```javascript
  // 多关键路径：float≈0 节点子图的 DFS，收集全部 root→terminal 路径
  const crit = {}
  tasks.forEach(t => { crit[t.id] = float[t.id] <= EPSILON })
  const isRoot = id => deps[id].length === 0
  const isTerminal = id => (succ[id] || []).length === 0
  const allCriticalPaths = []
  const dfs = (id, path) => {
    const cur = path.concat(id)
    if (isTerminal(id)) { allCriticalPaths.push(cur); return }
    ;(succ[id] || []).forEach(c => { if (crit[c]) dfs(c, cur) })
  }
  tasks.forEach(t => { if (crit[t.id] && isRoot(t.id)) dfs(t.id, []) })

  // critical 标志 = 在任一关键路径上
  const onAny = new Set()
  allCriticalPaths.forEach(p => p.forEach(id => onAny.add(id)))
  tasks.forEach(t => { critical[t.id] = onAny.has(t.id) })

  // 并行度曲线：扫描线 [ES, EF) 区间覆盖计数
  const ev = []
  tasks.forEach(t => {
    if (dur[t.id] <= 0) return
    ev.push({ t: es[t.id], d: +1 })
    ev.push({ t: ef[t.id], d: -1 })
  })
  ev.sort((a, b) => a.t - b.t || a.d - b.d)
  const parallelProfile = []
  let curCount = 0
  let prevT = null
  ev.forEach(e => {
    if (prevT !== null && curCount > 0 && e.t > prevT) {
      parallelProfile.push({ start: prevT, end: e.t, count: curCount })
    }
    curCount += e.d
    prevT = e.t
  })

  // 资源冲突：超阈值区间（默认阈值 = target_agent_type 去重数，无则不标注）
  const agentSet = new Set(tasks.map(t => t.target_agent_type).filter(Boolean))
  const maxParallel = opts.maxParallel !== undefined ? opts.maxParallel : (agentSet.size || null)
  const conflicts = maxParallel ? parallelProfile.filter(s => s.count > maxParallel) : []
  const resourceConflicts = conflicts.reduce((acc, s) => {
    const last = acc[acc.length - 1]
    if (last && last.end === s.start && last.count === s.count) last.end = s.end
    else acc.push({ start: s.start, end: s.end, count: s.count })
    return acc
  }, [])
```

将 `return` 改为：

```javascript
  return {
    total,
    es, ef, ls, lf, float,
    criticalPath,
    critical,
    parallel,
    allCriticalPaths,
    parallelProfile,
    resourceConflicts,
  }
```

- [ ] **Step 4: 运行确认通过**

Run: `node scripts/verify-cpm.mjs`
Expected: `CPM verify OK:` 输出，退出码 0，无 assert 报错。

- [ ] **Step 5: 全量前端构建 + 后端回归**

Run: `npm --prefix web run build && cd .. && python -m pytest tests/ -q`
Expected: `✓ built` 且 `245 passed`。

- [ ] **Step 6: Commit**

```bash
git add web/src/cpm.js web/scripts/verify-cpm.mjs
git commit -m "feat: cpm 多关键路径 + 并行度曲线 + 资源冲突"
```

---

## Task 2: GanttView.jsx 组件

**Files:**
- Create: `web/src/components/GanttView.jsx`

- [ ] **Step 1: 写组件**

创建 `web/src/components/GanttView.jsx`，完整实现：

```jsx
import { useState, useMemo, useRef, useEffect, useCallback } from 'react'
import { fmtDur } from '../constants'
import { computeCPM } from '../cpm'

const MIN_SCALE = 6            // px / 分钟 下限
const TRACK_BASE = 800         // 目标可视宽度 px（不足时放宽到下限）
const LABEL_W = 150            // 行标题列宽 px
const ROW_H = 44               // 每行高度 px

// 刻度粒度：找满足 粒度*scale >= 70px 的最小整粒度（分钟）
const TICK_UNITS = [15, 30, 60, 120, 240, 480, 960, 1440, 2880, 5760]
const tickUnit = (total, scale) => TICK_UNITS.find(u => u * scale >= 70) || TICK_UNITS[TICK_UNITS.length - 1]

// 行分组模式
const GROUP_MODES = [
  { id: 'task', label: '任务' },
  { id: 'agent', label: 'Agent' },
  { id: 'project', label: '项目' },
]

export default function GanttView({ tasks, onOpen }) {
  const [groupMode, setGroupMode] = useState('task')
  const [hoveredId, setHoveredId] = useState(null)
  const [bars, setBars] = useState({})      // id → { left, top, width }（DOM 测量）
  const bodyRef = useRef(null)

  const byId = useMemo(() => Object.fromEntries(tasks.map(t => [t.id, t])), [tasks])
  const cpm = useMemo(() => computeCPM(tasks), [tasks])

  // 刻度
  const scale = cpm.total > 0 ? Math.max(MIN_SCALE, TRACK_BASE / cpm.total) : MIN_SCALE
  const trackW = cpm.total * scale
  const unit = tickUnit(cpm.total, scale)
  const ticks = []
  for (let t = 0; t <= cpm.total; t += unit) ticks.push(t)

  // 行组织
  const rows = useMemo(() => {
    const sorted = [...tasks].sort((a, b) => (cpm.es[a.id] || 0) - (cpm.es[b.id] || 0))
    if (groupMode === 'task') {
      return sorted.map(t => ({ key: t.id, label: t.title, tasks: [t] }))
    }
    if (groupMode === 'agent') {
      const groups = {}
      sorted.forEach(t => {
        const k = t.target_agent_type || '未分配'
        ;(groups[k] = groups[k] || []).push(t)
      })
      return Object.entries(groups).map(([k, ts]) => ({ key: k, label: k, tasks: ts }))
    }
    const groups = {}
    sorted.forEach(t => {
      const k = t.project || '默认'
      ;(groups[k] = groups[k] || []).push(t)
    })
    return Object.entries(groups).map(([k, ts]) => ({ key: k, label: k, tasks: ts }))
  }, [tasks, groupMode, cpm])

  // 依赖箭头测量：每行 track 内 bar 的绝对位置
  const measureBars = useCallback(() => {
    if (!bodyRef.current) return
    const bodyRect = bodyRef.current.getBoundingClientRect()
    const out = {}
    bodyRef.current.querySelectorAll('[data-gantt-bar]').forEach(el => {
      const id = el.getAttribute('data-gantt-bar')
      const r = el.getBoundingClientRect()
      out[id] = { left: r.left - bodyRect.left, top: r.top - bodyRect.top, width: r.width, height: r.height }
    })
    setBars(out)
  }, [])

  useEffect(() => {
    measureBars()
    if (!bodyRef.current) return
    const ro = new ResizeObserver(() => requestAnimationFrame(measureBars))
    ro.observe(bodyRef.current)
    const mo = new MutationObserver(() => requestAnimationFrame(measureBars))
    mo.observe(bodyRef.current, { childList: true, subtree: true })
    return () => { ro.disconnect(); mo.disconnect() }
  }, [measureBars, tasks, groupMode])

  // hover 上下游
  const connectedIds = (id) => {
    if (!id) return new Set()
    const out = new Set()
    const t = byId[id]
    if (!t) return out
    ;(t.depends_on || []).forEach(d => { if (byId[d]) out.add(d) })
    tasks.forEach(x => { if ((x.depends_on || []).includes(id)) out.add(x.id) })
    return out
  }

  // 依赖箭头：前驱 EF → 本任务 ES
  const edges = []
  tasks.forEach(t => {
    ;(t.depends_on || []).forEach(d => {
      if (!byId[d]) return
      const a = bars[d], b = bars[t.id]
      if (!a || !b) return
      edges.push({ id: `${d}->${t.id}`, x1: a.left + a.width, y1: a.top + a.height / 2, x2: b.left, y2: b.top + b.height / 2 })
    })
  })

  const labelOffset = LABEL_W
  const conflictSet = new Set(cpm.resourceConflicts.map(s => `${s.start}-${s.end}`))

  return (
    <div className="gantt">
      <div className="gantt__head">
        <h2 className="gantt__title">甘特图</h2>
        <div className="gantt__group" role="group" aria-label="分组方式">
          {GROUP_MODES.map(m => (
            <button key={m.id} className={`btn btn--ghost gantt__group-btn${groupMode === m.id ? ' is-active' : ''}`}
              onClick={() => setGroupMode(m.id)} aria-pressed={groupMode === m.id}>{m.label}</button>
          ))}
        </div>
        <div className="gantt__metrics">
          <span>总工期 {fmtDur(cpm.total)}</span>
          <span>关键路径 {cpm.allCriticalPaths.length} 条</span>
          <span>峰值并行 {cpm.parallelProfile.length ? Math.max(...cpm.parallelProfile.map(s => s.count)) : 0}</span>
          <span>冲突 {cpm.resourceConflicts.length} 段</span>
        </div>
      </div>

      {tasks.length === 0 && <div className="gantt__empty">还没有任务。</div>}

      <div className="gantt__body" ref={bodyRef}>
        <div className="gantt__axis" style={{ marginLeft: labelOffset }}>
          <div className="gantt__axis-grid" style={{ width: trackW }}>
            {ticks.map((t, i) => (
              <div key={i} className="gantt__tick" style={{ left: t * scale }}>
                <span>{i > 0 ? fmtDur(t) : '0'}</span>
              </div>
            ))}
          </div>
        </div>

        <svg className="gantt__edges" width={labelOffset + trackW} height={rows.length * ROW_H} aria-hidden="true">
          {edges.map(e => {
            const active = hoveredId && (e.id.startsWith(hoveredId + '->') || e.id.endsWith('->' + hoveredId))
            return <line key={e.id} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
              className={`gantt__edge${active ? ' is-active' : ''}`} />
          })}
        </svg>

        {rows.map((row, ri) => {
          const conn = connectedIds(hoveredId)
          return (
            <div key={row.key} className="gantt__row" style={{ height: ROW_H }}>
              <div className="gantt__row-label" style={{ width: labelOffset }} title={row.label}>
                <span className="gantt__row-label-txt">{row.label}</span>
                {row.tasks.length > 1 && <span className="gantt__row-count">{row.tasks.length}</span>}
              </div>
              <div className="gantt__track" style={{ width: trackW }}>
                {/* 冲突背景 */}
                {cpm.resourceConflicts.map(s => (
                  <div key={`c${s.start}-${s.end}`} className="gantt__conflict" style={{ left: s.start * scale, width: (s.end - s.start) * scale }} />
                ))}
                {/* 并行度背景 */}
                {cpm.parallelProfile.map(s => (
                  <div key={`p${s.start}-${s.end}`} className="gantt__parallel" style={{ left: s.start * scale, width: (s.end - s.start) * scale, opacity: Math.min(0.06 * s.count, 0.3) }} />
                ))}
                {row.tasks.map(t => {
                  const es = cpm.es[t.id], ef = cpm.ef[t.id], ls = cpm.ls[t.id], dur = ef - es
                  const isCrit = cpm.critical[t.id]
                  const hl = hoveredId && (hoveredId === t.id || conn.has(t.id))
                  return (
                    <div key={t.id} className="gantt__cell" style={{ left: es * scale, width: dur * scale }}>
                      {dur < ls - ef && (
                        <div className="gantt__float" style={{ left: (ef - es) * scale, width: (ls - ef) * scale }} />
                      )}
                      <button className={`gantt__bar${isCrit ? ' is-critical' : ''}${hl ? ' is-hovered' : ''}`}
                        data-gantt-bar={t.id}
                        style={{ width: Math.max(dur * scale, 3) }}
                        onMouseEnter={() => setHoveredId(t.id)}
                        onMouseLeave={() => setHoveredId(null)}
                        onClick={() => onOpen && onOpen(t)}
                        aria-label={`任务 ${t.title}，工期 ${fmtDur(dur)}${isCrit ? '，关键路径' : `，浮动 ${fmtDur(ls - es)}`}`}
                        title={`${t.title}  [${fmtDur(es)} → ${fmtDur(ef)}]`}>
                        <span className="gantt__bar-txt">{t.title}</span>
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

> 注意：`.gantt__cell` 是绝对定位容器（左=ES），内部 `.gantt__bar` 与 `.gantt__float` 相对 cell 定位；`data-gantt-bar` 挂在 bar 上供箭头测量。

- [ ] **Step 2: 构建验证**

Run: `npm --prefix web run build`
Expected: `✓ built`，无 JS 语法错误（组件尚未接入 App，仅验证可编译）。

- [ ] **Step 3: Commit**

```bash
git add web/src/components/GanttView.jsx
git commit -m "feat: GanttView 甘特图组件（时间轴/任务条/浮动条/箭头/分组）"
```

---

## Task 3: 集成（Rail + App + 样式）

**Files:**
- Modify: `web/src/components/Rail.jsx`
- Modify: `web/src/App.jsx`
- Modify: `web/src/index.css`

- [ ] **Step 1: Rail 加「甘特」入口**

在 `web/src/components/Rail.jsx` 的 VIEWS 数组，`topo` 项之后插入：

```jsx
  { id: 'gantt', label: '甘特', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M3 5h18M3 12h18M3 19h18" />
      <path d="M6 5v14M12 5v14M18 5v14" />
      <path d="M4 8h8M7 15h11" />
    </svg>
  )},
```

- [ ] **Step 2: App.jsx 接入**

- import 区（`import TopoView ...` 之后）加：

```jsx
import GanttView from './components/GanttView'
```

- viewport 分支（`view === 'topo'` 块之后）加：

```jsx
            {view === 'gantt' && (
              <GanttView tasks={filteredTasks} onOpen={openTask} />
            )}
```

- [ ] **Step 3: index.css 追加 .gantt 样式**

在 `web/src/index.css` 末尾追加：

```css
/* --- gantt view --- */
.gantt { padding: 24px; overflow: auto; }
.gantt__head { display: flex; align-items: center; gap: 16px; margin-bottom: 14px; flex-wrap: wrap; }
.gantt__title { font-size: 18px; font-weight: 700; margin: 0; }
.gantt__group { display: flex; gap: 6px; }
.gantt__group-btn.is-active { background: var(--accent, #4f7cff); color: #fff; border-color: transparent; }
.gantt__metrics { display: flex; gap: 14px; font-size: 12.5px; color: var(--ink-dim); margin-left: auto; }
.gantt__body { position: relative; min-width: fit-content; }
.gantt__axis { position: relative; height: 26px; }
.gantt__axis-grid { position: relative; height: 100%; }
.gantt__tick { position: absolute; top: 0; height: 100%; border-left: 1px solid var(--line, #e3e3e3); }
.gantt__tick span { position: absolute; top: 6px; left: 4px; font-size: 10.5px; color: var(--ink-faint); white-space: nowrap; }
.gantt__edges { position: absolute; top: 26px; left: 0; pointer-events: none; }
.gantt__edge { stroke: var(--line-strong, #c9c9c9); stroke-width: 1.2; stroke-dasharray: 4 3; }
.gantt__edge.is-active { stroke: var(--accent, #4f7cff); stroke-width: 1.6; }
.gantt__row { display: flex; border-top: 1px solid var(--line, #eee); }
.gantt__row-label { flex: none; display: flex; align-items: center; gap: 8px; padding: 0 12px; overflow: hidden; font-size: 12.5px; font-weight: 600; color: var(--ink); }
.gantt__row-label-txt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gantt__row-count { font-size: 10.5px; color: var(--ink-faint); }
.gantt__track { position: relative; flex: 1; }
.gantt__conflict { position: absolute; top: 0; height: 100%; background: rgba(255, 92, 92, 0.15); border-left: 2px solid rgba(255, 92, 92, 0.6); }
.gantt__parallel { position: absolute; top: 0; height: 100%; background: var(--accent, #4f7cff); }
.gantt__cell { position: absolute; top: 0; height: 100%; }
.gantt__bar { position: absolute; top: 10px; height: 24px; border-radius: 5px; border: 1px solid var(--line-strong, #c9c9c9); background: var(--panel, #fff); color: var(--ink); font-size: 11px; padding: 0 8px; display: flex; align-items: center; overflow: hidden; cursor: pointer; }
.gantt__bar.is-critical { background: var(--danger, #ff5c5c); border-color: #e04545; color: #fff; }
.gantt__bar.is-hovered { box-shadow: 0 0 0 2px var(--accent, #4f7cff); z-index: 2; }
.gantt__bar-txt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gantt__float { position: absolute; top: 16px; height: 12px; background: repeating-linear-gradient(135deg, rgba(0,0,0,0.08) 0 3px, transparent 3px 6px); }
.gantt__empty { padding: 40px 0; text-align: center; color: var(--ink-faint); }
```

- [ ] **Step 4: 构建验证**

Run: `npm --prefix web run build`
Expected: `✓ built`。

- [ ] **Step 5: Commit**

```bash
git add web/src/components/Rail.jsx web/src/App.jsx web/src/index.css
git commit -m "feat: 甘特图入口与样式集成"
```

---

## Task 4: TopoView 增强

**Files:**
- Modify: `web/src/components/TopoView.jsx`

- [ ] **Step 1: 实现全部关键路径切换**

按 spec C 节。在 `TopoView.jsx`：

1. 加状态 `const [showAll, setShowAll] = useState(false)`（在 `hoveredId` state 附近）。

2. 计算"当前关键节点集合"（替换第 97 行 `const crit = cpm.critical[t.id]` 的判定来源）：

```jsx
  const critIds = useMemo(() => {
    if (showAll) {
      const s = new Set()
      cpm.allCriticalPaths.forEach(p => p.forEach(id => s.add(id)))
      return s
    }
    return new Set(cpm.criticalPath)
  }, [showAll, cpm])
```

3. `metrics` 区（第 83 行 `并行度 {cpm.parallel}` 之后）加冲突信息，并在标题旁加切换按钮。将：

```jsx
      <div className="topo__head">
        <h2 className="topo__title">依赖拓扑</h2>
        <span className="topo__count">{tasks.length} 个任务 · {layers.length} 层</span>
      </div>
```

改为：

```jsx
      <div className="topo__head">
        <h2 className="topo__title">依赖拓扑</h2>
        <button className={`btn btn--ghost topo__toggle${showAll ? ' is-active' : ''}`}
          onClick={() => setShowAll(s => !s)} aria-pressed={showAll}>
          {showAll ? `全部关键路径 (${cpm.allCriticalPaths.length})` : '单条关键路径'}
        </button>
        <span className="topo__count">{tasks.length} 个任务 · {layers.length} 层</span>
      </div>
```

将 metrics 行改为：

```jsx
      <div className="topo__metrics">
        <span>总工期 {fmtDur(cpm.total)}</span>
        <span>关键路径 {cpm.allCriticalPaths.length} 条</span>
        <span>并行度 {cpm.parallel}</span>
        <span>冲突 {cpm.resourceConflicts.length} 段</span>
        <span>阻塞 {tasks.filter(isBlocked).length}</span>
      </div>
```

4. 节点关键判定（第 100 行 class 拼接处），将 `const crit = cpm.critical[t.id]` 替换为 `const crit = critIds.has(t.id)`。

- [ ] **Step 2: 构建验证**

Run: `npm --prefix web run build`
Expected: `✓ built`。

- [ ] **Step 3: Commit**

```bash
git add web/src/components/TopoView.jsx
git commit -m "feat: TopoView 全部关键路径切换 + 冲突提示"
```

---

## Task 5: 全量回归 + UI 端到端验证

**Files:**
- Verify only（无代码改动，发现问题才改）

- [ ] **Step 1: 后端回归**

Run: `python -m pytest tests/ -q`
Expected: `245 passed`。

- [ ] **Step 2: 纯函数验证**

Run: `node scripts/verify-cpm.mjs`
Expected: `CPM verify OK: ...`，无 assert 报错。

- [ ] **Step 3: 前端构建**

Run: `npm --prefix web run build`
Expected: `✓ built`。

- [ ] **Step 4: Playwright 端到端验证**

用 `webapp-testing` skill（Playwright），启动 dev server（`npm --prefix web run dev` 或对 dist 起静态服务 + 后端 hub），验证：

- 创建带依赖的任务集（a→b→d, c 独立），确保有两条 float≈0 路径（可用 `test_ideas_api` 类似数据或直接 SQL/API 构造）。
- 切到「甘特」视图：任务条可见、关键路径条为红色、浮动条存在、并行度/冲突背景可见、分组切换（任务/Agent/项目）生效。
- 切到「拓扑」视图：点「全部关键路径」按钮后多路径节点均高亮；metrics 显示「冲突 N 段」。

> 注意（沿用本项目经验）：中文 aria-label 定位用 JS `document.querySelectorAll('[aria-label*=甘特]')` 而非中文 CSS 选择器；元素交互优先 `page.locator(...).nth(i)` 或按钮文本过滤。

- [ ] **Step 5: Commit（若 Playwright 发现修复项）**

```bash
git add -A web/src web/scripts/verify-cpm.mjs
git commit -m "fix: 甘特/拓扑 UI 验证修复"
```

---

## Self-Review

**Spec 覆盖核对：**
- A1 allCriticalPaths → Task 1（判据 EPSILON、DFS、critical 改任一路径）
- A2 parallelProfile → Task 1（扫描线 [ES,EF)）
- A3 resourceConflicts → Task 1（默认阈值=agent 去重数、合并相邻）
- B1 数据 → Task 2（computeCPM 全量结果）
- B2 行组织/分组切换 → Task 2（task/agent/project 三模式按钮）
- B3 时间轴刻度 → Task 2（自适应粒度 TICK_UNITS）；任务条/浮动条/关键红条 → Task 2；依赖箭头 → Task 2（SVG 测量）；并行度/冲突背景 → Task 2；交互 hover/onOpen → Task 2；入口 → Task 3
- B4 样式 → Task 3（index.css .gantt 块）
- C TopoView 增强 → Task 4（切换 + 冲突 N 段）
- 验证 → Task 5（verify-cpm.mjs 扩展在 Task 1，后端 245 全绿，Playwright）

**Placeholder 扫描：** 无 TBD/TODO；每个代码步骤含完整代码。

**类型一致性：** `computeCPM(tasks, opts)` 签名在 Task 1 定义，Task 2/4 均只用 `computeCPM(tasks)`（opts 默认）。`allCriticalPaths`/`parallelProfile`/`resourceConflicts` 字段名 Task 1 定义、Task 2/4 消费一致。`EPSILON` 仅在 cpm.js 内部使用。`data-gantt-bar` 在 Task 2 写入与读取一致。