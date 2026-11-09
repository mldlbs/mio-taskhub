import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { prio, fmtDur, agMono, agColor } from '../constants'

const STAGES = [
  { id: 'brainstorming', label: '需求理解', en: 'BRAINSTORM' },
  { id: 'design',        label: '设计',     en: 'DESIGN' },
  { id: 'planning',      label: '计划',     en: 'PLANNING' },
  { id: 'ready',         label: '待执行',   en: 'READY' },
  { id: 'implementing',  label: '执行中',   en: 'IMPL' },
  { id: 'review',        label: '审查',     en: 'REVIEW' },
  { id: 'done',          label: '完成',     en: 'DONE' },
]

const BACKTRACKS = [
  { from: 'design',       to: 'brainstorming', note: '设计时需求不明' },
  { from: 'planning',     to: 'design',        note: '计划时发现设计不足' },
  { from: 'review',       to: 'planning',      note: '审查不过需改计划' },
  { from: 'review',       to: 'design',        note: '审查不过需改设计' },
  { from: 'review',       to: 'ready',         note: '审查不过直接重做' },
]

const labelOf = (id) => STAGES.find(s => s.id === id)?.label || id
const nodeScale = (n) => Math.min(1 + n * 0.06, 1.5)

const depState = (t, tasks) => {
  const deps = t.depends_on || []
  if (!deps.length) return null
  const map = Object.fromEntries(tasks.map(x => [x.id, x]))
  const blocked = deps.some(d => {
    const p = map[d]
    return p && (p.state === 'cancelled' || p.state === 'failed')
  })
  const done = deps.every(d => {
    const p = map[d]
    return p && (p.state === 'completed' || p.stage === 'done')
  })
  return { count: deps.length, done, blocked }
}

/**
 * 依赖图视图。
 *
 * 推进（R2 C-4）：本组件**不再自带任何推进 UI / 输入**。
 * 拖拽落点与卡片上的「→ 下一阶段」按钮都只做一件事 —— 回调 `onAdvanceTask(task, targetStage)`，
 * 由 App.advanceTaskStage 决定：需要产出物的阶段弹统一的 StageAdvanceDialog 收集，
 * 不需要的直接推进并给成功 toast。原先这里的 `window.prompt` + 3 处 `alert()`
 * 是原生阻塞式弹窗（无法键盘 Esc 仲裁、无法显示在浮层内、样式与产品脱节），已全部删除；
 * 错误由 advanceStage 的返回值 / 对话框内错误条承接。
 */
export default function FlowView({ tasks, onOpen, onCancel, onAdvanceTask }) {
  const [expanded, setExpanded] = useState(STAGES[0].id)
  const [draggingId, setDraggingId] = useState(null)
  const [hoveredId, setHoveredId] = useState(null)
  const [edges, setEdges] = useState([])
  const [globalEdges, setGlobalEdges] = useState([])
  const panelRef = useRef(null)
  const canvasRef = useRef(null)

  const onCardDragStart = (e, id) => {
    setDraggingId(id)
    e.dataTransfer.setData('text/plain', id)
    e.dataTransfer.effectAllowed = 'move'
  }

  const onColumnDrop = (stageId) => {
    if (!draggingId) return
    const task = tasks.find(t => t.id === draggingId)
    setDraggingId(null)
    if (!task || task.stage === stageId) return
    if (onAdvanceTask) onAdvanceTask(task, stageId)
  }

  const flowTasks = useMemo(() => tasks.filter(t => t.stage !== 'cancelled'), [tasks])
  const byStage = useMemo(
    () => Object.fromEntries(STAGES.map(s => [s.id, flowTasks.filter(t => t.stage === s.id)])),
    [flowTasks]
  )
  const stage = STAGES.find(s => s.id === expanded)
  const activeTasks = useMemo(() => (stage ? byStage[stage.id] : []), [stage, byStage])

  const computeEdges = useCallback(() => {
    if (!panelRef.current || expanded === null) { setEdges([]); return }
    const panel = panelRef.current
    const cards = panel.querySelectorAll('.flow-card')
    const rects = {}
    cards.forEach(c => {
      const id = c.getAttribute('data-task-id')
      if (id) rects[id] = c.getBoundingClientRect()
    })
    const panelRect = panel.getBoundingClientRect()
    const out = []
    activeTasks.forEach(t => {
      ;(t.depends_on || []).forEach(depId => {
        if (!rects[t.id] || !rects[depId]) return
        const a = rects[t.id]
        const b = rects[depId]
        out.push({
          id: `${depId}->${t.id}`,
          from: depId,
          to: t.id,
          x1: b.right - panelRect.left, y1: b.top + b.height / 2 - panelRect.top,
          x2: a.left - panelRect.left, y2: a.top + a.height / 2 - panelRect.top,
        })
      })
    })
    setEdges(out)
  }, [activeTasks, expanded])

  useEffect(() => {
    computeEdges()
    if (!panelRef.current) return
    const ro = new ResizeObserver(() => requestAnimationFrame(computeEdges))
    ro.observe(panelRef.current)
    const mo = new MutationObserver(() => requestAnimationFrame(computeEdges))
    mo.observe(panelRef.current, { childList: true, subtree: true })
    return () => { ro.disconnect(); mo.disconnect() }
  }, [computeEdges, expanded])

  const computeGlobalEdges = useCallback(() => {
    if (expanded === null || !canvasRef.current) { setGlobalEdges([]); return }
    const container = canvasRef.current
    const containerRect = container.getBoundingClientRect()
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
    const edges = []
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

  const nextStage = (stage) => {
    const i = STAGES.findIndex(s => s.id === stage)
    return i >= 0 && i < STAGES.length - 1 ? STAGES[i + 1].id : null
  }

  return (
    <div className="flow" ref={canvasRef}>
      <div className="flow__canvas">
        <svg className="flow__global-edges" aria-hidden="true">
          {globalEdges.map(e => {
            const active = hoveredId && (e.id.startsWith(hoveredId + '->') || e.id.endsWith('->' + hoveredId))
            return (
              <line key={e.id} x1={e.from.x} y1={e.from.y} x2={e.to.x} y2={e.to.y}
                    className={`flow__global-edge${active ? ' is-active' : ''}`} />
            )
          })}
        </svg>
        <div className="flow__nodes">
          {STAGES.map((s, i) => (
            <div key={s.id} data-col={s.id} className="flow__step"
              onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }}
              onDragEnter={e => { e.currentTarget.classList.add('is-dragover') }}
              onDragLeave={e => { e.currentTarget.classList.remove('is-dragover') }}
              onDrop={e => { e.preventDefault(); e.currentTarget.classList.remove('is-dragover'); onColumnDrop(s.id) }}
            >
              {i > 0 && (
                <div className="flow__arrow" aria-hidden="true"><span>→</span></div>
              )}
              <button
                type="button"
                className={`flow-node${expanded === s.id ? ' is-open' : ''}`}
                style={{ '--s': nodeScale(byStage[s.id].length) }}
                onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                aria-expanded={expanded === s.id}
                aria-label={`阶段 ${s.label}，${byStage[s.id].length} 个任务`}
              >
                <span className="flow-node__en">{s.en}</span>
                <span className="flow-node__label">{s.label}</span>
                <span className="flow-node__count">{byStage[s.id].length}</span>
              </button>
            </div>
          ))}
        </div>

        {stage && (
          <div className="flow__panel" ref={panelRef}>
            <div className="flow__panel-head">
              <span className="flow__panel-title">{stage.label}</span>
              <span className="flow__panel-count">{activeTasks.length} 个任务</span>
            </div>
            <svg className="flow__edges" aria-hidden="true">
              {edges.map(e => {
                const active = hoveredId && (e.from === hoveredId || e.to === hoveredId)
                return (
                  <line key={e.id} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
                        className={`flow__edge${active ? ' is-active' : ''}`} />
                )
              })}
            </svg>
            {activeTasks.map(t => {
              const p = prio(t.priority)
              const ac = t.target_agent_type && agColor(t.target_agent_type)
              const next = nextStage(t.stage)
              return (
                <div key={t.id} className="flow-card" data-task-id={t.id} role="button" tabIndex={0}
                  draggable
                  onMouseEnter={() => setHoveredId(t.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  onDragStart={e => onCardDragStart(e, t.id)}
                  aria-label={`任务 ${t.title}，阶段 ${stage.label}。回车查看详情`}
                  onClick={() => onOpen && onOpen(t)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      if (onOpen) onOpen(t)
                    }
                  }}>
                  <div className="flow-card__top">
                    <h4>{t.title}</h4>
                    <span className="flow-card__chev">›</span>
                  </div>
                  <div className="flow-card__meta">
                    <span className={`chip${p.p >= 3 ? ' chip--p3' : ''}${p.p === 2 ? ' chip--p2' : ''}`}>{p.label}</span>
                    {(() => {
                      const ds = depState(t, tasks)
                      if (!ds) return null
                      return (
                        <span
                          className={`chip dep-chip${ds.done ? ' dep-chip--ok' : ''}${ds.blocked ? ' dep-chip--blocked' : ''}`}
                          title={ds.blocked ? '前置任务已取消/失败，无法放行' : (ds.done ? '前置任务已完成' : '等待前置任务')}
                        >⛓ {ds.count}</span>
                      )
                    })()}
                    {t.target_agent_type && (
                      <span className="task__agent">
                        <span className="agent-mono" style={{ background: ac.bg, borderColor: ac.fg, color: ac.fg }}>{agMono(t.target_agent_type)}</span>
                      </span>
                    )}
                  </div>
                  <div className="flow-card__foot">
                    <span>{fmtDur(t.est_duration_min)}</span>
                    {next && (
                      <button className="btn btn--ghost flow-card__adv" onClick={e => { e.stopPropagation(); if (onAdvanceTask) onAdvanceTask(t, next) }}>
                        → {labelOf(next)}
                      </button>
                    )}
                    {['brainstorming','design','planning'].includes(t.stage) && (
                      <button className="btn btn--ghost btn--danger flow-card__cancel" onClick={e => { e.stopPropagation(); onCancel(t.id) }}>×</button>
                    )}
                  </div>
                </div>
              )
            })}
            {activeTasks.length === 0 && <div className="flow__empty">—</div>}
          </div>
        )}

        <div className="flow__legend">
          <span className="flow__legend-title">回溯</span>
          {BACKTRACKS.map((b, i) => (
            <span key={i} className="flow__legend-item">
              <b>{labelOf(b.from)}</b>
              <span className="flow__legend-arrow">↶</span>
              <b>{labelOf(b.to)}</b>
              <i>{b.note}</i>
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
