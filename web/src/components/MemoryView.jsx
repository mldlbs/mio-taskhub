import { useEffect, useState, useCallback } from 'react'
import { api } from '../api'
import { Skeleton, SkeletonField, SkeletonList } from './Skeleton'

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
      <span className="memory-field__value" style={{ color: TONE[tone] || TONE.muted }}>
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

function ToolBar({ name, count }) {
  return (
    <div className="memory-toolbar">
      <div className="memory-toolbar__head">
        <span className="memory-toolbar__name">{name}</span>
        <span className="memory-toolbar__total mono">{count}</span>
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

function QueryPanel() {
  const [keyword, setKeyword] = useState('')
  const [kind, setKind] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)

  const doQuery = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (keyword) params.set('keyword', keyword)
      if (kind) params.set('kind', kind)
      params.set('limit', '50')
      const r = await fetch(`/api/memory/query?${params}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setResults(await r.json())
    } catch (e) {
      setResults({ error: e.message })
    } finally {
      setLoading(false)
    }
  }, [keyword, kind])

  return (
    <div className="memory-query">
      <div className="memory-query__bar">
        <input
          className="memory-query__input"
          placeholder="搜索关键词…"
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doQuery()}
        />
        <select className="memory-query__select" value={kind} onChange={e => setKind(e.target.value)}>
          <option value="">全部类型</option>
          <option value="decision">决策 (decision)</option>
          <option value="context">上下文 (context)</option>
          <option value="problem">问题 (problem)</option>
          <option value="note">笔记 (note)</option>
          <option value="experience">经验 (experience)</option>
        </select>
        <button className="memory-btn" onClick={doQuery} disabled={loading}>
          {loading ? '查询中…' : '查询'}
        </button>
      </div>
      {results && (
        <div className="memory-query__results">
          {results.error ? (
            <div className="memory-error" role="alert">▲ {results.error}</div>
          ) : results.total === 0 ? (
            <div className="memory-empty">无匹配结果</div>
          ) : (
            <>
              <div className="memory-query__count mono">{results.total} 条结果</div>
              {results.entities.map((ent, i) => (
                <div key={i} className="memory-entity">
                  <div className="memory-entity__head">
                    <span className="memory-entity__name">{ent.name}</span>
                    <span className="memory-entity__type mono">{ent.entityType}</span>
                    {ent._score > 0 && <span className="memory-entity__score mono">score:{ent._score}</span>}
                  </div>
                  <div className="memory-entity__obs">
                    {ent.observations.map((obs, j) => (
                      <div key={j} className="memory-entity__obs-line">{obs}</div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
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
    const t = setInterval(reload, 5000)
    return () => clearInterval(t)
  }, [reload])

  useEffect(() => {
    if (!liveEvent) return
    const ev = liveEvent.event || liveEvent
    if (typeof ev?.type === 'string' && ev.type.startsWith('memory_')) {
      setEvents(prev => [ev, ...prev].slice(0, 30))
    }
  }, [liveEvent])

  // 解析 metrics
  const callsTotal = metrics?.taskhub_memory_calls_total
  const perTool = {}
  if (Array.isArray(callsTotal)) {
    for (const row of callsTotal) {
      const tool = row.label === 'tool' ? row.value : null
      const outcome = row.label === 'outcome' ? row.value : null
      if (tool && outcome) {
        if (!perTool[tool]) perTool[tool] = { ok: 0, error: 0 }
        perTool[tool][outcome] = row.count
      }
    }
  }
  if (health?.mcp?.per_tool_5m) {
    for (const [tool, count] of Object.entries(health.mcp.per_tool_5m)) {
      if (!perTool[tool]) perTool[tool] = { ok: 0, error: 0 }
      perTool[tool].ok = count
    }
  }

  const mcp = health?.mcp || {}
  const storeAlive = mcp.available === true

  return (
    <div className="memory-view">
      <div className="memory-header">
        <h2>Memory Store</h2>
        <div className="memory-header__sub">
          {health === null ? <Skeleton variant="circle" width="8px" height="8px" /> : <StatusDot ok={storeAlive} />}
          <span>{health === null ? '加载中…' : `${mcp.store_type || 'jsonl'} · ${mcp.entity_count ?? 0} entities`}</span>
          <button className="memory-btn" onClick={reload} disabled={loading}>
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {err && <div className="memory-error" role="alert">▲ {err}</div>}

      <section className="memory-section">
        <h3>存储状态</h3>
        {health === null ? <SkeletonField count={4} /> : (
          <div className="memory-grid">
            <HealthField label="store_type" value={mcp.store_type} tone="ok" />
            <HealthField label="entity_count" value={mcp.entity_count ?? 0} tone="ok" />
            <HealthField label="relation_count" value={mcp.relation_count ?? 0} tone="ok" />
            <HealthField label="data_file" value={mcp.data_file || '—'} tone="muted" />
          </div>
        )}
      </section>

      <section className="memory-section">
        <h3>查询记忆</h3>
        <QueryPanel />
      </section>

      {Object.keys(perTool).length > 0 && (
        <section className="memory-section">
          <h3>调用统计</h3>
          <div className="memory-tools">
            {Object.entries(perTool).map(([tool, stats]) => (
              <ToolBar key={tool} name={tool} count={stats.ok + stats.error} />
            ))}
          </div>
        </section>
      )}

      <section className="memory-section">
        <h3>
          实时事件
          <span className="memory-section__hint mono">WS · {events.length} 条</span>
        </h3>
        {events.length === 0 ? (
          health === null
            ? <SkeletonList count={5} />
            : <div className="memory-empty">尚无 memory_* 事件。可通过 /api/memory/record 等端点写入。</div>
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
