import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
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
  const [hoveredId, setHoveredId] = useState(null)
  const [showAll, setShowAll] = useState(false)
  const [focusId, setFocusId] = useState(null)
  const [hideIsolated, setHideIsolated] = useState(true)
  const wrapRef = useRef(null)
  const nodeRefs = useRef(new Map())
  const [edges, setEdges] = useState([])

  // 有依赖关系（有前置或有后继）的任务集合
  const linkedIds = useMemo(() => {
    const s = new Set()
    tasks.forEach(t => {
      if ((t.depends_on || []).length) s.add(t.id)
      ;(t.depends_on || []).forEach(d => s.add(d))
    })
    return s
  }, [tasks])

  const visibleTasks = useMemo(
    () => (hideIsolated ? tasks.filter(t => linkedIds.has(t.id)) : tasks),
    [tasks, hideIsolated, linkedIds])

  const { layers } = useMemo(() => kahnLayers(visibleTasks), [visibleTasks])
  const cpm = useMemo(() => computeCPM(visibleTasks), [visibleTasks])
  const critIds = useMemo(() => {
    const s = new Set()
    const paths = showAll ? cpm.allCriticalPaths : cpm.allCriticalPaths.slice(0, 1)
    paths.forEach(p => p.forEach(id => s.add(id)))
    return s
  }, [cpm, showAll])

  const placed = useMemo(() => new Set(layers.flat().map(t => t.id)), [layers])
  const dropped = visibleTasks.filter(t => !placed.has(t.id))
  const depCount = tasks.filter(t => (t.depends_on || []).length > 0).length

  // 聚焦/悬停时的完整链路（上下游双向闭包）
  const activeId = focusId || hoveredId
  const chain = useMemo(() => {
    if (!activeId) return null
    const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
    const out = new Set()
    const up = [activeId]
    while (up.length) {
      const cur = byId[up.pop()]
      ;((cur && cur.depends_on) || []).forEach(d => {
        if (d !== activeId && !out.has(d)) { out.add(d); up.push(d) }
      })
    }
    const kids = {}
    tasks.forEach(t => (t.depends_on || []).forEach(d => { (kids[d] = kids[d] || []).push(t.id) }))
    const down = [activeId]
    while (down.length) {
      const cur = down.pop()
      ;(kids[cur] || []).forEach(c => {
        if (c !== activeId && !out.has(c)) { out.add(c); down.push(c) }
      })
    }
    return out
  }, [activeId, tasks])

  // 连线测量：节点矩形 → 上游底边中点 到 下游顶边中点的曲线
  const measure = () => {
    const wrap = wrapRef.current
    if (!wrap) return
    const base = wrap.getBoundingClientRect()
    const byId = Object.fromEntries(visibleTasks.map(t => [t.id, t]))
    const es = []
    visibleTasks.forEach(t => {
      const el = nodeRefs.current.get(t.id)
      if (!el) return
      const r = el.getBoundingClientRect()
      ;(t.depends_on || []).forEach(did => {
        if (!byId[did]) return
        const de = nodeRefs.current.get(did)
        if (!de) return
        const dr = de.getBoundingClientRect()
        es.push({
          key: `${did}->${t.id}`,
          from: did, to: t.id,
          x1: dr.left - base.left + dr.width / 2, y1: dr.bottom - base.top,
          x2: r.left - base.left + r.width / 2, y2: r.top - base.top,
        })
      })
    })
    setEdges(es)
  }

  useLayoutEffect(() => { measure() }, [visibleTasks, showAll])
  useEffect(() => {
    const wrap = wrapRef.current
    if (!wrap) return undefined
    let ro = null
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => measure())
      ro.observe(wrap)
    }
    const onWin = () => measure()
    window.addEventListener('resize', onWin)
    return () => {
      if (ro) ro.disconnect()
      window.removeEventListener('resize', onWin)
    }
  }, [visibleTasks])

  const critChain = useMemo(() => {
    const p = (cpm.allCriticalPaths || [])[0] || []
    const byId = Object.fromEntries(visibleTasks.map(t => [t.id, t]))
    return p.map(id => byId[id]).filter(Boolean)
  }, [cpm, visibleTasks])

  return (
    <div className="topo" ref={wrapRef}>
      <div className="topo__head">
        <h2 className="topo__title">依赖拓扑</h2>
        <button className={`btn btn--ghost topo__toggle${hideIsolated ? ' is-active' : ''}`}
          onClick={() => setHideIsolated(v => !v)} aria-pressed={hideIsolated}
          title="隐藏既无前置也无后继的孤立任务，只看依赖链">
          只看有依赖 {linkedIds.size}/{tasks.length}
        </button>
        <button className={`btn btn--ghost topo__toggle${showAll ? ' is-active' : ''}`}
          onClick={() => setShowAll(s => !s)} aria-pressed={showAll}>
          {showAll ? `全部关键路径 (${cpm.allCriticalPaths.length})` : '单条关键路径'}
        </button>
        {(focusId || hoveredId) && (
          <button className="btn btn--ghost topo__toggle" onClick={() => setFocusId(null)}>清除聚焦</button>
        )}
        <span className="topo__count">{visibleTasks.length}/{tasks.length} 个任务 · {layers.length} 层</span>
      </div>

      <div className="topo__metrics">
        <span>总工期 {fmtDur(cpm.total)}</span>
        <span>关键路径 {cpm.allCriticalPaths.length} 条</span>
        <span>并行度 {cpm.parallel}</span>
        <span>冲突 {cpm.resourceConflicts.length} 段</span>
        <span>阻塞 {visibleTasks.filter(t => (t.depends_on || []).some(d => {
          const dep = tasks.find(x => x.id === d)
          return dep && !(dep.state === 'completed' || dep.stage === 'done' || dep.stage === 'cancelled')
        })).length}</span>
      </div>

      <div className="topo__legend">
        <span><i className="lg lg--crit" /> 关键路径（无浮动，拖一天整体拖一天）</span>
        <span><i className="lg lg--block" /> 阻塞（前置未完成）</span>
        <span><i className="lg lg--norm" /> 普通（浮动 N = 可延迟 N）</span>
        <span>L1…Ln = 依赖层，同层可并行</span>
        <span className="detail-muted">单击节点聚焦链路 · 双击打开任务</span>
      </div>

      {critChain.length > 1 && (
        <div className="topo__chain">
          关键路径：{critChain.map((t, i) => (
            <span key={t.id}>{i > 0 ? ' → ' : ''}{t.title}</span>
          ))}
          <span className="detail-muted"> · 共 {fmtDur(cpm.total)} · 并行度 {cpm.parallel}</span>
        </div>
      )}

      {tasks.length > 0 && depCount === 0 && (
        <div className="topo__hint">
          当前任务之间<b>还没有依赖关系</b> —— 拓扑是平的（全部落在 L1），
          关键路径与阻塞都显示不出来。依赖的三种来源：
          ① 想法详情「行为拆解」拆出的子任务自带依赖；
          ② agent 建任务时带 <span className="mono">depends_on</span>；
          ③ 打开任务 →「依赖」区 →「编辑依赖」手工添加。
        </div>
      )}
      {dropped.length > 0 && (
        <div className="topo__hint topo__hint--warn">
          ⚠ {dropped.length} 个任务因依赖成环未参与分层（L 序列里看不到）——
          请打开这些任务检查依赖：{dropped.slice(0, 3).map(t => t.title).join('、')}
          {dropped.length > 3 ? ' 等' : ''}
        </div>
      )}

      <svg className="topo__edges" aria-hidden="true">
        {edges.map(e => {
          const inChain = chain && chain.has(e.from) && chain.has(e.to)
          const touches = activeId && (e.from === activeId || e.to === activeId)
          const hl = !!(touches || (focusId && inChain))
          const crit = critIds.has(e.from) && critIds.has(e.to)
          const my = (e.y1 + e.y2) / 2
          return (
            <path key={e.key}
              d={`M ${e.x1} ${e.y1} C ${e.x1} ${my}, ${e.x2} ${my}, ${e.x2} ${e.y2}`}
              className={`topo__edge${hl ? ' is-hl' : ''}${crit ? ' is-crit' : ''}`} />
          )
        })}
      </svg>

      {layers.length === 0 && <div className="topo__empty">还没有任务。</div>}
      {layers.map((layer, i) => (
        <div key={i} className="topo__layer">
          <div className="topo__layer-tag">L{i + 1}</div>
          <div className="topo__layer-nodes">
            {layer.map(t => {
              const p = prio(t.priority)
              const blocked = (t.depends_on || []).some(d => {
                const dep = tasks.find(x => x.id === d)
                return dep && !(dep.state === 'completed' || dep.stage === 'done' || dep.stage === 'cancelled')
              })
              const tone = blocked ? 'danger' : (STAGE_TONE[t.stage] || 'dim')
              const inChain = chain && chain.has(t.id)
              const hl = !!(activeId && (activeId === t.id || inChain))
              const dim = !!(focusId && focusId !== t.id && !inChain)
              const crit = critIds.has(t.id)
              const flt = cpm.float[t.id]
              return (
                <button key={t.id}
                  ref={el => { if (el) nodeRefs.current.set(t.id, el); else nodeRefs.current.delete(t.id) }}
                  className={`topo-node topo-node--${tone}${hl ? ' is-hovered' : ''}${crit ? ' is-critical' : ''}${dim ? ' is-dim' : ''}${focusId === t.id ? ' is-focus' : ''}`}
                  role="button" tabIndex={0}
                  title="单击聚焦链路 · 双击打开任务"
                  onMouseEnter={() => setHoveredId(t.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  aria-label={`任务 ${t.title}，阶段 ${t.stage}。回车查看详情`}
                  onClick={() => setFocusId(cur => (cur === t.id ? null : t.id))}
                  onDoubleClick={() => onOpen && onOpen(t)}
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
