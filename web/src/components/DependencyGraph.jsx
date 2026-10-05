import { useMemo, useState } from 'react'
import { prio } from '../constants'

const NODE_W = 148
const NODE_H = 46
const H_GAP = 18
const V_GAP = 36
const PAD = 16

function collectAncestors(task, byId) {
  const out = new Set()
  const stack = [...(task.depends_on || [])]
  const seen = new Set()
  while (stack.length) {
    const cur = stack.pop()
    if (seen.has(cur)) continue
    seen.add(cur)
    if (!byId[cur]) continue
    out.add(cur)
    const t = byId[cur]
    ;(t.depends_on || []).forEach(d => { if (!seen.has(d)) stack.push(d) })
  }
  return out
}

function collectDescendants(taskId, tasks, byId) {
  // reverse adjacency
  const succ = {}
  tasks.forEach(t => {
    ;(t.depends_on || []).forEach(d => {
      if (!byId[d]) return
      if (!succ[d]) succ[d] = []
      succ[d].push(t.id)
    })
  })
  const out = new Set()
  const stack = [...(succ[taskId] || [])]
  const seen = new Set()
  while (stack.length) {
    const cur = stack.pop()
    if (seen.has(cur)) continue
    seen.add(cur)
    if (!byId[cur]) continue
    out.add(cur)
    ;(succ[cur] || []).forEach(n => { if (!seen.has(n)) stack.push(n) })
  }
  return out
}

function kahnLayers(nodeIds, byId) {
  const idSet = new Set(nodeIds)
  const succ = {}
  const indeg = {}
  nodeIds.forEach(id => { succ[id] = []; indeg[id] = 0 })
  nodeIds.forEach(id => {
    const t = byId[id]
    if (!t) return
    ;(t.depends_on || []).forEach(d => {
      if (!idSet.has(d)) return
      indeg[id] += 1
      succ[d].push(id)
    })
  })
  const depth = {}
  let frontier = nodeIds.filter(id => indeg[id] === 0)
  let d = 0
  const layerMap = {}
  while (frontier.length) {
    const next = []
    frontier.forEach(id => {
      depth[id] = d
      if (!layerMap[d]) layerMap[d] = []
      layerMap[d].push(id)
      ;(succ[id] || []).forEach(ch => {
        indeg[ch] -= 1
        if (indeg[ch] === 0) next.push(ch)
      })
    })
    frontier = next
    d += 1
  }
  // nodes in cycles (indeg>0) get deepest layer+1
  const leftover = nodeIds.filter(id => depth[id] === undefined)
  if (leftover.length) {
    const maxD = Math.max(...Object.keys(layerMap).map(Number), -1)
    leftover.forEach(id => {
      depth[id] = maxD + 1
      if (!layerMap[maxD + 1]) layerMap[maxD + 1] = []
      layerMap[maxD + 1].push(id)
    })
  }
  const layers = Object.keys(layerMap).sort((a, b) => +a - +b).map(k => layerMap[k])
  return { layers, depth }
}

export default function DependencyGraph({ task, tasks, onOpen }) {
  const [hovered, setHovered] = useState(null)
  const [selected, setSelected] = useState(null)
  const activeId = hovered || selected || task.id

  const byId = useMemo(() => Object.fromEntries((tasks || []).map(t => [t.id, t])), [tasks])

  const ancestors = useMemo(() => collectAncestors(task, byId), [task, byId])
  const descendants = useMemo(() => collectDescendants(task.id, tasks || [], byId), [task, tasks, byId])

  const missing = useMemo(() => (task.depends_on || []).filter(id => !byId[id]), [task, byId])

  // detect cycle locally (simple DFS on full graph)
  const cycleInfo = useMemo(() => {
    const allIds = (tasks || []).map(t => t.id)
    const WHITE = 0, GRAY = 1, BLACK = 2
    const color = Object.fromEntries(allIds.map(id => [id, WHITE]))
    const stack = []
    let cyc = null
    const dfs = (u) => {
      color[u] = GRAY
      stack.push(u)
      const deps = (byId[u]?.depends_on || [])
      for (const v of deps) {
        if (!byId[v]) continue
        if (color[v] === GRAY) {
          const idx = stack.indexOf(v)
          cyc = [...stack.slice(idx), v]
          return true
        }
        if (color[v] === WHITE && dfs(v)) return true
      }
      stack.pop()
      color[u] = BLACK
      return false
    }
    for (const id of allIds) {
      if (color[id] === WHITE && dfs(id)) break
    }
    return cyc
  }, [tasks, byId])

  const subIds = useMemo(() => {
    const s = new Set([...ancestors, task.id, ...descendants])
    return [...s].filter(id => byId[id])
  }, [ancestors, descendants, task, byId])

  const { layers, depth } = useMemo(() => kahnLayers(subIds, byId), [subIds, byId])

  // missing placeholders are shown as extra top layer
  const hasMissing = missing.length > 0

  // layout positions
  const positions = useMemo(() => {
    const pos = {}
    // missing nodes share a virtual top layer
    if (hasMissing) {
      missing.forEach((mid, i) => {
        pos[mid] = {
          x: PAD + i * (NODE_W + H_GAP),
          y: PAD,
          isMissing: true,
        }
      })
    }
    const yOffset = hasMissing ? (NODE_H + V_GAP + PAD) : PAD
    layers.forEach((layer, li) => {
      const w = layer.length * NODE_W + (layer.length - 1) * H_GAP
      const totalW = Math.max(w, hasMissing ? missing.length * NODE_W + (missing.length - 1) * H_GAP : 0)
      // center each layer
      const startX = PAD + Math.max(0, (totalW - w) / 2)
      const y = yOffset + li * (NODE_H + V_GAP)
      layer.forEach((id, idx) => {
        pos[id] = { x: startX + idx * (NODE_W + H_GAP), y, isMissing: false }
      })
    })
    return pos
  }, [layers, missing, hasMissing])

  const edges = useMemo(() => {
    const out = []
    subIds.forEach(id => {
      const t = byId[id]
      if (!t) return
      ;(t.depends_on || []).forEach(dep => {
        if (positions[dep] && positions[id]) {
          out.push({ from: dep, to: id })
        }
      })
    })
    // edges from missing placeholders
    missing.forEach(mid => {
      if (positions[mid] && positions[task.id]) {
        // already covered if task depends on missing
      }
    })
    return out
  }, [subIds, byId, positions, missing, task])

  const svgW = useMemo(() => {
    let mx = 0
    Object.values(positions).forEach(p => { mx = Math.max(mx, p.x + NODE_W) })
    return Math.max(mx + PAD, 360)
  }, [positions])
  const svgH = useMemo(() => {
    let my = 0
    Object.values(positions).forEach(p => { my = Math.max(my, p.y + NODE_H) })
    return Math.max(my + PAD, 120)
  }, [positions])

  const isAncestor = (id) => ancestors.has(id)
  const isDescendant = (id) => descendants.has(id)

  const connOf = (id) => {
    if (!id) return new Set()
    const s = new Set()
    const t = byId[id]
    if (t) (t.depends_on || []).forEach(d => { if (byId[d] || missing.includes(d)) s.add(d) })
    // downstream
    subIds.forEach(other => {
      if ((byId[other]?.depends_on || []).includes(id)) s.add(other)
    })
    return s
  }
  const connected = activeId ? connOf(activeId) : new Set()

  const nodeClass = (id) => {
    if (id === task.id) return 'is-current'
    if (isAncestor(id)) return 'is-ancestor'
    if (isDescendant(id)) return 'is-descendant'
    return ''
  }

  if (subIds.length === 0 && missing.length === 0) {
    return (
      <div className="dep-graph dep-graph--empty">
        <p className="detail-muted">无依赖关系</p>
      </div>
    )
  }

  return (
    <div className="dep-graph">
      {cycleInfo && (
        <div className="dep-graph__alert dep-graph__alert--cycle" role="alert">
          <b>⚠ 循环依赖</b> {cycleInfo.join(' → ')}
        </div>
      )}
      {missing.length > 0 && (
        <div className="dep-graph__alert dep-graph__alert--missing" role="alert">
          <b>⚠ 依赖缺失</b> {missing.join(', ')} — 这些任务不存在，创建/更新时将被拒绝
        </div>
      )}
      <div className="dep-graph__legend">
        <span className="dep-legend dep-legend--current">当前</span>
        <span className="dep-legend dep-legend--ancestor">前置</span>
        <span className="dep-legend dep-legend--descendant">后置</span>
        {missing.length > 0 && <span className="dep-legend dep-legend--missing">缺失</span>}
        <span className="dep-graph__hint">点击节点跳转 · 悬停高亮上下游</span>
      </div>
      <div className="dep-graph__canvas-wrap">
        <svg width={svgW} height={svgH} viewBox={`0 0 ${svgW} ${svgH}`} className="dep-graph__svg" role="img" aria-label="任务依赖图">
          <defs>
            <marker id="dep-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ink-faint)" />
            </marker>
            <marker id="dep-arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)" />
            </marker>
          </defs>
          {edges.map(e => {
            const a = positions[e.from]
            const b = positions[e.to]
            if (!a || !b) return null
            const x1 = a.x + NODE_W / 2
            const y1 = a.y + NODE_H
            const x2 = b.x + NODE_W / 2
            const y2 = b.y
            const active = activeId && (e.from === activeId || e.to === activeId || connected.has(e.from) || connected.has(e.to))
            // direct line or stepped?
            const mx = (y2 - y1) > V_GAP * 1.5
            if (mx) {
              const midY = (y1 + y2) / 2
              return (
                <path
                  key={`${e.from}->${e.to}`}
                  d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
                  className={`dep-edge${active ? ' is-active' : ''}`}
                  markerEnd={active ? 'url(#dep-arrow-active)' : 'url(#dep-arrow)'}
                  fill="none"
                />
              )
            }
            return (
              <line
                key={`${e.from}->${e.to}`}
                x1={x1} y1={y1} x2={x2} y2={y2}
                className={`dep-edge${active ? ' is-active' : ''}`}
                markerEnd={active ? 'url(#dep-arrow-active)' : 'url(#dep-arrow)'}
              />
            )
          })}
          {Object.entries(positions).map(([id, p]) => {
            const isMissingNode = p.isMissing
            const t = byId[id]
            const title = isMissingNode ? id : (t?.title || id)
            const pr = t ? prio(t.priority) : null
            const hl = activeId === id || connected.has(id)
            return (
              <g
                key={id}
                transform={`translate(${p.x},${p.y})`}
                className={`dep-node ${nodeClass(id)}${hl ? ' is-highlight' : ''}${isMissingNode ? ' is-missing' : ''}`}
                onMouseEnter={() => setHovered(id)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => {
                  if (isMissingNode) return
                  setSelected(id)
                  if (onOpen && t) onOpen(t)
                }}
                role={isMissingNode ? 'img' : 'button'}
                tabIndex={isMissingNode ? -1 : 0}
                aria-label={isMissingNode ? `缺失任务 ${id}` : `任务 ${title}`}
                onKeyDown={e => { if (e.key === 'Enter' && !isMissingNode && onOpen && t) onOpen(t) }}
                style={{ cursor: isMissingNode ? 'not-allowed' : 'pointer' }}
              >
                <rect width={NODE_W} height={NODE_H} rx="10" ry="10" />
                <text x="10" y="18" className="dep-node__title">{title.length > 18 ? title.slice(0, 18) + '…' : title}</text>
                <text x="10" y="32" className="dep-node__meta">
                  {isMissingNode ? '缺失' : `${t?.stage || ''} · ${pr?.label || ''}`}
                </text>
                {!isMissingNode && (
                  <text x={NODE_W - 8} y="32" textAnchor="end" className="dep-node__id">{id}</text>
                )}
              </g>
            )
          })}
        </svg>
      </div>
      <div className="dep-graph__stats">
        <span>前置 {ancestors.size}</span>
        <span>后置 {descendants.size}</span>
        <span>层数 {layers.length + (hasMissing ? 1 : 0)}</span>
        <span>边 {edges.length}</span>
      </div>
    </div>
  )
}
