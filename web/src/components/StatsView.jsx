import { useEffect, useState } from 'react'
import { api } from '../api'
import { COMPOSITE_LABELS, LANES, STAGES } from '../constants'
import { Skeleton } from './Skeleton'

const TONE_COLORS = {
  live: 'var(--accent)',
  ok: 'rgb(34,197,94)',
  'ok-soft': 'rgb(120,180,120)',
  warn: 'var(--warn)',
  danger: 'rgb(255,100,100)',
  muted: 'var(--ink-faint)',
  dim: 'var(--ink-faint)',
}

function Bar({ label, value, max, tone }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0
  return (
    <div className="stats-bar">
      <span className="stats-bar__label">{label}</span>
      <div className="stats-bar__track">
        <div className="stats-bar__fill" style={{ width: `${pct}%`, background: TONE_COLORS[tone] || 'var(--ink-faint)' }} />
      </div>
      <span className="stats-bar__value mono">{value}</span>
    </div>
  )
}

export default function StatsView() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [metrics, setMetrics] = useState(null)

  useEffect(() => {
    api.statsOverview().then(setData).catch(() => setData(null)).finally(() => setLoading(false))
    api.metrics().then(setMetrics).catch(() => {})
  }, [])

  if (loading) return (
    <div className="stats-view">
      <div className="stats-header">
        <h2>统计</h2>
        <span className="stats-header__total">加载中…</span>
      </div>
      <div className="stats-bars">
        <Skeleton variant="box" height="60px" />
        <Skeleton variant="box" height="60px" />
        <Skeleton variant="box" height="60px" />
      </div>
    </div>
  )
  if (!data) return <div className="stats-loading">无法加载统计</div>

  const { composite_counts, by_state, by_stage, event_by_type, total_tasks, task_events_count } = data
  const maxComposite = Math.max(1, ...Object.values(composite_counts))
  const maxState = Math.max(1, ...Object.values(by_state))
  const maxStage = Math.max(1, ...Object.values(by_stage))
  const maxEvent = Math.max(1, ...Object.values(event_by_type))

  const compositeEntries = Object.entries(composite_counts)
    .map(([key, count]) => {
      const [state, stage] = key.split(',')
      const meta = COMPOSITE_LABELS[key] || { label: key, tone: 'dim' }
      return { key, state, stage, count, ...meta }
    })
    .sort((a, b) => b.count - a.count)

  return (
    <div className="stats-view">
      <header className="stats-header">
        <h2>统计概览</h2>
        <span className="stats-header__total mono">{total_tasks} 任务 · {task_events_count} 事件</span>
      </header>

      <section className="stats-section">
        <h3>状态分布</h3>
        <div className="stats-bars">
          {LANES.map(l => (
            <Bar key={l.id} label={l.label} value={by_state[l.id] || 0} max={maxState} tone={l.tone} />
          ))}
        </div>
      </section>

      <section className="stats-section">
        <h3>阶段分布</h3>
        <div className="stats-bars">
          {STAGES.map(s => (
            <Bar key={s.id} label={s.label} value={by_stage[s.id] || 0} max={maxStage} tone={s.tone} />
          ))}
        </div>
      </section>

      <section className="stats-section">
        <h3>复合状态 · {compositeEntries.length} 种</h3>
        <div className="stats-bars">
          {compositeEntries.map(e => (
            <Bar key={e.key} label={e.label} value={e.count} max={maxComposite} tone={e.tone} />
          ))}
        </div>
      </section>

      {Object.keys(event_by_type).length > 0 && (
        <section className="stats-section">
          <h3>事件类型 · {task_events_count} 条</h3>
          <div className="stats-bars">
            {Object.entries(event_by_type).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
              <Bar key={type} label={type} value={count} max={maxEvent} tone="dim" />
            ))}
          </div>
        </section>
      )}

      {metrics && (
        <section className="stats-section">
          <h3>系统健康</h3>
          <div className="stats-bars">
            <Bar label="运行时间 (s)" value={Math.round(metrics.taskhub_uptime_seconds || 0)} max={86400} tone="live" />
            {(metrics.taskhub_agents_online || []).map(a => (
              <Bar key={a.label} label={`Agent: ${a.label}`} value={a.count} max={10} tone={a.label === 'online' ? 'ok' : 'muted'} />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
