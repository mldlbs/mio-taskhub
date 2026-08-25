import { useState, useEffect } from 'react'
import { fmtDur, prio } from '../constants'
import { api } from '../api'

const WINDOW_MIN = 540
const WINDOW_START_MIN = 22 * 60
const DEFAULT_START = '22:00'
const DEFAULT_END = '07:00'

const hm2min = (s) => {
  const [h, m] = s.split(':').map(Number)
  return h * 60 + m
}

const min2hm = (min) => {
  const h = Math.floor(min / 60) % 24
  const m = min % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

const HOURS = ['22:00', '23:00', '00:00', '01:00', '02:00', '03:00', '04:00', '05:00', '06:00', '07:00']

export default function PlanView({ onSchedule }) {
  const [plan, setPlan] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [projects, setProjects] = useState([])
  const [selectedProject, setSelectedProject] = useState('')

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => {})
  }, [])

  const generate = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.nightPlan(DEFAULT_START, DEFAULT_END, selectedProject || null)
      const items = (data.items ?? []).map((t) => {
        const off = hm2min(t.scheduled_start) - WINDOW_START_MIN
        return {
          ...t,
          id: t.task_id ?? t.id,
          start: off < 0 ? off + 1440 : off,
          dur: Math.min(WINDOW_MIN, Math.max(5, t.est_duration_min ?? 30)),
        }
      })
      const lastEnd = items.length ? Math.min(WINDOW_MIN, Math.max(...items.map((i) => i.start + i.dur))) : 0
      setPlan({
        items,
        fitted: items.length,
        total: items.length,
        filled: lastEnd,
        overflow: data.has_overflow ? '有' : 0,
        project: selectedProject,
      })
      onSchedule && onSchedule({ items, fitted: items.length, total: items.length, filled: lastEnd, overflow: data.has_overflow ? '有' : 0 })
    } catch {
      setError('排期接口调用失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  const occupancy = plan ? Math.round((plan.filled / WINDOW_MIN) * 100) : 0

  return (
    <div className="np">
      <div className="np__head">
        <div>
          <h2>夜间计划 <span className="np__accent">NIGHT SHIFT</span></h2>
          <p className="np__sub">
            {DEFAULT_START} – {DEFAULT_END} · 按优先级排入窗口
            {plan?.project && <span> · <b style={{ color: 'var(--accent)' }}>{plan.project}</b></span>}
          </p>
        </div>
        <div className="np__actions">
          <select
            className="np__project-select"
            value={selectedProject}
            onChange={(e) => setSelectedProject(e.target.value)}
          >
            <option value="">全部项目</option>
            {projects.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <button className="btn btn--accent" onClick={generate} disabled={loading}>
            {loading ? '生成中…' : '⟳ 生成排期'}
          </button>
        </div>
      </div>

      {error && <div className="errorbar" role="alert"><span>▲</span><span>{error}</span></div>}

      {plan ? (
        <>
          {/* Stats */}
          <div className="np__stats">
            <div className="np-stat np-stat--primary">
              <div className="np-stat__icon">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 2v4M16 2v4M3 10h18"/></svg>
              </div>
              <div className="np-stat__num">{plan.fitted}<span className="np-stat__den"> / {plan.total}</span></div>
              <div className="np-stat__lbl">已排入</div>
            </div>
            <div className="np-stat">
              <div className="np-stat__bar"><div className="np-stat__bar-fill" style={{ width: `${occupancy}%` }} /></div>
              <div className="np-stat__num">{occupancy}<span className="np-stat__den">%</span></div>
              <div className="np-stat__lbl">窗口占用</div>
            </div>
            <div className={`np-stat ${plan.overflow ? 'np-stat--warn' : ''}`}>
              <div className="np-stat__num">{plan.overflow || '—'}</div>
              <div className="np-stat__lbl">溢出待排</div>
            </div>
            <div className="np-stat">
              <div className="np-stat__time">
                <span>{DEFAULT_START}</span>
                <span className="np-stat__sep">→</span>
                <span>{DEFAULT_END}</span>
              </div>
              <div className="np-stat__lbl">时间窗口</div>
            </div>
          </div>

          {/* Timeline header */}
          <div className="np-gantt">
            <div className="np-gantt__header">
              <div className="np-gantt__label-col">任务</div>
              <div className="np-gantt__time-col">
                {HOURS.map((h, i) => (
                  <span key={h} className="np-gantt__hour" style={{ left: `${(i / (HOURS.length - 1)) * 100}%` }}>
                    {h}
                  </span>
                ))}
                <div className="np-gantt__grid">
                  {HOURS.map((_, i) => (
                    <div key={i} className="np-gantt__gridline" style={{ left: `${(i / (HOURS.length - 1)) * 100}%` }} />
                  ))}
                </div>
              </div>
            </div>

            {/* Task rows */}
            <div className="np-gantt__body">
              {plan.items.map((t, i) => {
                const p = prio(t.priority)
                const left = (t.start / WINDOW_MIN) * 100
                const width = (t.dur / WINDOW_MIN) * 100
                return (
                  <div key={t.id} className="np-row" style={{ '--i': i }}>
                    <div className="np-row__label">
                      <span className={`np-row__prio np-row__prio--p${p.p}`}>{p.label}</span>
                      <span className="np-row__title" title={t.title}>{t.title}</span>
                    </div>
                    <div className="np-row__bar">
                      <div
                        className={`np-row__fill np-row__fill--p${p.p}`}
                        style={{ left: `${left}%`, width: `${Math.max(width, 2)}%` }}
                        title={`${t.title}\n${min2hm(t.start + WINDOW_START_MIN)} – ${min2hm(t.start + t.dur + WINDOW_START_MIN)} · ${fmtDur(t.dur)}`}
                      >
                        <span className="np-row__time">{fmtDur(t.dur)}</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Legend */}
          <div className="np__legend">
            {[{ p: 3, t: 'P3 紧急', c: 'accent' }, { p: 2, t: 'P2 高', c: 'warn' }, { p: 1, t: 'P1 中', c: 'dim' }, { p: 0, t: 'P0 低', c: 'faint' }].map(x => (
              <span key={x.p} className={`np-legend np-legend--${x.c}`}><i />{x.t}</span>
            ))}
          </div>
        </>
      ) : (
        <div className="np__empty">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <path d="M21 12.8A9 9 0 1111.2 3 7 7 0 0021 12.8z" />
          </svg>
          选择项目，点击「生成排期」预览今晚的执行序列
        </div>
      )}
    </div>
  )
}
