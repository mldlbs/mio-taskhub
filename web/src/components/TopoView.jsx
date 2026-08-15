import { useMemo, useState } from 'react'
import { prio, fmtDur } from '../constants'

const STAGE_TONE = {
  done: 'ok', cancelled: 'dim', review: 'live',
  implementing: 'live', ready: 'dim', planning: 'dim',
  design: 'dim', brainstorming: 'dim',
}

function kahnLayers(tasks) {
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
  const deps = Object.fromEntries(tasks.map(t => [t.id, t.depends_on || []]))
  const indegree = {}
  const outdegree = {}
  tasks.forEach(t => { indegree[t.id] = 0; outdegree[t.id] = 0 })
  tasks.forEach(t => {
    ;(t.depends_on || []).forEach(d => {
      if (!byId[d]) return
      indegree[t.id] += 1
      outdegree[d] += 1
    })
  })
  const depth = {}
  const layers = []
  const remaining = new Set(tasks.map(t => t.id))
  let frontier = tasks.filter(t => (indegree[t.id] || 0) === 0)
  let d = 0
  while (frontier.length) {
    const next = []
    frontier.forEach(t => { depth[t.id] = d })
    layers.push(frontier)
    frontier.forEach(t => {
      ;(deps[t.id] || []).forEach(dep => {
        if (!byId[dep]) return
        indegree[dep] -= 1
        if (indegree[dep] === 0 && remaining.has(dep)) {
          next.push(byId[dep])
          remaining.delete(dep)
        }
      })
    })
    frontier = next
    d += 1
  }
  return { layers, meta: { depth, indegree, outdegree } }
}

export default function TopoView({ tasks, onOpen }) {
  const { layers, meta } = useMemo(() => kahnLayers(tasks), [tasks])
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
  const [hoveredId, setHoveredId] = useState(null)
  const isBlocked = (t) =>
    (t.depends_on || []).some(d => {
      const dep = byId[d]
      return dep && (dep.state === 'cancelled' || dep.state === 'failed')
    })

  const connectedIds = (id) => {
    if (!id) return new Set()
    const out = new Set()
    const t = byId[id]
    if (!t) return out
    ;(t.depends_on || []).forEach(d => { if (byId[d]) out.add(d) })
    tasks.forEach(x => { if ((x.depends_on || []).includes(id)) out.add(x.id) })
    return out
  }

  return (
    <div className="topo">
      <div className="topo__head">
        <h2 className="topo__title">依赖拓扑</h2>
        <span className="topo__count">{tasks.length} 个任务 · {layers.length} 层</span>
      </div>
      {layers.length === 0 && <div className="topo__empty">还没有任务。</div>}
      {layers.map((layer, i) => (
        <div key={i} className="topo__layer">
          <div className="topo__layer-tag">L{i + 1}</div>
          <div className="topo__layer-nodes">
            {layer.map(t => {
              const p = prio(t.priority)
              const blocked = isBlocked(t)
              const tone = blocked ? 'danger' : (STAGE_TONE[t.stage] || 'dim')
              const conn = connectedIds(hoveredId)
              const hl = hoveredId && (hoveredId === t.id || conn.has(t.id))
              return (
                <button key={t.id} className={`topo-node topo-node--${tone}${hl ? ' is-hovered' : ''}`}
                  role="button" tabIndex={0}
                  onMouseEnter={() => setHoveredId(t.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  aria-label={`任务 ${t.title}，阶段 ${t.stage}。回车查看详情`}
                  onClick={() => onOpen && onOpen(t)}
                  onKeyDown={e => { if (e.key === 'Enter' && onOpen) onOpen(t) }}>
                  <div className="topo-node__title">{t.title}</div>
                  <div className="topo-node__meta">
                    <span className={`chip${p.p >= 3 ? ' chip--p3' : ''}${p.p === 2 ? ' chip--p2' : ''}`}>{p.label}</span>
                    <span className="topo-node__stage">{t.stage}</span>
                  </div>
                  <div className="topo-node__stats">
                    <span>{fmtDur(t.est_duration_min)}</span>
                    <span>{blocked ? '阻塞' : `↓${meta.outdegree[t.id] || 0} ↑${meta.indegree[t.id] || 0}`}</span>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
