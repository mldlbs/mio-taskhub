import { useEffect, useState } from 'react'
import { api } from '../api'
import { COMPOSITE_LABELS, LANES, STAGES, parseUtc } from '../constants'
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

function MetricCard({ label, value, tone }) {
  return (
    <div className={`stats-metric stats-metric--${tone || 'dim'}`}>
      <span className="stats-metric__value">{value}</span>
      <span className="stats-metric__label">{label}</span>
    </div>
  )
}

function TrendChart({ data }) {
  if (!data || data.length === 0) return null

  const W = 600
  const H = 180
  const PAD = { top: 20, right: 20, bottom: 30, left: 40 }
  const iw = W - PAD.left - PAD.right
  const ih = H - PAD.top - PAD.bottom

  const maxVal = Math.max(1, ...data.flatMap(d => [d.created, d.completed, d.failed]))
  const n = data.length

  const x = (i) => PAD.left + (n === 1 ? iw / 2 : (i / (n - 1)) * iw)
  const y = (v) => PAD.top + ih - (v / maxVal) * ih

  const line = (key) =>
    data.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join(' ')

  const colors = { created: 'var(--accent)', completed: 'rgb(34,197,94)', failed: 'rgb(255,100,100)' }

  return (
    <div className="stats-trend">
      <svg viewBox={`0 0 ${W} ${H}`} className="stats-trend__svg">
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(maxVal * f)} y2={y(maxVal * f)} stroke="var(--line)" strokeWidth="0.5" />
            <text x={PAD.left - 6} y={y(maxVal * f) + 4} textAnchor="end" fontSize="9" fill="var(--ink-faint)">{Math.round(maxVal * f)}</text>
          </g>
        ))}
        {data.map((d, i) => (
          <g key={i}>
            <rect x={x(i) - 12} y={PAD.top} width="24" height={ih} fill="transparent">
              <title>{`${d.date}: 创建 ${d.created} · 完成 ${d.completed} · 失败 ${d.failed}`}</title>
            </rect>
            <text x={x(i)} y={H - 10} textAnchor="middle" fontSize="9" fill="var(--ink-faint)">{d.date.slice(5)}</text>
          </g>
        ))}
        {['created', 'completed', 'failed'].map((key) => (
          <path key={key} d={line(key)} fill="none" stroke={colors[key]} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        ))}
        {data.map((d, i) =>
          ['created', 'completed', 'failed'].map((key) => (
            <circle key={`${key}-${i}`} cx={x(i)} cy={y(d[key])} r="3" fill={colors[key]} stroke="var(--bg)" strokeWidth="1.5" />
          ))
        )}
      </svg>
      <div className="stats-trend__legend">
        <span><i style={{ background: colors.created }} />创建</span>
        <span><i style={{ background: colors.completed }} />完成</span>
        <span><i style={{ background: colors.failed }} />失败</span>
      </div>
    </div>
  )
}

export default function StatsView() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [metrics, setMetrics] = useState(null)
  const [trendData, setTrendData] = useState(null)

  useEffect(() => {
    api.statsOverview().then(setData).catch(() => setData(null)).finally(() => setLoading(false))
    api.metrics().then(setMetrics).catch(() => {})
    api.listTasks({ cancelled: 'true' }).then((tasks) => {
      const days = []
      for (let i = 6; i >= 0; i--) {
        const d = new Date()
        d.setDate(d.getDate() - i)
        days.push(d.toISOString().slice(0, 10))
      }
      const agg = days.map((date) => ({ date, created: 0, completed: 0, failed: 0 }))
      const idx = Object.fromEntries(days.map((d, i) => [d, i]))
      for (const t of tasks) {
        const cd = t.created_at ? parseUtc(t.created_at)?.toISOString().slice(0, 10) : null
        if (cd && cd in idx) agg[idx[cd]].created++
        const td = t.updated_at ? parseUtc(t.updated_at)?.toISOString().slice(0, 10) : null
        if (td && td in idx) {
          if (t.state === 'completed') agg[idx[td]].completed++
          if (t.state === 'failed') agg[idx[td]].failed++
        }
      }
      setTrendData(agg)
    }).catch(() => {})
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

  const successRate = total_tasks > 0
    ? Math.round(((by_state['completed'] || 0) / total_tasks) * 100)
    : 0
  const agentsOnline = metrics?.taskhub_agents_online?.find(a => a.label === 'online')?.count ?? 0

  return (
    <div className="stats-view">
      <header className="stats-header">
        <h2>统计概览</h2>
        <span className="stats-header__total mono">{total_tasks} 任务 · {task_events_count} 事件</span>
      </header>

      <div className="stats-metrics">
        <MetricCard label="总任务" value={total_tasks} tone="live" />
        <MetricCard label="事件" value={task_events_count} tone="dim" />
        <MetricCard label="Agent 在线" value={agentsOnline} tone="ok" />
        <MetricCard label="成功率" value={`${successRate}%`} tone={successRate >= 80 ? 'ok' : successRate >= 50 ? 'warn' : 'danger'} />
      </div>

      <section className="stats-section">
        <h3>7 日趋势</h3>
        <TrendChart data={trendData} />
      </section>

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
