import { useEffect, useState, useCallback } from 'react'
import { api } from '../api'

function fmtPct(n) {
  if (n === null || n === undefined) return '—'
  return (n * 100).toFixed(1) + '%'
}
function fmtNum(n) {
  if (n === null || n === undefined) return '—'
  return n
}

function BigMetric({ label, value, tone }) {
  return (
    <div className={`obs-big${tone ? ' obs-big--' + tone : ''}`}>
      <span className="obs-big__value mono">{value}</span>
      <span className="obs-big__label">{label}</span>
    </div>
  )
}

function SloChart({ snaps }) {
  if (!snaps || snaps.length === 0) return null

  const data = snaps.slice(-24)
  const W = 600
  const H = 160
  const PAD = { top: 16, right: 16, bottom: 24, left: 44 }
  const iw = W - PAD.left - PAD.right
  const ih = H - PAD.top - PAD.bottom
  const n = data.length

  const x = (i) => PAD.left + (n === 1 ? iw / 2 : (i / (n - 1)) * iw)
  const y = (v) => PAD.top + ih - ((v ?? 0)) * ih

  const line = (key) =>
    data.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join(' ')

  return (
    <div className="obs-slo-chart">
      <svg viewBox={`0 0 ${W} ${H}`} className="obs-slo-chart__svg">
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(f)} y2={y(f)} stroke="var(--line)" strokeWidth="0.5" />
            <text x={PAD.left - 6} y={y(f) + 4} textAnchor="end" fontSize="9" fill="var(--ink-faint)">{(f * 100).toFixed(0)}%</text>
          </g>
        ))}
        {data.map((d, i) => (
          <g key={i}>
            <rect x={x(i) - 10} y={PAD.top} width="20" height={ih} fill="transparent">
              <title>{`${new Date(d.ts * 1000).toLocaleTimeString()}: 可用性 ${fmtPct(d.availability)} · 成功率 ${fmtPct(d.success_rate)}`}</title>
            </rect>
            {i % Math.ceil(n / 6) === 0 && (
              <text x={x(i)} y={H - 8} textAnchor="middle" fontSize="9" fill="var(--ink-faint)">
                {new Date(d.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </text>
            )}
          </g>
        ))}
        <path d={line('availability')} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <path d={line('success_rate')} fill="none" stroke="rgb(34,197,94)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        {data.map((d, i) => (
          <g key={i}>
            <circle cx={x(i)} cy={y(d.availability)} r="2.5" fill="var(--accent)" stroke="var(--bg)" strokeWidth="1" />
            <circle cx={x(i)} cy={y(d.success_rate)} r="2.5" fill="rgb(34,197,94)" stroke="var(--bg)" strokeWidth="1" />
          </g>
        ))}
      </svg>
      <div className="obs-slo-chart__legend">
        <span><i style={{ background: 'var(--accent)' }} />可用性</span>
        <span><i style={{ background: 'rgb(34,197,94)' }} />成功率</span>
      </div>
    </div>
  )
}

function MetricCard({ label, value, tone }) {
  return (
    <div className={`obs-card${tone ? ' obs-card--' + tone : ''}`}>
      <div className="obs-card__label">{label}</div>
      <div className="obs-card__value mono">{value}</div>
    </div>
  )
}

export default function ObservabilityView({ onOpenTask }) {
  const [summary, setSummary] = useState(null)
  const [alerts, setAlerts] = useState(null)
  const [traces, setTraces] = useState(null)
  const [slo, setSlo] = useState(null)
  const [insights, setInsights] = useState(null)
  const [mioIns, setMioIns] = useState(null)
  const [proc, setProc] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([
      api.observabilitySummary().then(setSummary).catch(() => setSummary(null)),
      api.listAlerts().then(setAlerts).catch(() => setAlerts({ alerts: [], active_count: 0 })),
      api.taskTraces(50).then(setTraces).catch(() => setTraces({ traces: [] })),
      api.sloHistory(24).then(setSlo).catch(() => setSlo({ snapshots: [] })),
      api.listInsights(20).then(setInsights).catch(() => setInsights([])),
      api.mioInsight(20).then(setMioIns).catch(() => setMioIns(null)),
      api.processInfo().then(setProc).catch(() => setProc(null)),
    ]).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) {
    return (
      <div className="stats-view">
        <div className="stats-header"><h2>观测台</h2></div>
        <div className="stats-loading">加载中…</div>
      </div>
    )
  }

  const s = summary || {}
  const alertList = (alerts && alerts.alerts) || []
  const activeCount = (alerts && alerts.active_count) || 0
  const traceList = (traces && traces.traces) || []
  const sloSnaps = (slo && slo.snapshots) || []
  const insightList = insights || []

  return (
    <div className="stats-view obs-view">
      <header className="stats-header">
        <h2>观测台</h2>
        <button className="obs-refresh" onClick={load}>刷新</button>
      </header>

      <div className="obs-big-row">
        <BigMetric label="SLO 可用性" value={fmtPct(s.slo_availability)} tone={s.slo_availability != null && s.slo_availability < 0.99 ? 'warn' : 'ok'} />
        <BigMetric label="任务成功率" value={fmtPct(s.task_success_rate)} tone={s.task_success_rate != null && s.task_success_rate < 0.95 ? 'warn' : 'ok'} />
        <BigMetric label="活跃告警" value={activeCount} tone={activeCount > 0 ? 'danger' : 'ok'} />
        <BigMetric label="未确认洞察" value={fmtNum(s.insights_unacknowledged)} tone={s.insights_unacknowledged > 0 ? 'warn' : undefined} />
      </div>

      <section className="stats-section">
        <h3>系统概览</h3>
        <div className="obs-cards">
          <MetricCard label="错误预算剩余" value={fmtPct(s.error_budget_remaining)} />
          <MetricCard label="任务失败率" value={fmtPct(s.task_failure_rate)} tone={s.task_failure_rate != null && s.task_failure_rate > 0.05 ? 'warn' : undefined} />
          <MetricCard label="CPU" value={s.cpu_percent != null ? s.cpu_percent + '%' : '—'} />
          <MetricCard label="内存" value={s.memory_percent != null ? s.memory_percent + '%' : '—'} />
          <MetricCard label="DB 池使用" value={s.db_pool_utilization != null ? (s.db_pool_utilization * 100).toFixed(0) + '%' : '—'} />
          <MetricCard label="活跃线程" value={fmtNum(s.active_threads)} />
          <MetricCard label="24h 审计事件" value={fmtNum(s.audit_events_24h)} />
        </div>
      </section>

      <div className="obs-cols">
        <section className="stats-section obs-col">
          <h3>活动告警 · {activeCount}</h3>
          {alertList.length === 0
            ? <div className="obs-empty">无告警</div>
            : <ul className="obs-list">
                {alertList.map((a, i) => (
                  <li key={i} className={`obs-alert obs-alert--${a.active ? 'active' : 'resolved'} obs-sev-${a.severity}`}>
                    <span className="obs-alert__name mono">{a.name}</span>
                    <span className="obs-alert__msg">{a.message}</span>
                  </li>
                ))}
              </ul>}
        </section>

        <section className="stats-section obs-col">
          <h3>智能洞察</h3>
          {insightList.length === 0
            ? <div className="obs-empty">暂无</div>
            : <ul className="obs-list">
                {insightList.map((it) => (
                  <li key={it.id} className={`obs-insight obs-sev-${it.severity || 'info'}${it.acknowledged ? ' is-ack' : ''}`}>
                    <span className="obs-insight__title">{it.title || it.kind}</span>
                    <span className="obs-insight__kind mono">{it.kind}</span>
                  </li>
                ))}
              </ul>}
        </section>
      </div>

      <section className="stats-section">
        <h3>
          洞察 · 来源 Mio Runtime
          {mioIns?.available && mioIns.status
            ? `（共 ${mioIns.status.total ?? 0} · 未确认 ${mioIns.status.unreported ?? 0} · 高价值 ${mioIns.status.highValue ?? 0}）`
            : ''}
        </h3>
        {!mioIns || !mioIns.available
          ? <div className="obs-empty">Mio Runtime 不可用（未检测到 MIO_HOME）</div>
          : (mioIns.items && mioIns.items.length > 0
              ? <ul className="obs-list">
                  {mioIns.items.map((it, i) => (
                    <li key={it.id || i} className={`obs-insight obs-sev-${it.severity || 'info'}`}>
                      <span className="obs-insight__title">{it.title || it.kind || '(无标题)'}</span>
                      <span className="obs-insight__kind mono">{it.detector || it.kind || ''}</span>
                      {it.score != null && <span className="obs-insight__kind mono">score {it.score}</span>}
                    </li>
                  ))}
                </ul>
              : <div className="obs-empty">
                  Mio 尚未产出洞察 —— 生成走 MCP <span className="mono">mio.insight.generate</span>
                  （本机 CLI <span className="mono">mio insight generate</span> 有上游接线 bug：漏传 chatJson）
                </div>)}
      </section>

      <section className="stats-section">
        <h3>任务链路 · 最近 {traceList.length}</h3>
        {traceList.length === 0
          ? <div className="obs-empty">暂无已完成 / 失败 / 取消的任务链路</div>
          : <div className="obs-table">
              <div className="obs-row obs-row--head">
                <span>任务</span><span>状态</span><span>阶段</span><span>耗时(s)</span><span>重试</span><span>事件</span>
              </div>
              {traceList.map((t) => (
                <div key={t.task_id} className="obs-row obs-row--click" onClick={() => onOpenTask && onOpenTask(t.task_id)}>
                  <span className="obs-row__title" title={t.title}>{t.title || t.task_id}</span>
                  <span className={`obs-tag obs-tag--${t.state}`}>{t.state}</span>
                  <span>{t.stage || '—'}</span>
                  <span className="mono">{t.duration_seconds != null ? t.duration_seconds : '—'}</span>
                  <span className="mono">{t.retry_count || 0}</span>
                  <span className="mono">{t.event_count || 0}</span>
                </div>
              ))}
            </div>}
      </section>

      <section className="stats-section">
        <h3>SLO 历史 · 近 24h {sloSnaps.length} 点</h3>
        {sloSnaps.length === 0
          ? <div className="obs-empty">暂无快照（每 5 分钟落库）</div>
          : <SloChart snaps={sloSnaps} />}
      </section>

      {proc && (
        <section className="stats-section">
          <h3>进程</h3>
          <div className="obs-cards">
            <MetricCard label="PID" value={proc.pid} />
            <MetricCard label="内存 RSS(MB)" value={proc.memory_rss_mb} />
            <MetricCard label="线程" value={proc.threads} />
            <MetricCard label="运行时长(s)" value={Math.round(proc.uptime_seconds)} />
          </div>
        </section>
      )}
    </div>
  )
}
