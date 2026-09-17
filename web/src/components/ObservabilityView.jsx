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

      <section className="stats-section">
        <h3>系统概览</h3>
        <div className="obs-cards">
          <MetricCard label="SLO 可用性" value={fmtPct(s.slo_availability)} tone={s.slo_availability != null && s.slo_availability < 0.99 ? 'warn' : 'ok'} />
          <MetricCard label="错误预算剩余" value={fmtPct(s.error_budget_remaining)} />
          <MetricCard label="任务成功率" value={fmtPct(s.task_success_rate)} />
          <MetricCard label="任务失败率" value={fmtPct(s.task_failure_rate)} tone={s.task_failure_rate != null && s.task_failure_rate > 0.05 ? 'warn' : undefined} />
          <MetricCard label="CPU" value={s.cpu_percent != null ? s.cpu_percent + '%' : '—'} />
          <MetricCard label="内存" value={s.memory_percent != null ? s.memory_percent + '%' : '—'} />
          <MetricCard label="DB 池使用" value={s.db_pool_utilization != null ? (s.db_pool_utilization * 100).toFixed(0) + '%' : '—'} />
          <MetricCard label="活跃线程" value={fmtNum(s.active_threads)} />
          <MetricCard label="未确认洞察" value={fmtNum(s.insights_unacknowledged)} />
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
          : <div className="obs-table">
              <div className="obs-row obs-row--head"><span>时间</span><span>可用性</span><span>成功率</span><span>失败率</span><span>延迟(ms)</span><span>CPU</span><span>内存</span></div>
              {sloSnaps.slice(-24).map((s2, i) => (
                <div key={i} className="obs-row obs-slo">
                  <span className="mono">{new Date(s2.ts * 1000).toLocaleTimeString()}</span>
                  <span className="mono">{fmtPct(s2.availability)}</span>
                  <span className="mono">{fmtPct(s2.success_rate)}</span>
                  <span className="mono">{fmtPct(s2.failure_rate)}</span>
                  <span className="mono">{s2.latency_avg_ms != null ? s2.latency_avg_ms.toFixed(0) : '—'}</span>
                  <span className="mono">{s2.cpu_percent != null ? s2.cpu_percent.toFixed(1) : '—'}</span>
                  <span className="mono">{s2.memory_percent != null ? s2.memory_percent.toFixed(1) : '—'}</span>
                </div>
              ))}
            </div>}
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
