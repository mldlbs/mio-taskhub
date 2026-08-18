import { useMemo, useState } from 'react'
import { prio, fmtDur } from '../constants'
import { computeCPM } from '../cpm'

const STAGE_TONE = {
  done: 'ok', cancelled: 'dim', review: 'live',
  implementing: 'live', ready: 'dim', planning: 'dim',
  design: 'dim', brainstorming: 'dim',
}

function kahnLayers(tasks) {
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
  const succ = {}
  tasks.forEach(t => { succ[t.id] = [] })
  const indegree = {}
  const outdegree = {}
  tasks.forEach(t => { indegree[t.id] = 0; outdegree[t.id] = 0 })
  tasks.forEach(t => {
    ;(t.depends_on || []).forEach(d => {
      if (!byId[d]) return
      indegree[t.id] += 1
      outdegree[d] += 1
      succ[d].push(t.id)
    })
  })
  const depth = {}
  const layers = []
  let frontier = tasks.filter(t => (indegree[t.id] || 0) === 0).map(t => t.id)
  let d = 0
  while (frontier.length) {
    const next = []
    frontier.forEach(id => {
      depth[id] = d
      layers.push(byId[id])
      ;(succ[id] || []).forEach(child => {
        indegree[child] -= 1
        if (indegree[child] === 0) next.push(child)
      })
    })
    frontier = next
    d += 1
  }
  const grouped = {}
  tasks.forEach(t => {
    const dd = depth[t.id]
    if (dd === undefined) return
    ;(grouped[dd] = grouped[dd] || []).push(t)
  })
  const layerList = Object.keys(grouped).sort((a, b) => a - b).map(k => grouped[k])
  return { layers: layerList, meta: { depth, indegree, outdegree } }
}

export default function TopoView({ tasks, onOpen }) {
  const { layers } = useMemo(() => kahnLayers(tasks), [tasks])
  const cpm = useMemo(() => computeCPM(tasks), [tasks])
  const critIds = useMemo(() => {
    if (showAll) {
      const s = new Set()
      cpm.allCriticalPaths.forEach(p => p.forEach(id => s.add(id)))
      return s
    }
    return new Set(cpm.criticalPath)
  }, [showAll, cpm])
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
  const [hoveredId, setHoveredId] = useState(null)
  const [showAll, setShowAll] = useState(false)
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
        <button className={`btn btn--ghost topo__toggle${showAll ? ' is-active' : ''}`}
          onClick={() => setShowAll(s => !s)} aria-pressed={showAll}>
          {showAll ? `全部关键路径 (${cpm.allCriticalPaths.length})` : '单条关键路径'}
        </button>
        <span className="topo__count">{tasks.length} 个任务 · {layers.length} 层</span>
      </div>
      <div className="topo__metrics">
        <span>总工期 {fmtDur(cpm.total)}</span>
        <span>关键路径 {cpm.allCriticalPaths.length} 条</span>
        <span>并行度 {cpm.parallel}</span>
        <span>冲突 {cpm.resourceConflicts.length} 段</span>
        <span>阻塞 {tasks.filter(isBlocked).length}</span>
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
              const crit = critIds.has(t.id)
              const flt = cpm.float[t.id]
              return (
                <button key={t.id} className={`topo-node topo-node--${tone}${hl ? ' is-hovered' : ''}${crit ? ' is-critical' : ''}`}
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
                    <span>{blocked ? '阻塞' : (crit ? '关键' : `浮动 ${fmtDur(flt)}`)}</span>
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
