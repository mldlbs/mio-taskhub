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
                      <div className="gantt__float" style={{ left: (ef - es) * scale, width: Math.max(ls - ef, 0) * scale }} />
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
