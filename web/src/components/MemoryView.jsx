import { useEffect, useState, useCallback } from 'react'
import { api } from '../api'

const TONE = {
  ok: 'var(--accent)',
  live: 'var(--accent)',
  warn: 'var(--warn)',
  danger: 'var(--danger)',
  muted: 'var(--ink-faint)',
}

function HealthField({ label, value, tone = 'muted', mono = true }) {
  const display = value === null || value === undefined ? '—' : String(value)
  return (
    <div className="memory-field">
      <span className="memory-field__label">{label}</span>
      <span className="memory-field__value" style={{ color: TONE[tone] || TONE.muted }} {...(mono ? {} : {})}>
        <span style={{ fontFamily: mono ? 'var(--font-mono)' : 'inherit' }}>{display}</span>
      </span>
    </div>
  )
}

function StatusDot({ ok }) {
  return (
    <span className={`memory-dot${ok ? ' is-live' : ' is-down'}`} aria-label={ok ? '正常' : '不可达'} />
  )
}

function ToolBar({ name, ok, timeout, rpc, unavailable, total_5m }) {
  const total = ok + timeout + rpc + unavailable
  const ok_pct = total > 0 ? (ok / total) * 100 : 0
  const err_pct = total > 0 ? ((timeout + rpc + unavailable) / total) * 100 : 0
  return (
    <div className="memory-toolbar">
      <div className="memory-toolbar__head">
        <span className="memory-toolbar__name">{name}</span>
        <span className="memory-toolbar__total mono">{total}</span>
      </div>
      <div className="memory-toolbar__track" title={`ok ${ok} / err ${timeout + rpc + unavailable} / total ${total}`}>
        <div className="memory-toolbar__ok" style={{ width: `${ok_pct}%` }} />
        <div className="memory-toolbar__err" style={{ width: `${err_pct}%`, left: `${ok_pct}%` }} />
      </div>
      <div className="memory-toolbar__break mono">
        <span className="ok" style={{ color: 'var(--accent)' }}>{ok}</span>
        <span className="err" style={{ color: 'var(--danger)' }}>{timeout + rpc + unavailable}</span>
        <span className="hint">{total_5m > 0 ? `${total_5m}/5m` : ''}</span>
      </div>
    </div>
  )
}

function EventRow({ ev }) {
  const tone =
    ev.type === 'memory_record' ? 'live' :
    ev.type === 'memory_observer_ingest' ? 'warn' :
    ev.type === 'memory_experience_reuse' ? 'ok' : 'muted'
  return (
    <div className="memory-event" data-type={ev.type}>
      <div className="memory-event__row">
        <span className="memory-event__type" style={{ color: TONE[tone] }}>{ev.type}</span>
        <span className="memory-event__seq mono">#{ev.seq}</span>
        <span className="memory-event__time mono">{new Date(ev.at).toLocaleTimeString()}</span>
      </div>
      <div className="memory-event__entity mono">{ev.entity_id}</div>
      {ev.payload && (
        <div className="memory-event__payload mono">
          {ev.payload.tool || ''} {ev.payload.params ? JSON.stringify(ev.payload.params).slice(0, 80) : ''}
        </div>
      )}
    </div>
  )
}

export default function MemoryView({ liveEvent }) {
  const [health, setHealth] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setErr(null)
    try {
      const [h, m, e] = await Promise.allSettled([
        api.memoryHealth(),
        api.metrics(),
        api.memoryEvents(200),
      ])
      if (h.status === 'fulfilled') setHealth(h.value)
      else setErr('health: ' + h.reason.message)
      if (m.status === 'fulfilled') setMetrics(m.value)
      if (e.status === 'fulfilled') setEvents(e.value)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    reload()
    const t = setInterval(reload, 5000)  // 5s 轮询（health/metrics），事件走 WS
    return () => clearInterval(t)
  }, [reload])

  // 实时事件：WS 推过来时插入列表头部
  useEffect(() => {
    if (!liveEvent) return
    const ev = liveEvent.event || liveEvent
    if (typeof ev?.type === 'string' && ev.type.startsWith('memory_')) {
      setEvents(prev => [ev, ...prev].slice(0, 30))
    }
  }, [liveEvent])

  // 解析 metrics 中的 taskhub_memory_calls_total
  const callsTotal = metrics && metrics.taskhub_memory_calls_total
  const perTool = {}
  if (Array.isArray(callsTotal)) {
    for (const row of callsTotal) {
      const tool = row.label === 'tool' ? row.value : null
      const outcome = row.label === 'outcome' ? row.value : null
      if (tool && outcome) {
        if (!perTool[tool]) perTool[tool] = { ok: 0, timeout: 0, rpc_error: 0, unavailable: 0, total_5m: 0 }
        perTool[tool][outcome] = row.count
      }
    }
  }
  // 也合并 health.per_tool_5m
  if (health?.mcp?.per_tool_5m) {
    for (const [tool, count] of Object.entries(health.mcp.per_tool_5m)) {
      if (!perTool[tool]) perTool[tool] = { ok: 0, timeout: 0, rpc_error: 0, unavailable: 0, total_5m: 0 }
      perTool[tool].total_5m = count
    }
  }

  const mcp = health?.mcp || {}
  const mcpAlive = mcp.proc_alive === true

  return (
    <div className="memory-view">
      <div className="memory-header">
        <h2>Memory Gateway</h2>
        <div className="memory-header__sub">
          <StatusDot ok={mcpAlive} />
          <span>{mcpAlive ? 'MCP alive' : 'MCP down'}</span>
          <button className="memory-btn" onClick={reload} disabled={loading}>
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {err && <div className="memory-error" role="alert">▲ {err}</div>}

      <section className="memory-section">
        <h3>MCP 客户端</h3>
        <div className="memory-grid">
          <HealthField label="available" value={mcp.available === undefined ? '—' : String(mcp.available)} tone={mcpAlive ? 'ok' : 'danger'} />
          <HealthField label="proc_alive" value={mcp.proc_alive === undefined ? '—' : String(mcp.proc_alive)} tone={mcp.proc_alive ? 'ok' : 'danger'} />
          <HealthField label="respawn_count" value={mcp.respawn_count ?? 0} tone={mcp.respawn_count > 0 ? 'warn' : 'muted'} />
          <HealthField label="last_call_ms" value={mcp.last_call_ms ?? '—'} tone="muted" />
          <HealthField label="last_error" value={mcp.last_error || '—'} tone={mcp.last_error ? 'danger' : 'ok'} />
          <HealthField label="calls_total_5m" value={mcp.calls_total_5m ?? 0} tone="muted" />
        </div>
      </section>

      <section className="memory-section">
        <h3>工具调用（taskhub_memory_calls_total）</h3>
        {Object.keys(perTool).length === 0 ? (
          <div className="memory-empty">暂无调用数据</div>
        ) : (
          <div className="memory-tools">
            {Object.entries(perTool).map(([tool, stats]) => (
              <ToolBar
                key={tool}
                name={tool}
                ok={stats.ok}
                timeout={stats.timeout}
                rpc={stats.rpc_error}
                unavailable={stats.unavailable}
                total_5m={stats.total_5m}
              />
            ))}
          </div>
        )}
      </section>

      <section className="memory-section">
        <h3>
          实时事件
          <span className="memory-section__hint mono">WS · {events.length} 条</span>
        </h3>
        {events.length === 0 ? (
          <div className="memory-empty">尚无 memory_* 事件。可通过 /api/memory/record 等端点写入。</div>
        ) : (
          <div className="memory-events">
            {events.map(ev => (
              <EventRow key={ev.seq} ev={ev} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
