import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { prio, fmtDur } from '../constants'
import { computeCPM } from '../cpm'

const STAGE_TONE = {
  done: 'ok', cancelled: 'dim', review: 'live',
  implementing: 'live', ready: 'dim', planning: 'dim',
  design: 'dim', brainstorming: 'dim',
}

const isDone = t => t.state === 'completed' || t.stage === 'done'
const isClosed = t => isDone(t) || t.stage === 'cancelled' || t.state === 'cancelled'
const blockedBy = (t, pool) => (t.depends_on || []).some(d => {
  const dep = pool.find(x => x.id === d)
  return dep && !isClosed(dep)
})

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

/** 无向连通分量：共享依赖视为同簇（谁的链和谁的链连在一起）。 */
function connectedComponents(tasks) {
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
  const adj = {}
  tasks.forEach(t => { adj[t.id] = adj[t.id] || [] })
  tasks.forEach(t => {
    ;(t.depends_on || []).forEach(d => {
      if (!byId[d]) return
      adj[t.id].push(d)
      adj[d] = adj[d] || []
      adj[d].push(t.id)
    })
  })
  const seen = new Set()
  const out = []
  tasks.forEach(t => {
    if (seen.has(t.id)) return
    seen.add(t.id)
    const stack = [t.id]
    const ids = []
    while (stack.length) {
      const cur = stack.pop()
      ids.push(cur)
      ;(adj[cur] || []).forEach(n => { if (!seen.has(n)) { seen.add(n); stack.push(n) } })
    }
    out.push(ids.map(id => byId[id]))
  })
  return out
}

function clusterStat(ts) {
  const sorted = [...ts].sort((a, b) => String(a.id).localeCompare(String(b.id)))
  const cpm = computeCPM(ts)
  const { layers } = kahnLayers(ts)
  const roots = ts.filter(t => !(t.depends_on || []).some(d => ts.some(x => x.id === d)))
  return {
    key: sorted[0].id,
    tasks: ts,
    cpm,
    layerCount: layers.length,
    doneCount: ts.filter(isDone).length,
    blocked: ts.filter(t => blockedBy(t, ts)).length,
    roots,
  }
}

export default function TopoView({ tasks, onOpen }) {
  const [hoveredId, setHoveredId] = useState(null)
  const [showAll, setShowAll] = useState(false)
  const [focusId, setFocusId] = useState(null)
  const [hideIsolated, setHideIsolated] = useState(true)
  const [clusterKey, setClusterKey] = useState(null)
  const [query, setQuery] = useState('')
  const wrapRef = useRef(null)
  const nodeRefs = useRef(new Map())
  const [edges, setEdges] = useState([])

  // 有依赖关系（有前置或有后继）的任务
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

  const clusters = useMemo(() => {
    const cs = connectedComponents(visibleTasks).map(clusterStat)
    cs.sort((a, b) => b.tasks.length - a.tasks.length)
    return cs
  }, [visibleTasks])

  const active = clusterKey
    ? (clusters.find(c => c.key === clusterKey) || null)
    : (clusters.length === 1 ? clusters[0] : null)
  // 必须 memo：否则 [] 每次渲染都是新引用 → useLayoutEffect 反复触发 → setEdges 死循环（React #185）
  const detailTasks = useMemo(() => (active ? active.tasks : []), [active])

  const { layers } = useMemo(() => kahnLayers(detailTasks), [detailTasks])
  const cpm = useMemo(() => computeCPM(detailTasks), [detailTasks])
  const critIds = useMemo(() => {
    const s = new Set()
    const paths = showAll ? cpm.allCriticalPaths : cpm.allCriticalPaths.slice(0, 1)
    paths.forEach(p => p.forEach(id => s.add(id)))
    return s
  }, [cpm, showAll])

  const placed = useMemo(() => new Set(layers.flat().map(t => t.id)), [layers])
  const dropped = detailTasks.filter(t => !placed.has(t.id))
  const depCount = tasks.filter(t => (t.depends_on || []).length > 0).length

  // 聚焦/悬停链路（上下游闭包，限本簇）
  const activeId = focusId || hoveredId
  const chain = useMemo(() => {
    if (!activeId) return null
    const byId = Object.fromEntries(detailTasks.map(t => [t.id, t]))
    const out = new Set()
    const up = [activeId]
    while (up.length) {
      const cur = byId[up.pop()]
      ;((cur && cur.depends_on) || []).forEach(d => {
        if (d !== activeId && byId[d] && !out.has(d)) { out.add(d); up.push(d) }
      })
    }
    const kids = {}
    detailTasks.forEach(t => (t.depends_on || []).forEach(d => { (kids[d] = kids[d] || []).push(t.id) }))
    const down = [activeId]
    while (down.length) {
      const cur = down.pop()
      ;(kids[cur] || []).forEach(c => {
        if (c !== activeId && !out.has(c)) { out.add(c); down.push(c) }
      })
    }
    return out
  }, [activeId, detailTasks])

  // 连线测量
  const measure = () => {
    const wrap = wrapRef.current
    if (!wrap) return
    const base = wrap.getBoundingClientRect()
    const byId = Object.fromEntries(detailTasks.map(t => [t.id, t]))
    const es = []
    detailTasks.forEach(t => {
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
          x2: r.left + r.width / 2, y2: r.top - base.top,
        })
      })
    })
    setEdges(es)
  }

  useLayoutEffect(() => { measure() }, [detailTasks, showAll])
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
  }, [detailTasks])

  // 聚焦后滚动到该节点
  useEffect(() => {
    if (!focusId) return
    const el = nodeRefs.current.get(focusId)
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [focusId, active && active.key])

  const critChain = useMemo(() => {
    const p = (cpm.allCriticalPaths || [])[0] || []
    const byId = Object.fromEntries(detailTasks.map(t => [t.id, t]))
    return p.map(id => byId[id]).filter(Boolean)
  }, [cpm, detailTasks])

  // 搜索：按标题匹配（≤5 建议）→ 切到所在簇 + 聚焦 + 滚动
  const suggestions = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    return tasks.filter(t => (t.title || '').toLowerCase().includes(q)).slice(0, 5)
  }, [query, tasks])

  const pickSearch = (t) => {
    const owner = clusters.find(c => c.tasks.some(x => x.id === t.id))
    if (owner) setClusterKey(owner.key)
    else setClusterKey(null)
    setFocusId(t.id)
    setQuery('')
  }

  return (
    <div className="topo" ref={wrapRef}>
      <div className="topo__head">
        <h2 className="topo__title">依赖拓扑</h2>
        {active && clusters.length > 1 && (
          <button className="btn btn--ghost topo__toggle" onClick={() => { setClusterKey(null); setFocusId(null) }}>
            ← 链簇列表（{clusters.length}）
          </button>
        )}
        <button className={`btn btn--ghost topo__toggle${hideIsolated ? ' is-active' : ''}`}
          onClick={() => { setHideIsolated(v => !v); setClusterKey(null); setFocusId(null) }}
          aria-pressed={hideIsolated}
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
        <input className="inp inp--xs topo__search" placeholder="搜索任务定位…" value={query}
               onChange={e => setQuery(e.target.value)} />
        <span className="topo__count">
          {active ? `${detailTasks.length} 任务 · ${layers.length} 层` : `${clusters.length} 个链簇 · ${visibleTasks.length}/${tasks.length} 任务`}
        </span>
      </div>

      {suggestions.length > 0 && (
        <div className="topo__search-list">
          {suggestions.map(t => (
            <button key={t.id} className="btn btn--ghost btn--xs" onClick={() => pickSearch(t)}>
              {t.title}
            </button>
          ))}
        </div>
      )}

      {/* 链簇列表：路径多了先分组，一簇一卡 */}
      {!active && clusters.length > 1 && (
        <>
          {tasks.length > 0 && depCount === 0 && (
            <div className="topo__hint">
              当前任务之间<b>还没有依赖关系</b> —— 拓扑是平的，关键路径与阻塞都显示不出来。
              依赖的三种来源：① 想法详情「行为拆解」；② agent 建任务带 <span className="mono">depends_on</span>；
              ③ 打开任务 →「依赖」区 →「编辑依赖」。
            </div>
          )}
          <div className="topo__clusters">
            {clusters.map(c => (
              <button key={c.key} className="topo-cluster" onClick={() => { setClusterKey(c.key); setFocusId(null) }}>
                <div className="topo-cluster__title">
                  {c.roots.length > 1 ? `${c.roots.length} 条起始链` : (c.roots[0]?.title || '链簇')}
                </div>
                <div className="topo-cluster__stats">
                  <span>{c.tasks.length} 任务</span>
                  <span>完成 {c.doneCount}/{c.tasks.length}</span>
                  <span>{c.layerCount} 层</span>
                  <span>总工期 {fmtDur(c.cpm.total)}</span>
                  <span>关键路径 {c.cpm.allCriticalPaths.length}</span>
                  {c.blocked > 0 && <span className="topo-cluster__warn">阻塞 {c.blocked}</span>}
                </div>
                <div className="topo-cluster__sub">
                  {c.tasks.slice(0, 4).map(t => t.title).join(' · ')}{c.tasks.length > 4 ? ' …' : ''}
                </div>
              </button>
            ))}
          </div>
        </>
      )}

      {/* 簇详情：层级图 + 连线 + 图例 + 关键路径摘要 */}
      {active && (
        <>
          <div className="topo__metrics">
            <span>总工期 {fmtDur(cpm.total)}</span>
            <span>关键路径 {cpm.allCriticalPaths.length} 条</span>
            <span>并行度 {cpm.parallel}</span>
            <span>冲突 {cpm.resourceConflicts.length} 段</span>
            <span>阻塞 {detailTasks.filter(t => blockedBy(t, detailTasks)).length}</span>
          </div>

          <div className="topo__legend">
            <span><i className="lg lg--crit" /> 关键路径（无浮动，拖一天整体拖一天）</span>
            <span><i className="lg lg--block" /> 阻塞（前置未完成）</span>
            <span><i className="lg lg--norm" /> 普通（浮动 N = 可延迟 N）</span>
            <span>L1…Ln = 依赖层，同层可并行</span>
            <span className="detail-muted">单击聚焦 · 双击打开</span>
          </div>

          {critChain.length > 1 && (
            <div className="topo__chain">
              关键路径：{critChain.map((t, i) => (
                <span key={t.id}>{i > 0 ? ' → ' : ''}{t.title}</span>
              ))}
              <span className="detail-muted"> · 共 {fmtDur(cpm.total)} · 并行度 {cpm.parallel}</span>
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
              const dim = !!(activeId && !hl)
              const crit = critIds.has(e.from) && critIds.has(e.to)
              const my = (e.y1 + e.y2) / 2
              return (
                <path key={e.key}
                  d={`M ${e.x1} ${e.y1} C ${e.x1} ${my}, ${e.x2} ${my}, ${e.x2} ${e.y2}`}
                  className={`topo__edge${hl ? ' is-hl' : ''}${crit ? ' is-crit' : ''}${dim ? ' is-dim' : ''}`} />
              )
            })}
          </svg>

          {layers.map((layer, i) => (
            <div key={i} className="topo__layer">
              <div className="topo__layer-tag">L{i + 1}</div>
              <div className="topo__layer-nodes">
                {layer.map(t => {
                  const p = prio(t.priority)
                  const blocked = blockedBy(t, detailTasks)
                  const tone = blocked ? 'danger' : (STAGE_TONE[t.stage] || 'dim')
                  const inChain = chain && chain.has(t.id)
                  const hl = !!(activeId && (activeId === t.id || inChain))
                  const dim = !!(activeId && activeId !== t.id && !inChain)
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
        </>
      )}

      {!active && clusters.length === 0 && (
        <div className="topo__empty">
          {tasks.length === 0 ? '还没有任务。' : '当前筛选下没有带依赖关系的任务（可关闭「只看有依赖」查看全部）。'}
        </div>
      )}
    </div>
  )
}
